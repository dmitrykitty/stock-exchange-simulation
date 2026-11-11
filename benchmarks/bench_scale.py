"""Can the F6 network (18 assets, ~1000 investors, 502 sessions) be simulated in acceptable time?

Run: ``python benchmarks/bench_scale.py [--assets 3 10 18] [--sessions 20] [--backend python cpp]``

Part 1 drives the Gateway (K1) directly with an F6-like order flow: every investor follows ``--attention``
assets, wakes ``--wakes`` times per session and, like the F4 agents, first cancels its open orders and then
sends an order on each followed asset with probability ``--order-prob``; one market maker per asset re-quotes
``--maker-rate`` times per session. Resource invariants are checked every session. This measures the market
layer (gateway bookkeeping + matching) at full scale.

Part 2 runs the F4 single-asset simulation with ``--investors`` investors to measure the cost of one agent
decision including the event loop, signals, herding and logging. F6 code does not exist yet, so the F6 run
time is extrapolated from the two parts (see the printed estimate and docs/plans/phases/F3_exchange_engine.md).
"""

from __future__ import annotations

import argparse
import dataclasses
import tempfile
import time
from pathlib import Path

import numpy as np

from marketsim.market import Gateway, make_exchange
from marketsim.market.api import NO_PRICE, Side, Status, TimeInForce

TICK_PRICE = 10_000  # 100.00 USD in ticks


def market_layer(n_assets: int, backend: str, args, seed: int = 0) -> dict:
    """Simulated F6 order flow through the Gateway; returns throughput and book health."""
    rng = np.random.default_rng(seed)
    n_inv, n_agents = args.investors, args.investors + n_assets
    cash = np.full(n_agents, 10_000_000, dtype=np.int64)  # 100,000 USD each
    shares = np.zeros((n_agents, n_assets), dtype=np.int64)
    shares[:n_inv] = 100
    for a in range(n_assets):  # one market maker per asset, holding inventory only in its asset
        shares[n_inv + a, a] = 20_000
    gw = Gateway(make_exchange(backend, n_assets), cash, shares)
    attention = np.array([rng.choice(n_assets, min(args.attention, n_assets), replace=False) for _ in range(n_inv)],
                         dtype=object)
    open_orders: list[list[int]] = [[] for _ in range(n_agents)]
    mid = np.full(n_assets, TICK_PRICE, dtype=np.int64)
    counts = {"place": 0, "cancel": 0, "filled_orders": 0}
    empty_side = samples = 0

    def place(agent, asset, side, qty, limit, tif):
        report = gw.place(agent, asset, side, qty, limit, tif, 0.0)
        counts["place"] += 1
        if report.status not in (Status.REJECTED_FUNDS, Status.REJECTED_INVALID, Status.REJECTED_SELF_TRADE):
            if report.resting_qty > 0:
                open_orders[agent].append(report.order_id)
            if report.filled_qty > 0:
                counts["filled_orders"] += 1

    def cancel_all(agent):
        for order_id in open_orders[agent]:
            gw.cancel(agent, order_id, 0.0)
            counts["cancel"] += 1
        open_orders[agent].clear()

    start = time.perf_counter()
    for _session in range(args.sessions):
        # Wake-ups of one session in random order: investors and makers interleaved.
        events = np.concatenate([np.repeat(np.arange(n_inv), args.wakes),
                                 np.repeat(np.arange(n_inv, n_agents), args.maker_rate)])
        rng.shuffle(events)
        for agent in events:
            agent = int(agent)
            cancel_all(agent)
            if agent >= n_inv:  # market maker: two quotes around the current mid
                asset = agent - n_inv
                place(agent, asset, int(Side.BUY), 20, int(mid[asset]) - 5, int(TimeInForce.GTC))
                place(agent, asset, int(Side.SELL), 20, int(mid[asset]) + 5, int(TimeInForce.GTC))
                continue
            for asset in attention[agent]:
                if rng.random() >= args.order_prob:
                    continue
                side = int(Side.BUY) if rng.random() < 0.5 else int(Side.SELL)
                qty = int(rng.integers(1, 6))
                if rng.random() < 0.5:  # aggressive: IOC with a 1% protection price
                    limit = int(mid[asset] * (1.01 if side > 0 else 0.99))
                    place(agent, int(asset), side, qty, limit, int(TimeInForce.IOC))
                else:  # passive: join the best price of its side
                    top = gw.market().top(int(asset))
                    best = top.bid if side > 0 else top.ask
                    limit = int(best) if best != NO_PRICE else int(mid[asset]) - 5 * side
                    place(agent, int(asset), side, qty, limit, int(TimeInForce.GTC))
            for asset in attention[agent]:
                top = gw.market().top(int(asset))
                if top.bid != NO_PRICE and top.ask != NO_PRICE:
                    mid[asset] = (top.bid + top.ask) // 2
        gw.drain_fills()
        gw.check_invariants()
        for asset in range(n_assets):
            top = gw.market().top(asset)
            empty_side += top.bid == NO_PRICE or top.ask == NO_PRICE
            samples += 1
    elapsed = time.perf_counter() - start
    ops = counts["place"] + counts["cancel"]
    return {"assets": n_assets, "backend": backend, "seconds": elapsed, "orders": counts["place"],
            "cancels": counts["cancel"], "ops_per_s": ops / elapsed, "orders_per_session": counts["place"] / args.sessions,
            "cancels_per_session": counts["cancel"] / args.sessions,
            "seconds_per_session": elapsed / args.sessions, "empty_side_share": empty_side / samples}


def agent_layer(investors: int, sessions: int, backend: str) -> dict:
    """F4 single-asset simulation scaled to ``investors``; returns the cost of one investor decision."""
    from marketsim.experiments.config import expand, load_config
    from marketsim.sim.api import run
    from marketsim.sim.log_schema import load_run

    root = Path(__file__).resolve().parents[1]
    spec = next(s for s in expand(load_config(root / "configs/f4_e1.yaml"))
            if s.overrides == {"agents.herding.h": 0.3, "agents.concentration": 0.5})
    scale = investors / 80
    agents = dict(spec.config.agents, valuers=investors // 2, trend=investors // 4, noise=investors - investors // 2 - investors // 4,
                  n_funds=max(1, round(8 * scale)), total_cash_cents=int(80_000_000 * scale), total_shares=int(8_000 * scale))
    config = dataclasses.replace(spec.config, agents=agents, n_sessions=sessions, warmup_sessions=1,
                                 exchange_backend=backend, name=f"scale_{investors}")
    with tempfile.TemporaryDirectory() as out:
        start = time.perf_counter()
        log = load_run(run(config, spec.seed, Path(out)))
        elapsed = time.perf_counter() - start
    decisions = len(log["signals"])
    return {"investors": investors, "backend": backend, "seconds": elapsed, "decisions": decisions,
            "orders": len(log["orders"]), "sim_seconds": float(log.meta["wall_time_s"]),
            # wall_time_s covers the simulation only: no population setup output, Parquet writing or re-validation.
            "us_per_decision": 1e6 * float(log.meta["wall_time_s"]) / decisions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assets", type=int, nargs="+", default=[3, 10, 18])
    parser.add_argument("--backend", nargs="+", default=["python", "cpp"])
    parser.add_argument("--investors", type=int, default=1000)
    parser.add_argument("--sessions", type=int, default=20, help="sessions to simulate (F6 needs 502)")
    parser.add_argument("--attention", type=int, default=4, help="assets each investor follows (F6: 3-5)")
    parser.add_argument("--wakes", type=int, default=2, help="investor wake-ups per session (F4: 2)")
    parser.add_argument("--order-prob", type=float, default=0.8, help="chance of an order per followed asset per wake")
    parser.add_argument("--maker-rate", type=int, default=20, help="market-maker re-quotes per session (F4: 20)")
    parser.add_argument("--agent-sessions", type=int, default=10, help="sessions for the agent-layer run")
    parser.add_argument("--full-sessions", type=int, default=502, help="sessions of one F6 run for the estimate")
    args = parser.parse_args(argv)

    from marketsim.market.cpp_exchange import is_available
    backends = [b for b in args.backend if b != "cpp" or is_available()]

    print(f"Part 1. Market layer: {args.investors} investors x {args.attention} followed assets, "
          f"{args.wakes} wakes/session, {args.sessions} sessions; invariants checked every session.")
    market = {}
    for backend in backends:
        for n in args.assets:
            r = market_layer(n, backend, args)
            market[(backend, n)] = r
            print(f"  {backend:6s} {n:2d} assets: {r['orders_per_session']:6.0f} orders + {r['cancels_per_session']:5.0f} cancels/session, "
                  f"{r['ops_per_s']:9,.0f} ops/s, {r['seconds_per_session']:.3f} s/session, "
                  f"empty book side {100 * r['empty_side_share']:.1f}% of samples")

    print(f"\nPart 2. Agent layer: F4 market with {args.investors} investors, {args.agent_sessions} sessions.")
    agent = {}
    for backend in backends:
        r = agent_layer(args.investors, args.agent_sessions, backend)
        agent[backend] = r
        print(f"  {backend:6s}: {r['decisions']:,} decisions, {r['orders']:,} orders, {r['sim_seconds']:.1f} s simulated "
              f"-> {r['us_per_decision']:.0f} us per decision")

    best = "cpp" if "cpp" in agent else backends[0]
    print(f"\nEstimate for one F6 run ({args.full_sessions} sessions, backend {best}):")
    for n in args.assets:
        layer = market[(best, n)]["seconds_per_session"] * args.full_sessions
        decisions = args.investors * args.wakes * args.full_sessions * min(args.attention, n)
        agents_s = decisions * agent[best]["us_per_decision"] / 1e6
        # The per-decision cost of part 2 already contains that decision's own trip through gateway and exchange,
        # so the agent part is the central estimate; adding the whole market layer double-counts and is an upper bound.
        print(f"  {n:2d} assets: ~{agents_s / 60:4.1f} min per run (agents, one decision per followed asset); "
              f"upper bound with the full market layer added: ~{(agents_s + layer) / 60:4.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
