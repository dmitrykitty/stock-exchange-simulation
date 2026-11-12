"""Live run of the network market (F9): the simulation advances step by step while controls arrive.

A ``LiveSession`` owns one ``NetworkSimulation``. ``step`` advances by one sampling step (``sample_dt``) with
the usual event-by-event loop, ``frame`` reads what the live page shows, ``control`` schedules a control event
at the earliest time that keeps the run reproducible (an external shock at the next session open, the other
controls at the time simulated so far). ``stop`` closes the run at the next session boundary and ``write``
stores a K3 log whose ``meta.json`` lists the applied controls; ``run_with_controls`` replays such a list into
the same log. The wall clock never reaches the simulation: how fast the steps are taken and when the buttons
were pressed only decide which simulation times the controls get, and those are logged.

    python -m marketsim.sim.live runs/live/<run_id>      # replay meta.controls and compare every table
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import queue
import signal
import tempfile
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from marketsim.agents.strategies import market_price
from marketsim.experiments.config import ConfigError, RunConfig
from marketsim.market.api import NO_PRICE
from marketsim.sim import api
from marketsim.sim.log_schema import RUN_TABLES, load_run
from marketsim.sim.network import CONTROL_KINDS, NetworkSimulation
from marketsim.sim.settings import identity_dict

LIVE_VERSION = "f9-live-v1"


class LiveSession:
    def __init__(self, config: RunConfig, seed: int) -> None:
        if config.graph == "single_asset":
            raise ConfigError("the live mode runs a graph market (F5/F6), not graph: single_asset")
        self.config, self.seed = config, seed
        self._v = api._validate_graph(config, seed)
        v = self._v
        self.sim = NetworkSimulation(config, v["settings"], seed, v["graph"], v["external"], v["external_names"],
                                     v["reference"])
        self.initial_resources = self.sim.resources()
        self.started = datetime.now(UTC).isoformat()
        self._clock = time.perf_counter()
        self._steps = 0
        self._trades_seen = 0
        self.end: int | None = None  # session boundary the run was closed at
        self.sim.start()

    @property
    def now(self) -> float:
        """Simulation time reached: every event before it has been processed."""
        return self.sim.horizon

    @property
    def done(self) -> bool:
        return self.end is not None

    @property
    def assets(self) -> list[str]:
        return self.sim.assets

    @property
    def external_names(self) -> list[str]:
        return self.sim._external_names

    def step(self) -> None:
        """Advance by one sampling step; at the configured end the run is closed."""
        if self.done:
            raise RuntimeError("the run is closed")
        self._steps += 1
        self.sim.advance(self._steps * self.config.sample_dt)
        if self.sim.horizon >= self.config.n_sessions:
            self.sim.finish()
            self.end = self.config.n_sessions

    def control(self, kind: str, **params) -> dict:
        """Schedule a control as soon as possible and return it as logged: ``{"t", "kind", **params}``."""
        if self.done:
            raise ConfigError("the run is closed")
        if kind not in CONTROL_KINDS:
            raise ConfigError(f"control kind must be one of {CONTROL_KINDS}, got {kind!r}")
        t = float(max(1, math.ceil(self.now))) if kind == "shock_external" else self.now
        if t >= self.config.n_sessions:
            raise ConfigError("the run ends before this control could start")
        self.sim.schedule_control(t, kind, params)
        return {"t": t, "kind": kind, **params}

    def stop(self) -> None:
        """Run to the next session boundary and close the run there (the log covers whole sessions only)."""
        if self.done:
            return
        end = max(1, math.ceil(self.now))
        self.sim.advance(end)
        if end >= self.config.n_sessions:
            self.sim.finish()
            self.end = self.config.n_sessions
        else:
            self.sim.finish(end)
            self.end = end

    def frame(self) -> dict:
        """What the page draws at the current time: prices and quotes in ticks, R, mean valuations, external
        levels, the trades of the last step as per-session candles and the mean herding strength of followers."""
        sim, market = self.sim, self.sim.gateway.market()
        n_assets = len(sim.assets)
        tops = [market.top(i) for i in range(n_assets)]
        depth = [market.depth(i, 5) for i in range(n_assets)]
        v_mean, _ = sim.valuation_stats()
        session = min(max(math.floor(self.now), 0), self.config.n_sessions - 1)
        trades = sim.recorder.rows["trades"].columns
        first, self._trades_seen = self._trades_seen, len(trades["t"])
        candles: dict[tuple[int, int], list[int]] = {}
        for k in range(first, self._trades_seen):
            key = (int(trades["asset"][k]), math.floor(trades["t"][k]))
            price, qty = int(trades["price"][k]), int(trades["qty"][k])
            c = candles.get(key)
            if c is None:
                candles[key] = [price, price, price, price, qty]
            else:
                c[1], c[2], c[3], c[4] = max(c[1], price), min(c[2], price), price, c[4] + qty
        followers = sim.herd_followers
        return {
            "t": round(self.now, 6), "session": session,
            "price": [market_price(market, sim.start_ticks[i], i) for i in range(n_assets)],
            "bid": [None if top.bid == NO_PRICE else int(top.bid) for top in tops],
            "ask": [None if top.ask == NO_PRICE else int(top.ask) for top in tops],
            "depth_bid": [int(d["bid_qty"].sum()) for d in depth],
            "depth_ask": [int(d["ask_qty"].sum()) for d in depth],
            "vmean": [round(float(v), 1) for v in v_mean],
            "ref": [round(float(r), 1) for r in sim.reference_at(session)],
            "ext": [round(float(x), 6) for x in sim.external_at(session)],
            "candles": [[a, s, *c] for (a, s), c in sorted(candles.items())],
            "h": round(float(sim.herd_h[followers].mean()), 4) if len(followers) else 0.0,
        }

    def describe(self) -> dict:
        """What the live page needs once, as plain data: assets, start prices, external names, run settings."""
        sim, hs = self.sim, self.sim.settings.herding
        return {
            "assets": list(sim.assets), "start": list(sim.start_ticks), "externals": list(self.external_names),
            "n_sessions": self.config.n_sessions, "sample_dt": self.config.sample_dt, "seed": self.seed,
            "config": asdict(self.config), "investors": sim.investors, "followers": len(sim.herd_followers),
            "h": hs.h, "h_std": hs.h_std,
        }

    def applied_controls(self) -> list[dict]:
        """Controls the simulation has applied so far, in order (``{"t", "kind", **params}``)."""
        return [dict(c) for c in self.sim.applied_controls]

    def logged_config(self) -> RunConfig:
        """The configuration a fresh run needs to give this log: ``n_sessions`` cut to where the run stopped."""
        if self.end is None:
            raise RuntimeError("stop the run first")
        return replace(self.config, n_sessions=self.end,
                       warmup_sessions=min(self.config.warmup_sessions, self.end - 1))

    def write(self, out_dir: str | Path) -> Path:
        """Write the K3 log of the closed run to ``out_dir / <run_id>``; ``meta.controls`` lists the controls."""
        config = self.logged_config()
        controls = list(self.sim.applied_controls)
        v = api._validate_graph(config, self.seed)  # hashes of the external levels of the logged sessions
        run_id = _run_id(config, self.seed, v, controls)
        if (Path(out_dir) / run_id).exists():
            raise FileExistsError(f"run already exists: {Path(out_dir) / run_id}")
        meta = api._graph_meta(self.sim, run_id, config, self.seed, v, self.started,
                               time.perf_counter() - self._clock, self.initial_resources)
        meta["simulation_version"] = LIVE_VERSION
        meta["controls"] = controls
        meta["live"] = {"configured_sessions": self.config.n_sessions, "stopped_at_session": self.end}
        return api._write(self.sim, run_id, meta, out_dir)


def serve(config: RunConfig, seed: int, out_dir: str, speed: float, commands, out) -> None:
    """The simulation process of the live page: steps a ``LiveSession`` paced by the wall clock, obeys commands
    from ``commands`` and puts plain-data messages on ``out`` (``ready``, ``frame``, ``applied``, ``control`` /
    ``control_error`` replies, ``status``, ``end``, ``error``).

    Commands: ``{"cmd": "pause" | "resume" | "stop" | "quit"}``, ``{"cmd": "speed", "value": sessions per second,
    0 = as fast as possible}`` and ``{"cmd": "control", "id", "kind", "params"}``. Ctrl+C in the terminal reaches
    every process of the group; this one ignores it, so the server can ask for a clean stop and the log is written.
    A failure of the simulation is reported as an ``error`` message, never as a silent exit.
    """
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:  # run in a thread (tests): signals stay with the main thread
        pass
    try:
        session = LiveSession(config, seed)
        out.put({"type": "ready", **session.describe(), "frame": session.frame()})
        _serve_loop(session, out_dir, speed, commands, out)
    except Exception as exc:  # noqa: BLE001 - reported to the page and the terminal
        out.put({"type": "error", "message": f"the simulation failed: {type(exc).__name__}: {exc}"})


def _pace(value) -> float:
    """A display speed in sessions per second (0 = as fast as possible), checked."""
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("speed must be a finite number >= 0")
    return value


def _serve_loop(session: LiveSession, out_dir: str, speed: float, commands, out) -> None:
    config = session.config
    paused, next_at, reported = True, time.perf_counter(), 0

    def interval_of(value: float) -> float:
        return config.sample_dt / value if value > 0 else 0.0

    def report_applied() -> None:
        nonlocal reported
        applied = session.applied_controls()
        if len(applied) > reported:
            out.put({"type": "applied", "controls": applied[reported:]})
            reported = len(applied)

    def close() -> None:
        session.stop()
        report_applied()
        out.put({"type": "frame", "frame": session.frame()})
        try:
            path = session.write(out_dir)
            out.put({"type": "end", "path": str(path), "session": session.end, "controls": session.applied_controls()})
        except Exception as exc:  # noqa: BLE001
            out.put({"type": "error", "message": f"could not write the log: {exc}"})

    interval = interval_of(_pace(speed))
    while True:
        running = not paused and not session.done
        wait = max(0.0, next_at - time.perf_counter()) if running else 0.5
        try:
            command = commands.get(timeout=wait) if wait > 0 else commands.get_nowait()
        except queue.Empty:
            command = None
        if command is not None:
            name = command.get("cmd")
            if name == "quit":
                break
            if name == "pause":
                paused = True
            elif name == "resume":
                paused, next_at = False, time.perf_counter()
            elif name == "speed":
                try:
                    interval = interval_of(_pace(command.get("value")))
                except (TypeError, ValueError):
                    pass  # the server checks the value; a bad one leaves the speed as it is
                next_at = time.perf_counter()
            elif name == "control":
                try:
                    params = command.get("params", {})
                    if not isinstance(params, dict):
                        raise ConfigError("control params must be an object")
                    control = session.control(str(command.get("kind")), **params)
                    out.put({"type": "control", "id": command.get("id"), "control": control})
                except (ConfigError, TypeError, ValueError) as exc:
                    out.put({"type": "control_error", "id": command.get("id"), "message": str(exc)})
            elif name == "stop" and not session.done:
                close()
            out.put({"type": "status", "paused": paused, "done": session.done})
            continue
        if not running:
            continue
        session.step()
        report_applied()
        out.put({"type": "frame", "frame": session.frame()})
        if session.done:
            close()
            out.put({"type": "status", "paused": paused, "done": True})
        next_at = max(next_at + interval, time.perf_counter()) if interval else time.perf_counter()


def _run_id(config: RunConfig, seed: int, v: dict, controls: list[dict]) -> str:
    identity = json.dumps({
        "config": asdict(config), "seed": seed, "resolved_agents": identity_dict(v["settings"]),
        "simulation_version": LIVE_VERSION, "controls": controls, **v["hashes"],
    }, sort_keys=True)
    return "live_" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def run_with_controls(config: RunConfig, seed: int, controls: list[dict], out_dir: str | Path) -> Path:
    """Run ``config`` with timed controls (``{"t", "kind", **params}``, as in ``meta.controls`` of a live run)
    scheduled before the start; a live run gives the same log as this replay of its controls."""
    session = LiveSession(config, seed)
    for c in sorted(controls, key=lambda c: c["t"]):  # stable: equal times keep their logged order
        params = {k: v for k, v in c.items() if k not in ("t", "kind")}
        session.sim.schedule_control(float(c["t"]), c["kind"], params)
    session.sim.advance(config.n_sessions)
    session.sim.finish()
    session.end = config.n_sessions
    return session.write(out_dir)



def check(run_dir: str | Path) -> list[str]:
    """Replay the controls of a live run log into a new log; return the tables that differ (empty: identical)."""
    log = load_run(run_dir)
    if "controls" not in log.meta:
        raise ValueError(f"{run_dir} is not a live run (no meta.controls)")
    with tempfile.TemporaryDirectory() as out:
        again = load_run(run_with_controls(RunConfig(**log.meta["config"]), log.meta["seed"], log.meta["controls"], out))
        differ = [spec.name for spec in RUN_TABLES if not log[spec.name].equals(again[spec.name])]
        if again.path.name != log.path.name:
            differ.append("run_id")
    return differ


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Check that a live run log replays from its control events.")
    parser.add_argument("run_dir", help="live run directory (runs/live/<run_id>)")
    args = parser.parse_args(argv)
    meta = json.loads((Path(args.run_dir) / "meta.json").read_text(encoding="utf-8"))
    print(f"{len(meta.get('controls', []))} control event(s), {meta['config']['n_sessions']} sessions, "
          f"seed {meta['seed']}; replaying ...", flush=True)
    differ = check(args.run_dir)
    if differ:
        raise SystemExit(f"the replay differs in: {', '.join(differ)}")
    print("identical: every table of the replay equals the live log, same run id")


if __name__ == "__main__":
    main()
