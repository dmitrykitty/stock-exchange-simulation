"""Single consumer of fills, maintaining both sides' order totals and valid K3 tables."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.data.api import write_table
from marketsim.market.api import MarketDataView, Report, Status
from marketsim.sim.log_schema import RUN_TABLES


class Recorder:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {spec.name: [] for spec in RUN_TABLES}
        self._orders: dict[int, dict] = {}

    def event(self, now: float, kind: str, payload: dict) -> None:
        """Record a deterministic JSON payload, including lifecycle states absent in K1."""
        self.rows["events"].append({
            "t": now, "kind": kind, "payload": json.dumps(payload, sort_keys=True, allow_nan=False),
        })

    def order(self, now: float, agent: int, side: int, qty: int, limit: int, tif: int, report: Report) -> None:
        """Register before draining fills, so taker and maker rows can both be updated."""
        row = {
            "t": now, "order_id": report.order_id, "agent": agent, "asset": 0,
            "side": side, "qty": qty, "limit": limit, "tif": tif,
            "status": int(report.status), "filled_qty": 0,
        }
        self.rows["orders"].append(row)
        if report.order_id:
            self._orders[report.order_id] = row

    def fills(self, now: float, fills: np.ndarray) -> list[int]:
        """Update orders once per fill; return newly fully filled order IDs."""
        completed = []
        for fill in fills:
            self.rows["trades"].append({
                "t": now, **{name: int(fill[name]) for name in (
                    "seq", "asset", "price", "qty", "taker_owner", "maker_owner", "taker_side",
                )},
            })
            for key in ("taker_order", "maker_order"):
                order_id = int(fill[key])
                row = self._orders[order_id]
                row["filled_qty"] += int(fill["qty"])
                if row["filled_qty"] == row["qty"]:
                    row["status"] = int(Status.FILLED)
                    completed.append(order_id)
                elif row["status"] == int(Status.RESTING):
                    row["status"] = int(Status.PARTIAL_RESTING)
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

    def write(self, directory: Path, meta: dict) -> None:
        """Write every table with explicit dtypes, including tables with zero rows."""
        dtypes = {"f": "float64", "i": "int64", "s": "string"}
        for spec in RUN_TABLES:
            rows = self.rows[spec.name]
            frame = pd.DataFrame({
                c.name: pd.Series([row[c.name] for row in rows], dtype=dtypes[c.kind]) for c in spec.columns
            })
            write_table(frame, spec, directory / spec.file)
        (directory / "meta.json").write_text(
            json.dumps(meta, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )
