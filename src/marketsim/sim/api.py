"""Simulation entry point (owner: S).

The discrete-event simulation: an event queue ordered by (time, sequence number), agents woken up at their
decision times, the gateway (contract K1), external signals and the dependency graph (contract K2).
Output: a run directory following contract K3.

``graph: single_asset`` runs the F4 market (one synthetic asset, constant valuations). Any other ``graph`` names a
K2 graph and runs the F5 network market (every company is an asset, valuers update from the graph).
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
import pandas as pd

from marketsim import paths
from marketsim.data.api import load_external
from marketsim.experiments.config import ConfigError, RunConfig
from marketsim.graph.api import DependencyGraph, GraphError, load_graph, resolve_graph_dir
from marketsim.graph.reference import reference_path
from marketsim.market.api import CONTRACT_VERSION
from marketsim.sim.engine import Simulation
from marketsim.sim.log_schema import LOG_VERSION, load_run
from marketsim.sim.network import NetworkSimulation
from marketsim.sim.settings import AgentSettings, identity_dict

_SIMULATION_VERSION = "f4-v3"
_NETWORK_VERSION = "f5-v1"


def _validate_common(config: RunConfig, seed: int) -> None:
    if type(seed) is not int or seed < 0:
        raise ConfigError("seed must be a non-negative integer")
    if type(config.n_sessions) is not int or type(config.warmup_sessions) is not int:
        raise ConfigError("session counts must be integers")
    if type(config.sample_dt) not in (int, float) or not math.isfinite(config.sample_dt):
        raise ConfigError("sample_dt must be finite")
    config.validate()
    if config.n_sessions / config.sample_dt > 1_000_000:
        raise ConfigError("at most 1000000 book snapshots are supported per run")


def _validate(config: RunConfig, seed: int) -> tuple[AgentSettings, dict]:
    _validate_common(config, seed)
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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_graph(config: RunConfig, seed: int) -> dict:
    """Load and check everything a graph run needs before any output exists (F5)."""
    _validate_common(config, seed)
    if type(config.use_market_index) is not bool:
        raise ConfigError("use_market_index must be boolean")
    settings = AgentSettings.from_dict(config.agents, graph_mode=True)
    try:
        graph = load_graph(config.graph)
        graph_dir = resolve_graph_dir(config.graph)
    except (FileNotFoundError, GraphError) as exc:
        raise ConfigError(f"graph {config.graph!r}: {exc}") from exc
    if not graph.companies():
        raise ConfigError("the graph has no company nodes")
    if settings.assets:
        graph = _subgraph(graph, settings.assets)
    try:
        external = load_external(config.external)
    except FileNotFoundError as exc:
        raise ConfigError(f"external scenario {config.external!r}: {exc}") from exc
    names = graph.externals()
    missing = [name for name in names if name not in external.columns]
    if missing:
        raise ConfigError(f"external scenario {config.external!r} lacks graph nodes {missing}")
    levels = external.sort_index()[names]
    if list(levels.index) != list(range(len(levels))):
        raise ConfigError("external scenario sessions must be 0, 1, 2, ... without gaps")
    values = levels.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ConfigError("external levels must be finite and > 0")
    # Sessions beyond the scenario keep its last levels; scenario rows beyond the run are not used.
    values = values[np.minimum(np.arange(config.n_sessions), len(values) - 1)]
    ref_levels = pd.DataFrame(values.copy(), index=pd.Index(range(config.n_sessions), name="session"), columns=names)
    if not config.use_market_index and "sp500" in names:
        ref_levels["sp500"] = ref_levels["sp500"].iloc[0]  # agents ignore the index, so does the reference
    reference = reference_path(graph, ref_levels, mode="full")[graph.companies()].to_numpy() * 100  # USD -> ticks
    hashes = {
        "graph_sha256": _sha(b"".join((graph_dir / f).read_bytes() for f in ("nodes.csv", "edges.csv"))),
        "external_sha256": _sha(pd.DataFrame(values, columns=names).to_csv(index=False).encode()),
    }
    return {"settings": settings, "graph": graph, "external": values, "external_names": names,
            "reference": reference, "hashes": hashes}


def _subgraph(graph: DependencyGraph, tickers: tuple[str, ...]) -> DependencyGraph:
    """The graph restricted to ``tickers`` (file order kept) and all external nodes (F6 pilots).

    A selected company must bring its company parents along: without them its remaining weights would describe
    a different regression.
    """
    companies = graph.companies()
    unknown = [t for t in tickers if t not in companies]
    if unknown:
        raise ConfigError(f"agents.assets: {unknown} are not companies of the graph")
    chosen = set(tickers)
    for company in tickers:
        missing = [p for p in graph.parents(company, "full") if p in companies and p not in chosen]
        if missing:
            raise ConfigError(f"agents.assets: {company} needs its parents {missing}")
    nodes = {name: node for name, node in graph.nodes.items() if node.kind == "external" or name in chosen}
    return DependencyGraph(nodes, [e for e in graph.edges if e.child in chosen], dict(graph.meta)).validate()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=paths.PROJECT_ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _versions() -> dict:
    return {name: importlib.metadata.version(name) for name in ("marketsim", "numpy", "pandas", "pyarrow")}


def _write(simulation, run_id: str, meta: dict, out_dir: Path) -> Path:
    """Write into a temporary directory, validate it through K3, then rename into place."""
    destination = Path(out_dir) / run_id
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{run_id}_", dir=out_dir) as temporary:
        staging = Path(temporary)
        simulation.recorder.write(staging, meta)
        load_run(staging)
        staging.rename(destination)
    return destination


def _run_graph(config: RunConfig, seed: int, out_dir: Path) -> Path:
    v = _validate_graph(config, seed)
    settings = v["settings"]
    identity = json.dumps({
        "config": asdict(config), "seed": seed, "resolved_agents": identity_dict(settings),
        "simulation_version": _NETWORK_VERSION, **v["hashes"],
    }, sort_keys=True)
    run_id = "f5_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    if (Path(out_dir) / run_id).exists():
        raise FileExistsError(f"run already exists: {Path(out_dir) / run_id}")
    started = datetime.now(UTC).isoformat()
    start = time.perf_counter()
    simulation = NetworkSimulation(config, settings, seed, v["graph"], v["external"], v["external_names"],
                                   v["reference"])
    initial_resources = simulation.resources()
    simulation.execute()
    meta = _graph_meta(simulation, run_id, config, seed, v, started, time.perf_counter() - start, initial_resources)
    return _write(simulation, run_id, meta, out_dir)


def _graph_meta(
    simulation: NetworkSimulation, run_id: str, config: RunConfig, seed: int, v: dict, started: str,
    wall_time_s: float, initial_resources: dict,
) -> dict:
    """meta.json of a finished network run (also used by the F9 live mode)."""
    settings = v["settings"]
    population = simulation.population
    investors = simulation.investors
    meta = {
        "run_id": run_id, "log_version": LOG_VERSION, "contract_version": CONTRACT_VERSION,
        "config": asdict(config), "seed": seed, "git_commit": _git_commit(), "versions": _versions(),
        "started_at": started, "wall_time_s": wall_time_s, "assets": simulation.assets,
        "simulation_version": _NETWORK_VERSION, "resolved_agents": asdict(settings), **v["hashes"],
        "start_prices_ticks": simulation.start_ticks, "external_nodes": v["external_names"],
        "initial_resources": initial_resources, "final_resources": simulation.resources(),
        "invariant_checks": simulation.invariant_checks,
        "population": {
            "kinds": population.kinds, "fund_ids": np.flatnonzero(population.funds).tolist(),
            "maker_assets": population.maker_asset, "followed_assets": simulation.followed[:investors],
            "herding_h": [round(float(h), 6) for h in simulation.herd_h],
            "observation_delay": [round(float(d), 6) for d in simulation.delay[:investors]],
        },
        "valuation_semantics": ("v_mean/v_std over valuers following the asset, each as of its latest decision; "
                                "reference: path R with full weights, constant within a session"),
        "order_status_semantics": "last K1 execution status; cancellations and GTD expiry are recorded in events",
    }
    if settings.price_scaling:
        meta["asset_parameters"] = {
            "total_shares": simulation.asset_total_shares, "market_maker_shares": simulation.asset_maker_shares,
            "market_maker_qty": [a.market_maker_qty for a in simulation.asset_settings],
            "market_maker_spread_ticks": [a.market_maker_spread_ticks for a in simulation.asset_settings],
            "inventory_skew_ticks": [a.inventory_skew_ticks for a in simulation.asset_settings],
        }
    if settings.risk_limit > 0:
        meta["risk_limit_triggers"] = simulation.risk_triggers
        meta["population"]["risk_limited"] = np.flatnonzero(simulation.risk_limited).tolist()
    return meta


def run(config: RunConfig, seed: int, out_dir: Path) -> Path:
    """Run one simulation and write its log to ``out_dir / <run_id>``. Same config + seed -> same log."""
    if config.graph != "single_asset":
        return _run_graph(config, seed, out_dir)
    settings, levels = _validate(config, seed)
    identity = json.dumps({
        "config": asdict(config), "seed": seed, "resolved_agents": identity_dict(settings),
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
        "config": asdict(config), "seed": seed, "git_commit": _git_commit(), "versions": _versions(),
        "started_at": started, "wall_time_s": time.perf_counter() - start, "assets": [settings.ticker],
        "simulation_version": _SIMULATION_VERSION, "resolved_agents": identity_dict(settings),
        "initial_resources": initial_resources, "final_resources": simulation.resources(),
        "invariant_checks": simulation.invariant_checks,
        "population": {"kinds": simulation.population.kinds, "fund_ids": np.flatnonzero(simulation.population.funds).tolist()},
        "order_status_semantics": "last K1 execution status; cancellations and GTD expiry are recorded in events",
    }
    return _write(simulation, run_id, meta, out_dir)
