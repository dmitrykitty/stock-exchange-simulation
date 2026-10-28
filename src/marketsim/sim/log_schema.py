"""Contract K3: the run log written by the simulation (owner: S), read by metrics and visualization.

A run is a directory ``runs/<run_id>/`` with:

    meta.json            run metadata (see META_KEYS)
    trades.parquet       one row per trade
    orders.parquet       one row per submitted order (with its final status)
    book.parquet         book snapshots, sampled every dt of simulation time
    signals.parquet      agent signals (own, herd, final), one row per decision
    valuations.parquet   mean/std of agent valuations and the reference path, sampled every dt
    events.parquet       control events (parameter changes, shocks) for chart annotations

Metrics (D1) and visualization (O, D1) read only these files and never import simulation code.
Change the format only following docs/model/contracts.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from marketsim.data.schemas import Column, ContractError, TableSpec, validate

LOG_VERSION = 1

META_KEYS = (
    "run_id", "log_version", "contract_version", "config", "seed", "git_commit", "versions",
    "started_at", "wall_time_s", "assets",
)


def _run_spec(name: str, *columns: Column, description: str) -> TableSpec:
    return TableSpec(name, f"{name}.parquet", "run", description, columns=columns, min_rows=0, owner="S")


TRADES = _run_spec(
    "trades",
    Column("t", "f", False), Column("seq", "i", False), Column("asset", "i", False), Column("price", "i", False),
    Column("qty", "i", False), Column("taker_owner", "i", False), Column("maker_owner", "i", False),
    Column("taker_side", "i", False),
    description="All trades. Prices in ticks.",
)
ORDERS = _run_spec(
    "orders",
    Column("t", "f", False), Column("order_id", "i", False), Column("agent", "i", False), Column("asset", "i", False),
    Column("side", "i", False), Column("qty", "i", False), Column("limit", "i", False), Column("tif", "i", False),
    Column("status", "i", False), Column("filled_qty", "i", False),
    description="All submitted orders with their final status.",
)
BOOK = _run_spec(
    "book",
    Column("t", "f", False), Column("asset", "i", False), Column("best_bid", "i", False),
    Column("best_ask", "i", False), Column("bid_qty", "i", False), Column("ask_qty", "i", False),
    Column("depth5_bid", "i", False), Column("depth5_ask", "i", False),
    description="Book snapshots every dt. Empty side: price NO_PRICE, quantity 0.",
)
SIGNALS = _run_spec(
    "signals",
    Column("t", "f", False), Column("agent", "i", False), Column("asset", "i", False),
    Column("own_signal", "f", False), Column("herd_signal", "f", False), Column("final_signal", "f", False),
    description="Agent signals in [-1, 1] at every decision.",
)
VALUATIONS = _run_spec(
    "valuations",
    Column("t", "f", False), Column("asset", "i", False), Column("v_mean", "f", False), Column("v_std", "f", False),
    Column("reference", "f", True),
    description="Mean and std of agent valuations (ticks) and the reference path R, every dt.",
)
EVENTS = _run_spec(
    "events",
    Column("t", "f", False), Column("kind", "s", False), Column("payload", "s", False),
    description="Control events; payload is a JSON string.",
)

RUN_TABLES = (TRADES, ORDERS, BOOK, SIGNALS, VALUATIONS, EVENTS)


@dataclass
class RunLog:
    path: Path
    meta: dict
    tables: dict[str, pd.DataFrame]

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.tables[name]


def validate_meta(meta: dict) -> dict:
    missing = [k for k in META_KEYS if k not in meta]
    if missing:
        raise ContractError(f"meta.json: missing keys {missing}")
    if meta["log_version"] != LOG_VERSION:
        raise ContractError(f"meta.json: log_version {meta['log_version']} != {LOG_VERSION}")
    return meta


def load_run(run_dir: str | Path) -> RunLog:
    """Read and validate a run directory."""
    d = Path(run_dir)
    meta = validate_meta(json.loads((d / "meta.json").read_text(encoding="utf-8")))
    tables = {}
    for spec in RUN_TABLES:
        path = d / spec.file
        if not path.exists():
            raise ContractError(f"{d}: missing {spec.file}")
        tables[spec.name] = validate(pd.read_parquet(path), spec)
    return RunLog(d, meta, tables)
