"""Price and reference series of one asset from a K3 run log, with explicit empty-book handling.

Every metric that needs "the price of asset i over time" should take it from here, so that all
metrics treat a one-sided book the same way.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from marketsim.market.api import NO_PRICE
from marketsim.sim.log_schema import RunLog

__all__ = ["as_of", "price_series", "reference_series"]

PriceKind = Literal["mid", "last_trade"]
EmptyPolicy = Literal["last_trade", "hold", "nan"]


def _asset_rows(log: RunLog, table: str, asset: int) -> pd.DataFrame:
    df = log[table]
    if "asset" in df.columns:
        df = df.loc[df["asset"] == asset]
    order = ["t", "seq"] if "seq" in df.columns else ["t"]
    return df.sort_values(order, kind="stable")


def as_of(series: pd.Series, times: np.ndarray | pd.Index) -> np.ndarray:
    """Latest non-missing value of ``series`` (indexed by time) at or before each of ``times``.

    ``nan`` before the first valid observation.
    """
    valid = series.dropna()
    times = np.asarray(times, dtype=float)
    if not len(valid):
        return np.full(len(times), np.nan)
    pos = np.searchsorted(valid.index.to_numpy(dtype=float), times, side="right") - 1
    values = valid.to_numpy(dtype=float)[np.clip(pos, 0, None)]
    return np.where(pos >= 0, values, np.nan)


def _last_trade(log: RunLog, asset: int, times: np.ndarray) -> np.ndarray:
    trades = _asset_rows(log, "trades", asset)
    if not len(trades):
        return np.full(len(times), np.nan)
    # Several trades at one time: the last one in sequence order wins.
    prices = trades.groupby("t", sort=True)["price"].last().astype(float)
    return as_of(prices, times)


def price_series(log: RunLog, asset: int, kind: PriceKind = "mid",
                 empty: EmptyPolicy = "last_trade") -> pd.Series:
    """Price of the asset in ticks at every book snapshot time (float, indexed by ``t``).

    ``kind="mid"``: midpoint ``(best_bid + best_ask) / 2`` where both sides are present. What to use
    when a side is empty is chosen explicitly with ``empty``:

    - ``"last_trade"`` (default): price of the last trade at or before the snapshot, as agents
      observe a parent price in F5 (docs/guides/f5_graph_valuers.md);
    - ``"hold"``: the last two-sided midpoint;
    - ``"nan"``: missing.

    ``kind="last_trade"``: price of the last trade at or before each snapshot, whatever the book.

    Values before any usable observation are ``nan``; no price is ever invented.
    """
    book = _asset_rows(log, "book", asset)
    times = book["t"].to_numpy(dtype=float)
    if kind == "last_trade":
        values = _last_trade(log, asset, times)
    elif kind == "mid":
        bid, ask = book["best_bid"].to_numpy(), book["best_ask"].to_numpy()
        two_sided = (bid != NO_PRICE) & (ask != NO_PRICE)
        mid = np.where(two_sided, (bid.astype(float) + ask.astype(float)) / 2, np.nan)
        if empty == "nan":
            values = mid
        elif empty == "hold":
            values = pd.Series(mid).ffill().to_numpy()
        elif empty == "last_trade":
            values = np.where(two_sided, mid, _last_trade(log, asset, times))
        else:
            raise ValueError(f"unknown empty-side policy {empty!r}")
    else:
        raise ValueError(f"unknown price kind {kind!r}")
    return pd.Series(values, index=pd.Index(times, name="t"), name="price")


def reference_series(log: RunLog, asset: int) -> pd.Series:
    """Reference path R of the asset in ticks (``valuations.reference``), indexed by ``t``; missing rows dropped."""
    rows = _asset_rows(log, "valuations", asset)
    values = pd.to_numeric(rows["reference"], errors="coerce").to_numpy(dtype=float)
    series = pd.Series(values, index=pd.Index(rows["t"].to_numpy(dtype=float), name="t"), name="reference")
    return series.dropna()
