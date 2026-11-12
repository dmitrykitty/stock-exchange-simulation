"""Single consumer of fills, maintaining both sides' order totals and valid K3 tables."""

from __future__ import annotations

import json
from array import array
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.data.api import write_table
from marketsim.data.schemas import TableSpec
from marketsim.market.api import MarketDataView, Report, Status
from marketsim.sim.log_schema import RUN_TABLES

_DTYPES = {"f": "float64", "i": "int64", "s": "string"}


class _Rows:
    """Rows of one K3 table stored by column: 8 bytes per number instead of a Python dict per row.

    A two-year run on 18 assets logs millions of rows; dict rows would need several gigabytes per process.
    """

    def __init__(self, spec: TableSpec) -> None:
        self.spec = spec
        self.columns = {c.name: array("d") if c.kind == "f" else array("q") if c.kind == "i" else []
                        for c in spec.columns}
        self._appenders = [(c.name, self.columns[c.name].append) for c in spec.columns]

    def append(self, row: dict) -> int:
        """Store a row given as a dict with every column of the table; return its position."""
        for name, append in self._appenders:
            append(row[name])
        return len(self) - 1

    def __len__(self) -> int:
        return len(self.columns[self.spec.columns[0].name])

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            c.name: pd.Series(self.columns[c.name] if c.kind == "s" else np.frombuffer(
                self.columns[c.name], dtype=_DTYPES[c.kind]).copy(), dtype=_DTYPES[c.kind])
            for c in self.spec.columns
        })


class Recorder:
    def __init__(self) -> None:
        self.rows: dict[str, _Rows] = {spec.name: _Rows(spec) for spec in RUN_TABLES}
        self._orders: dict[int, int] = {}  # order id -> position in the orders table

    def event(self, now: float, kind: str, payload: dict) -> None:
        """Record a deterministic JSON payload, including lifecycle states absent in K1."""
        self.rows["events"].append({
            "t": now, "kind": kind, "payload": json.dumps(payload, sort_keys=True, allow_nan=False),
        })

    def order(
        self, now: float, agent: int, side: int, qty: int, limit: int, tif: int, report: Report, asset: int = 0,
    ) -> None:
        """Register before draining fills, so taker and maker rows can both be updated."""
        row = {
            "t": now, "order_id": report.order_id, "agent": agent, "asset": asset,
            "side": side, "qty": qty, "limit": limit, "tif": tif,
            "status": int(report.status), "filled_qty": 0,
        }
        position = self.rows["orders"].append(row)
        if report.order_id:
            self._orders[report.order_id] = position

    def fills(self, now: float, fills: np.ndarray) -> list[int]:
        """Update orders once per fill; return newly fully filled order IDs."""
        completed = []
        orders = self.rows["orders"].columns
        qty, filled, status = orders["qty"], orders["filled_qty"], orders["status"]
        for fill in fills:
            self.rows["trades"].append({
                "t": now, **{name: int(fill[name]) for name in (
                    "seq", "asset", "price", "qty", "taker_owner", "maker_owner", "taker_side",
                )},
            })
            for key in ("taker_order", "maker_order"):
                order_id = int(fill[key])
                row = self._orders[order_id]
                filled[row] += int(fill["qty"])
                if filled[row] == qty[row]:
                    status[row] = int(Status.FILLED)
                    completed.append(order_id)
                elif status[row] == int(Status.RESTING):
                    status[row] = int(Status.PARTIAL_RESTING)
        return completed

    def snapshot(self, now: float, market: MarketDataView, valuations: np.ndarray, initial_price: int) -> None:
        """Capture the observable book plus constant F4 valuations/reference."""
        top = market.top(0)
        depth = market.depth(0, 5)
        self.rows["book"].append({
            "t": now, "asset": 0, "best_bid": top.bid, "best_ask": top.ask,
            "bid_qty": top.bid_qty, "ask_qty": top.ask_qty,
            "depth5_bid": int(depth["bid_qty"].sum()), "depth5_ask": int(depth["ask_qty"].sum()),
        })
        self.rows["valuations"].append({
            "t": now, "asset": 0,
            "v_mean": float(valuations.mean()) if len(valuations) else float(initial_price),
            "v_std": float(valuations.std()) if len(valuations) else 0.0,
            "reference": float(initial_price),
        })

    def snapshot_assets(
        self, now: float, market: MarketDataView, v_mean: np.ndarray, v_std: np.ndarray, reference: np.ndarray,
    ) -> None:
        """Book, valuation mean/std (ticks) and reference path (ticks) of every asset at one moment (F5+)."""
        for asset in range(len(v_mean)):
            top = market.top(asset)
            depth = market.depth(asset, 5)
            self.rows["book"].append({
                "t": now, "asset": asset, "best_bid": top.bid, "best_ask": top.ask,
                "bid_qty": top.bid_qty, "ask_qty": top.ask_qty,
                "depth5_bid": int(depth["bid_qty"].sum()), "depth5_ask": int(depth["ask_qty"].sum()),
            })
            self.rows["valuations"].append({
                "t": now, "asset": asset, "v_mean": float(v_mean[asset]), "v_std": float(v_std[asset]),
                "reference": float(reference[asset]),
            })

    def write(self, directory: Path, meta: dict) -> None:
        """Write every table with explicit dtypes, including tables with zero rows."""
        for spec in RUN_TABLES:
            write_table(self.rows[spec.name].frame(), spec, directory / spec.file)
        (directory / "meta.json").write_text(
            json.dumps(meta, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )
