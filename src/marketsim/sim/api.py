"""Simulation entry point (owner: S).

The discrete-event simulation: an event queue ordered by (time, sequence number), agents woken up at their
decision times, the gateway (contract K1), external signals and the dependency graph (contract K2).
Output: a run directory following contract K3.
"""

from __future__ import annotations

from pathlib import Path

from marketsim.experiments.config import RunConfig


def run(config: RunConfig, seed: int, out_dir: Path) -> Path:
    """Run one simulation and write its log to ``out_dir / <run_id>``. Same config + seed -> same log."""
    raise NotImplementedError("sim.run (owner: S, see docs/plans/phases/F4_single_asset_market.md)")
