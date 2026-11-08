"""Simulation entry point (owner: S).

The discrete-event simulation: an event queue ordered by (time, sequence number), agents woken up at their
decision times, the gateway (contract K1), external signals and the dependency graph (contract K2).
Output: a run directory following contract K3.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import subprocess
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from marketsim import paths
from marketsim.data.api import load_external
from marketsim.experiments.config import ConfigError, RunConfig
from marketsim.market.api import CONTRACT_VERSION
from marketsim.sim.engine import Simulation
from marketsim.sim.log_schema import LOG_VERSION, load_run
from marketsim.sim.settings import AgentSettings

_SIMULATION_VERSION = "f4-v2"


def _validate(config: RunConfig, seed: int) -> tuple[AgentSettings, dict]:
    if type(seed) is not int or seed < 0:
        raise ConfigError("seed must be a non-negative integer")
    if type(config.n_sessions) is not int or type(config.warmup_sessions) is not int:
        raise ConfigError("session counts must be integers")
    if type(config.sample_dt) not in (int, float) or not math.isfinite(config.sample_dt):
        raise ConfigError("sample_dt must be finite")
    config.validate()
    if config.n_sessions / config.sample_dt > 1_000_000:
        raise ConfigError("at most 1000000 book snapshots are supported per run")
    if config.graph != "single_asset" or config.external != "flat" or config.use_market_index is not False:
        raise ConfigError("F4 requires graph='single_asset', external='flat', use_market_index=false; see F5 for graphs")
    settings = AgentSettings.from_dict(config.agents)
    external = load_external(config.external)
    levels = external.drop(columns="date")
    if levels.empty or not np.isfinite(levels.to_numpy()).all() or (levels.to_numpy() <= 0).any():
        raise ConfigError("flat external scenario must contain positive finite levels")
    if not levels.eq(levels.iloc[0]).all().all():
        raise ConfigError("F4 external scenario must be constant")
    return settings, {str(k): float(v) for k, v in levels.iloc[0].items()}


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=paths.PROJECT_ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run(config: RunConfig, seed: int, out_dir: Path) -> Path:
    """Run one simulation and write its log to ``out_dir / <run_id>``. Same config + seed -> same log."""
    settings, levels = _validate(config, seed)
    identity = json.dumps({
        "config": asdict(config), "seed": seed, "resolved_agents": asdict(settings),
        "simulation_version": _SIMULATION_VERSION,
    }, sort_keys=True)
    run_id = "f4_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    destination = Path(out_dir) / run_id
    if destination.exists():
        raise FileExistsError(f"run already exists: {destination}")
    started = datetime.now(UTC).isoformat()
    start = time.perf_counter()
    simulation = Simulation(config, settings, seed, levels)
    initial_resources = simulation.resources()
    simulation.execute()
    meta = {
        "run_id": run_id, "log_version": LOG_VERSION, "contract_version": CONTRACT_VERSION,
        "config": asdict(config), "seed": seed, "git_commit": _git_commit(),
        "versions": {name: importlib.metadata.version(name) for name in ("marketsim", "numpy", "pandas", "pyarrow")},
        "started_at": started, "wall_time_s": time.perf_counter() - start, "assets": [settings.ticker],
        "simulation_version": _SIMULATION_VERSION, "resolved_agents": asdict(settings),
        "initial_resources": initial_resources, "final_resources": simulation.resources(),
        "invariant_checks": simulation.invariant_checks,
        "population": {"kinds": simulation.population.kinds, "fund_ids": np.flatnonzero(simulation.population.funds).tolist()},
        "order_status_semantics": "last K1 execution status; cancellations and GTD expiry are recorded in events",
    }
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{run_id}_", dir=out_dir) as temporary:
        staging = Path(temporary)
        simulation.recorder.write(staging, meta)
        load_run(staging)
        staging.rename(destination)
    return destination
