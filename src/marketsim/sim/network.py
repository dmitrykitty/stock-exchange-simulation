"""Multi-asset DES on a dependency graph (F5): valuers update their valuations from the graph (contract K2).

Every company of the graph is an asset, in ``graph.companies()`` order. Investors share one wallet across assets
and trade the assets they follow; one or more market makers quote each asset. Valuers follow the rule of
docs/model/model_description.md, section 4.2:

    ln V_a,i(t) = ln V_a,i(t') + sum_j w^_a,ij [ln P^obs_a,j(t) - ln P^obs_a,j(t')]

Summing these increments over an agent's decisions telescopes, so the valuation at any moment is

    ln V_a,i(t) = ln V_a,i(0) + sum_j w^_a,ij [ln P^obs_a,j(t) - ln P^obs_a,j(0)],

which is what is computed here (same result, no per-decision state). Parents and weights: myopic valuers use
``w_direct`` (external parents only), network valuers ``w_full`` (also companies). ``w^ = w (1 + eta)`` with a
fixed personal error eta per agent, asset and parent. ``P^obs(t) = P(t - d_a)``: for an external node, the level
of the session containing ``t - d_a``; for a company, the midpoint on this exchange (last trade, then start price,
when a side is empty), taken live when ``d_a = 0`` and otherwise from the last book snapshot not after ``t - d_a``.

All randomness comes from the run seed; the same config and seed give the same log.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from marketsim.agents.herding import HerdingNetwork, susceptibility
from marketsim.agents.strategies import (
    OrderIntent,
    make_order,
    maker_orders,
    market_price,
    noise_signal,
    trend_signal,
    valuer_signal,
)
from marketsim.experiments.config import RunConfig
from marketsim.graph.api import DependencyGraph
from marketsim.market import Gateway, make_exchange
from marketsim.market.api import Report, Status, TimeInForce
from marketsim.sim.recorder import Recorder
from marketsim.sim.scheduler import Scheduler
from marketsim.sim.settings import AgentSettings

INVESTOR_KINDS = ("valuer_myopic", "valuer_network", "trend", "noise")
_REJECTED = (Status.REJECTED_FUNDS, Status.REJECTED_INVALID, Status.REJECTED_SELF_TRADE)


@dataclass
class NetworkPopulation:
    kinds: list[str]
    funds: np.ndarray            # bool per agent (investors only can be funds)
    cash: np.ndarray             # int64 per agent
    shares: np.ndarray           # int64 (agents, assets)
    maker_asset: list[int]       # asset of every market maker, in maker order
    follows: np.ndarray          # bool (agents, assets): assets an agent trades


def _allocate(total: int, indices: np.ndarray, out: np.ndarray) -> None:
    amount, remainder = divmod(total, len(indices))
    out[indices] = amount
    out[indices[:remainder]] += 1


def make_network_population(
    settings: AgentSettings, n_assets: int, rng: np.random.Generator, attention_rng: np.random.Generator,
) -> NetworkPopulation:
    """Investor groups, fund quotas and integer resources as in F4, with shares in every asset."""
    s = settings
    counts = (s.valuers_myopic, s.valuers_network, s.trend, s.noise)
    kinds = [kind for kind, size in zip(INVESTOR_KINDS, counts) for _ in range(size)]
    n = len(kinds)
    maker_asset = [asset for asset in range(n_assets) for _ in range(s.market_makers_per_asset)]
    kinds += ["market_maker"] * len(maker_asset)
    funds = np.zeros(len(kinds), dtype=bool)
    # Largest-remainder quotas keep the fund strategy mix proportional and independent of the seed and C.
    quotas = [size * s.n_funds // n for size in counts]
    remainders = [size * s.n_funds % n for size in counts]
    for index in sorted(range(len(counts)), key=lambda i: (-remainders[i], i))[:s.n_funds - sum(quotas)]:
        quotas[index] += 1
    offset = 0
    for size, quota in zip(counts, quotas):
        if quota:
            funds[offset + rng.choice(size, quota, replace=False)] = True
        offset += size
    fund_indices = np.flatnonzero(funds)
    retail_indices = np.flatnonzero(~funds[:n])
    cash = np.zeros(len(kinds), dtype=np.int64)
    shares = np.zeros((len(kinds), n_assets), dtype=np.int64)
    fund_cash = int(Decimal(str(s.concentration)) * s.total_cash_cents)
    _allocate(fund_cash, fund_indices, cash)
    _allocate(s.total_cash_cents - fund_cash, retail_indices, cash)
    fund_shares = int(Decimal(str(s.concentration)) * s.total_shares)  # total_shares is per asset
    for asset in range(n_assets):
        _allocate(fund_shares, fund_indices, shares[:, asset])
        _allocate(s.total_shares - fund_shares, retail_indices, shares[:, asset])
    cash[n:] = s.market_maker_cash_cents
    for k, asset in enumerate(maker_asset):
        shares[n + k, asset] = s.market_maker_shares
    follows = np.zeros((len(kinds), n_assets), dtype=bool)
    if s.attention == 0 or s.attention >= n_assets:
        follows[:n] = True
    else:
        for agent in range(n):
            follows[agent, np.sort(attention_rng.choice(n_assets, s.attention, replace=False))] = True
    for k, asset in enumerate(maker_asset):
        follows[n + k, asset] = True
    return NetworkPopulation(kinds, funds, cash, shares, maker_asset, follows)


class NetworkSimulation:
    def __init__(
        self, config: RunConfig, settings: AgentSettings, seed: int, graph: DependencyGraph,
        external: np.ndarray, external_names: list[str], reference: np.ndarray,
    ) -> None:
        """``external``: levels (n_sessions, n_external) in ``external_names`` order; ``reference``: R in ticks
        (n_sessions, n_assets). Both already cover every session of the run."""
        self.config, self.settings, self.graph = config, settings, graph
        s = settings
        self.assets = graph.companies()
        n_assets = len(self.assets)
        self.start_ticks = [round(graph.nodes[c].start_price * 100) for c in self.assets]
        (population_seed, value_seed, network_seed, wake_seed, decision_seed, herd_seed, attention_seed,
         weight_seed) = np.random.SeedSequence(seed).spawn(8)
        self.population = make_network_population(
            s, n_assets, np.random.default_rng(population_seed), np.random.default_rng(attention_seed),
        )
        n = len(self.population.kinds)
        self.investors = s.investors
        self._wake_rng = [np.random.default_rng(x) for x in wake_seed.spawn(n)]
        self._decision_rng = [np.random.default_rng(x) for x in decision_seed.spawn(n)]
        self.followed = [np.flatnonzero(self.population.follows[a]).tolist() for a in range(n)]
        self.network = HerdingNetwork(
            self.investors, s.herding.neighbors, s.herding.ttl, np.random.default_rng(network_seed),
            n_assets=n_assets, follows=self.population.follows[:self.investors],
        )
        hs = s.herding
        self.herd_h = susceptibility(self.investors, hs.h, hs.followers, hs.h_std, np.random.default_rng(herd_seed))
        self.gateway = Gateway(make_exchange(config.exchange_backend, n_assets), self.population.cash,
                               self.population.shares)
        self.scheduler = Scheduler()
        self.recorder = Recorder()
        self._open: list[set[int]] = [set() for _ in range(n)]
        self._owners: dict[int, int] = {}
        self._external = external
        self._external_names = external_names
        self._reference = reference
        self._history_times = [0.0]
        self._history_prices = [[p] for p in self.start_ticks]  # per asset, aligned with _history_times
        self.invariant_checks = 0
        self._init_valuers(np.random.default_rng(value_seed), np.random.default_rng(weight_seed))

    # ----- valuers -------------------------------------------------------------------------------------------
    def _init_valuers(self, value_rng: np.random.Generator, weight_rng: np.random.Generator) -> None:
        """Initial valuations, personal weights and delays; parents resolved once per (kind, asset)."""
        s, cfg = self.settings, self.config
        company_pos = {c: i for i, c in enumerate(self.assets)}
        external_pos = {name: k for k, name in enumerate(self._external_names)}
        hidden = set() if cfg.use_market_index else {"sp500"}
        self._parents: dict[tuple[str, int], list[tuple[str, int, float]]] = {}
        for kind, mode in (("valuer_myopic", "direct"), ("valuer_network", "full")):
            for i, company in enumerate(self.assets):
                items = []
                for parent, weight in self.graph.parents(company, mode).items():
                    if parent in hidden:
                        continue
                    if parent in company_pos:
                        items.append(("company", company_pos[parent], weight))
                    else:
                        items.append(("external", external_pos[parent], weight))
                self._parents[(kind, i)] = items
        n = len(self.population.kinds)
        self.delay = np.zeros(n)
        valuers = [a for a in range(self.investors) if self.population.kinds[a].startswith("valuer")]
        if s.observation_delay > 0:
            self.delay[valuers] = value_rng.uniform(0.0, 2 * s.observation_delay, len(valuers))
        # Per valuer and followed asset: ln V(0) and perceived weights, drawn in a fixed order.
        self._ln_v0: dict[tuple[int, int], float] = {}
        self._weights: dict[tuple[int, int], np.ndarray] = {}
        self._ln_v_last = np.full((n, len(self.assets)), np.nan)
        for agent in valuers:
            kind = self.population.kinds[agent]
            for i in self.followed[agent]:
                error = float(np.clip(value_rng.normal(0.0, s.valuation_std), -0.9, 0.9))
                self._ln_v0[(agent, i)] = math.log(self.start_ticks[i] * (1 + error))
                base = np.array([w for _, _, w in self._parents[(kind, i)]], dtype=float)
                if s.weight_error_std > 0 and len(base):
                    base = base * (1 + weight_rng.normal(0.0, s.weight_error_std, len(base)))
                self._weights[(agent, i)] = base
                self._ln_v_last[agent, i] = self._ln_v0[(agent, i)]

    def _session_of(self, t: float) -> int:
        return min(max(math.floor(t), 0), len(self._external) - 1)

    def _observed_company(self, j: int, t: float, now: float) -> int:
        if t >= now:
            return market_price(self.gateway.market(), self.start_ticks[j], j)
        index = max(0, bisect_right(self._history_times, t) - 1)
        return self._history_prices[j][index]

    def valuation(self, agent: int, asset: int, now: float) -> float:
        """Valuation in ticks of ``agent`` for ``asset`` at ``now`` (the telescoped F5 rule)."""
        kind = self.population.kinds[agent]
        seen_at = now - float(self.delay[agent])
        ln_v = self._ln_v0[(agent, asset)]
        weights = self._weights[(agent, asset)]
        for (source, j, _), w in zip(self._parents[(kind, asset)], weights):
            if source == "external":
                change = math.log(self._external[self._session_of(seen_at), j] / self._external[0, j])
            else:
                change = math.log(self._observed_company(j, seen_at, now) / self.start_ticks[j])
            ln_v += w * change
        self._ln_v_last[agent, asset] = ln_v
        return math.exp(ln_v)

    # ----- events --------------------------------------------------------------------------------------------
    def _check(self) -> None:
        self.gateway.check_invariants()
        self.invariant_checks += 1

    def _next_wake(self, agent: int, now: float) -> None:
        s = self.settings
        rate = s.market_maker_rate if agent >= self.investors else s.decision_rate
        interval = float(self._wake_rng[agent].exponential(1 / rate))
        time = max(now + interval, float(np.nextafter(now, np.inf)))
        if time < self.config.n_sessions:
            self.scheduler.schedule(time, "wake", agent)

    def _withdraw(self, agent: int, order_id: int, now: float, kind: str) -> None:
        if kind == "order_expired":
            remaining = self.gateway.expire(order_id, now)
        else:
            remaining = self.gateway.cancel(agent, order_id, now)
        self._open[agent].discard(order_id)
        if remaining:
            self.recorder.event(now, kind, {"order_id": order_id, "agent": agent, "remaining_qty": remaining})

    def _place(self, agent: int, asset: int, intent: OrderIntent, now: float) -> Report:
        r = self.gateway.place(agent, asset, intent.side, intent.qty, intent.limit, intent.tif, now)
        self.recorder.order(now, agent, intent.side, intent.qty, intent.limit, intent.tif, r, asset)
        self._owners[r.order_id] = agent
        if r.resting_qty:
            self._open[agent].add(r.order_id)
            if intent.tif == TimeInForce.GTD:
                self.scheduler.schedule(now + self.settings.order_lifetime, "expire", (agent, r.order_id))
        completed = self.recorder.fills(now, self.gateway.drain_fills())
        for order_id in completed:
            self._open[self._owners[order_id]].discard(order_id)
        return r

    def _wealth(self, portfolio) -> int:
        market = self.gateway.market()
        holdings = portfolio.shares_available + portfolio.shares_reserved
        return portfolio.cash_available + portfolio.cash_reserved + sum(
            int(holdings[j]) * market_price(market, self.start_ticks[j], j) for j in range(len(self.assets))
        )

    def _own_signal(self, agent: int, kind: str, asset: int, price: int, now: float) -> float:
        s = self.settings
        if kind.startswith("valuer"):
            return valuer_signal(self.valuation(agent, asset, now), price, s.gamma)
        if kind == "trend" and s.ablation != "no_trend":
            index = max(0, bisect_right(self._history_times, now - s.trend_lookback) - 1)
            return trend_signal(price, self._history_prices[asset][index], s.trend_scale)
        return noise_signal(self._decision_rng[agent])

    def _wake(self, agent: int, now: float) -> None:
        s = self.settings
        market = self.gateway.market()
        kind = self.population.kinds[agent]
        if kind == "market_maker":
            asset = self.population.maker_asset[agent - self.investors]
            # The maker uses the visible center before removing its own two quotes.
            price = market_price(market, self.start_ticks[asset], asset)
            for order_id in sorted(self._open[agent]):
                self._withdraw(agent, order_id, now, "order_cancelled")
            portfolio = self.gateway.portfolio(agent)
            for intent in maker_orders(price, portfolio, s.market_maker_shares, s, asset):
                self._place(agent, asset, intent, now)
        else:
            for order_id in sorted(self._open[agent]):
                self._withdraw(agent, order_id, now, "order_cancelled")
            h = 0.0 if s.ablation == "no_herding" else float(self.herd_h[agent])
            # Followed assets are decided one after another; each decision sees the book and the wallet left by
            # the previous one, keeping the event-by-event semantics of the model.
            for asset in self.followed[agent]:
                price = market_price(market, self.start_ticks[asset], asset)
                own = self._own_signal(agent, kind, asset, price, now)
                herd, signal = self.network.mix(agent, own, now, h, asset)
                self.recorder.rows["signals"].append({
                    "t": now, "agent": agent, "asset": asset, "own_signal": own,
                    "herd_signal": herd, "final_signal": signal,
                })
                portfolio = self.gateway.portfolio(agent)
                intent = make_order(signal, price, market.top(asset), portfolio, s, self._decision_rng[agent],
                                    asset, self._wealth(portfolio))
                direction = 0
                if intent is not None:
                    report = self._place(agent, asset, intent, now)
                    if report.status not in _REJECTED:
                        direction = intent.side
                self.network.publish(agent, direction, now, asset)
        if s.debug:
            self._check()
        self._next_wake(agent, now)

    def _snapshot(self, now: float) -> None:
        n_assets = len(self.assets)
        v_mean = np.empty(n_assets)
        v_std = np.zeros(n_assets)
        valuers = self._ln_v_last[:self.investors]
        for i in range(n_assets):
            column = valuers[:, i]
            column = column[~np.isnan(column)]
            if len(column):
                values = np.exp(column)
                v_mean[i], v_std[i] = values.mean(), values.std()
            else:
                v_mean[i] = self.start_ticks[i]
        session = self._session_of(now)
        self.recorder.snapshot_assets(now, self.gateway.market(), v_mean, v_std, self._reference[session])

    def resources(self) -> dict:
        """Totals from public portfolio views, per asset and overall, using Python integers."""
        portfolios = [self.gateway.portfolio(i) for i in range(len(self.population.kinds))]
        n_assets = len(self.assets)
        by_asset = [sum(int(p.shares_available[j]) + int(p.shares_reserved[j]) for p in portfolios)
                    for j in range(n_assets)]
        return {
            "cash_cents": sum(p.cash_available + p.cash_reserved for p in portfolios),
            "shares": sum(by_asset),
            "shares_by_asset": by_asset,
            "cash_reserved": sum(p.cash_reserved for p in portfolios),
            "shares_reserved": sum(int(p.shares_reserved[j]) for p in portfolios for j in range(n_assets)),
        }

    def execute(self) -> None:
        """Process events in time/sequence order; cancel remaining orders at termination."""
        cfg = self.config
        self._check()
        self.scheduler.schedule(0.0, "session", 0)
        for agent in range(self.investors, len(self.population.kinds)):
            self.scheduler.schedule(0.0, "wake", agent)
        for agent in range(self.investors):
            self._next_wake(agent, 0.0)
        self.scheduler.schedule(0.0, "sample", 0)
        market = self.gateway.market()
        for event in self.scheduler.before(cfg.n_sessions):
            now = event.time
            if event.kind == "wake":
                self._wake(event.payload, now)
            elif event.kind == "expire":
                agent, order_id = event.payload
                self._withdraw(agent, order_id, now, "order_expired")
                if self.settings.debug:
                    self._check()
            elif event.kind == "session":
                session = event.payload
                levels = {name: float(self._external[min(session, len(self._external) - 1), k])
                          for k, name in enumerate(self._external_names)}
                self.recorder.event(now, "session_start", {"session": session, **levels})
                if session + 1 < cfg.n_sessions:
                    self.scheduler.schedule(now + 1, "session", session + 1)
            elif event.kind == "sample":
                self._snapshot(now)
                self._history_times.append(now)
                for j in range(len(self.assets)):
                    self._history_prices[j].append(market_price(market, self.start_ticks[j], j))
                next_time = (event.payload + 1) * cfg.sample_dt
                if next_time < cfg.n_sessions:
                    self.scheduler.schedule(next_time, "sample", event.payload + 1)
        end = float(cfg.n_sessions)
        self._snapshot(end)
        for agent, orders in enumerate(self._open):
            for order_id in sorted(orders):
                self._withdraw(agent, order_id, end, "run_end_cancelled")
        self._check()
