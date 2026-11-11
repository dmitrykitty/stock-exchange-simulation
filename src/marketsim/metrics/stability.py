"""Single-asset stability and liquidity metrics (phase F4), computed per asset from a K3 run log.

Every function takes a ``RunLog`` (``marketsim.sim.log_schema.load_run``) and an asset index (the
position of the ticker in ``meta["assets"]``) and returns a float; ``nan`` means "not defined for
this run" (for example, no two-sided quotes). ``stability_metrics`` collects all of them into one
row per asset.

Conventions shared by all metrics:

- Measurement window: ``[warmup_sessions, n_sessions]`` from ``meta["config"]``; events before the
  warmup are ignored. Without these keys the window is the whole log.
- A book snapshot describes the book on ``[t_k, t_{k+1})``; the last one lasts until the window end.
- Price ``P`` is the midpoint ``(best_bid + best_ask) / 2`` of snapshots where both sides are
  present. An empty side is never interpolated into a price.

Metric list: docs/plans/phases/F4_single_asset_market.md, section 5. The exact definitions are the
docstrings below (quote impact and volatility have no formula in docs/model/model_description.md yet).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from marketsim.market.api import NO_PRICE
from marketsim.sim.log_schema import RunLog

__all__ = [
    "assets",
    "empty_side_fraction",
    "execution_rate",
    "max_drawdown",
    "mean_depth5",
    "mean_spread",
    "quote_impact",
    "stability_metrics",
    "valuation_error",
    "volatility",
    "window",
]

NAN = float("nan")


# ---------------------------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------------------------

def assets(log: RunLog) -> list[int]:
    """Asset indices of the run: ``range(len(meta["assets"]))``, falling back to the book column."""
    names = log.meta.get("assets")
    if names is not None:
        return list(range(len(names)))
    return sorted(int(a) for a in log["book"]["asset"].unique())


def window(log: RunLog) -> tuple[float, float]:
    """Measurement window ``(start, end)`` in sessions: post-warmup part of the run."""
    cfg = log.meta.get("config", {})
    start = float(cfg.get("warmup_sessions", 0.0))
    if "n_sessions" in cfg:
        end = float(cfg["n_sessions"])
    else:
        times = [log[name]["t"].max() for name in ("book", "orders", "trades") if len(log[name])]
        end = float(max(times)) if times else start
    return start, end


def _rows(log: RunLog, table: str, asset: int) -> pd.DataFrame:
    df = log[table]
    if "asset" in df.columns:
        df = df.loc[df["asset"] == asset]
    return df.sort_values("t", kind="stable")


def _book(log: RunLog, asset: int) -> pd.DataFrame:
    """All snapshots of the asset (warmup included: the one before the window start still counts)."""
    return _rows(log, "book", asset)


def _two_sided(book: pd.DataFrame) -> np.ndarray:
    return ((book["best_bid"] != NO_PRICE) & (book["best_ask"] != NO_PRICE)).to_numpy()


def _quotes(log: RunLog, asset: int) -> pd.DataFrame:
    """Two-sided snapshots inside the window with a ``mid`` column (ticks, float)."""
    start, end = window(log)
    book = _book(log, asset)
    book = book.loc[_two_sided(book) & (book["t"] >= start).to_numpy() & (book["t"] <= end).to_numpy()]
    return book.assign(mid=(book["best_bid"].to_numpy() + book["best_ask"].to_numpy()) / 2)


def _in_window(log: RunLog, table: str, asset: int) -> pd.DataFrame:
    start, end = window(log)
    df = _rows(log, table, asset)
    return df.loc[(df["t"] >= start) & (df["t"] <= end)]


# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------

def max_drawdown(log: RunLog, asset: int) -> float:
    """Deepest relative fall of the midpoint from its running maximum: ``max_t (1 - P_t / max_{s<=t} P_s)``.

    In ``[0, 1)``; 0 for a never-falling price.
    """
    prices = _quotes(log, asset)["mid"].to_numpy()
    if not len(prices):
        return NAN
    return float(np.max(1.0 - prices / np.maximum.accumulate(prices)))


def empty_side_fraction(log: RunLog, asset: int) -> float:
    """Share of window time during which at least one side of the book is empty.

    Snapshots are weighted by how long they are valid inside the window, so a snapshot taken before
    the warmup ends still covers the beginning of the window.
    """
    start, end = window(log)
    book = _book(log, asset)
    if not len(book) or end <= start:
        return NAN
    times = book["t"].to_numpy(dtype=float)
    durations = np.diff(np.clip(np.append(times, end), start, end))
    total = durations.sum()
    if total <= 0:
        return NAN
    return float(np.sum(durations * ~_two_sided(book)) / total)


def mean_spread(log: RunLog, asset: int) -> float:
    """Mean ``best_ask - best_bid`` in ticks over two-sided snapshots in the window."""
    quotes = _quotes(log, asset)
    if not len(quotes):
        return NAN
    return float((quotes["best_ask"] - quotes["best_bid"]).mean())


def mean_depth5(log: RunLog, asset: int) -> float:
    """Mean number of shares on the 5 best levels of both sides (``depth5_bid + depth5_ask``).

    All snapshots in the window are used; an empty side contributes 0.
    """
    book = _in_window(log, "book", asset)
    if not len(book):
        return NAN
    return float((book["depth5_bid"] + book["depth5_ask"]).mean())


def execution_rate(log: RunLog, asset: int) -> float:
    """Filled share of the quantity of orders submitted in the window: ``sum(filled_qty) / sum(qty)``.

    Uses the final status of each order, so fills after the order was submitted count; rejected
    orders count as unfilled.
    """
    orders = _in_window(log, "orders", asset)
    total = orders["qty"].sum()
    if not total:
        return NAN
    return float(orders["filled_qty"].sum() / total)


def valuation_error(log: RunLog, asset: int) -> float:
    """Mean relative distance of the midpoint from the agents' mean valuation: ``mean |P_t - V_t| / V_t``.

    ``V_t`` is the latest ``valuations.v_mean`` of the asset at or before the snapshot time.
    """
    quotes = _quotes(log, asset)
    values = _rows(log, "valuations", asset)[["t", "v_mean"]]
    if not len(quotes) or not len(values):
        return NAN
    joined = pd.merge_asof(quotes[["t", "mid"]].astype({"t": float}), values.astype({"t": float}),
                           on="t", direction="backward").dropna(subset=["v_mean"])
    joined = joined.loc[joined["v_mean"] > 0]
    if not len(joined):
        return NAN
    return float(np.mean(np.abs(joined["mid"] - joined["v_mean"]) / joined["v_mean"]))


def quote_impact(log: RunLog, asset: int) -> float:
    """How far the best quotes move per share of aggressive order flow (Kyle's lambda), ticks/share.

    Between two consecutive two-sided snapshots ``k`` and ``k+1`` in the window, take the midpoint
    change ``dP_k`` and the signed taker volume ``Q_k = sum(taker_side * qty)`` of trades with
    ``t_k <= t < t_{k+1}`` (buys positive). The metric is the OLS slope of ``dP_k`` on ``Q_k`` (with
    intercept) over all such intervals. Positive: buying pushes quotes up. ``nan`` with fewer than
    two intervals or no variation in order flow.
    """
    quotes = _quotes(log, asset)
    if len(quotes) < 3:
        return NAN
    times = quotes["t"].to_numpy(dtype=float)
    trades = _rows(log, "trades", asset)
    interval = np.searchsorted(times, trades["t"].to_numpy(dtype=float), side="right") - 1
    inside = (interval >= 0) & (interval < len(times) - 1)
    signed = (trades["taker_side"].to_numpy() * trades["qty"].to_numpy())[inside].astype(float)
    flow = np.bincount(interval[inside], weights=signed, minlength=len(times) - 1)
    moves = np.diff(quotes["mid"].to_numpy())
    flow_dev = flow - flow.mean()
    var = float(np.dot(flow_dev, flow_dev))
    if var == 0:
        return NAN
    return float(np.dot(flow_dev, moves - moves.mean()) / var)


def volatility(log: RunLog, asset: int, horizon: float | None = None) -> float:
    """Standard deviation (ddof=1) of midpoint log returns ``ln(P_{k+1} / P_k)``.

    ``horizon=None``: returns between consecutive two-sided snapshots (the log sampling step
    ``sample_dt``). ``horizon=h``: the midpoint is read every ``h`` sessions from the window start
    (latest two-sided snapshot at or before each grid point), e.g. ``h=1`` gives per-session
    volatility. ``nan`` with fewer than two returns.
    """
    quotes = _quotes(log, asset)
    if horizon is not None:
        if not horizon > 0:
            raise ValueError("horizon must be positive")
        start, end = window(log)
        grid = pd.DataFrame({"t": start + horizon * np.arange(int(np.floor((end - start) / horizon + 1e-9)) + 1)})
        quotes = pd.merge_asof(grid, quotes[["t", "mid"]].astype({"t": float}), on="t",
                               direction="backward").dropna(subset=["mid"])
    returns = np.diff(np.log(quotes["mid"].to_numpy(dtype=float)))
    if len(returns) < 2:
        return NAN
    return float(np.std(returns, ddof=1))


METRICS = {
    "max_drawdown": max_drawdown,
    "empty_side_fraction": empty_side_fraction,
    "mean_spread_ticks": mean_spread,
    "mean_depth5": mean_depth5,
    "execution_rate": execution_rate,
    "valuation_error": valuation_error,
    "quote_impact": quote_impact,
    "volatility": volatility,
}


def stability_metrics(log: RunLog) -> pd.DataFrame:
    """One row per asset: ``run_id``, ``seed``, ``asset``, ``ticker`` and every metric of this module."""
    names = log.meta.get("assets")
    rows = []
    for asset in assets(log):
        row = {"run_id": log.meta.get("run_id"), "seed": log.meta.get("seed"), "asset": asset,
               "ticker": names[asset] if names is not None else None}
        row.update({name: fn(log, asset) for name, fn in METRICS.items()})
        rows.append(row)
    return pd.DataFrame(rows)
