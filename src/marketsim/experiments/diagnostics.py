"""Run diagnostics for experiment summaries; they read only K3. Formal research metrics remain owned by D1."""

from __future__ import annotations

import math

import numpy as np

from marketsim.market.api import NO_PRICE
from marketsim.sim.log_schema import RunLog


def _quote_fields(book, warmup: float, end: float) -> dict:
    """Book-based fields of one asset's snapshots (all snapshots, including warmup, for time weighting)."""
    valid_all = (book.best_bid != NO_PRICE) & (book.best_ask != NO_PRICE)
    # A snapshot represents [t, next_t). Retain the part crossing an off-grid warmup.
    times = book.t.to_numpy()
    durations = np.diff(np.clip(np.append(times, end), warmup, end))
    duration = float(durations.sum())
    window = book.loc[(book.t >= warmup) & (book.t <= end)]
    quotes = window.loc[(window.best_bid != NO_PRICE) & (window.best_ask != NO_PRICE)]
    prices = (quotes.best_bid.to_numpy() + quotes.best_ask.to_numpy()) / 2
    return {
        "empty_side_fraction": float(np.sum(durations * (~valid_all).to_numpy()) / duration) if duration else float("nan"),
        "mean_spread_ticks": float((quotes.best_ask - quotes.best_bid).mean()) if len(quotes) else float("nan"),
        "mean_depth5": float((window.depth5_bid + window.depth5_ask).mean()) if len(window) else float("nan"),
        "max_drawdown": float(np.max(1 - prices / np.maximum.accumulate(prices))) if len(prices) else float("nan"),
        "_prices": prices,
    }


def diagnose_run(log: RunLog) -> dict:
    """Summarize post-warmup activity and sampled quote behavior, with explicit missing values.

    Drawdown and valuation error use only two-sided midpoint observations. Execution rate refers
    to the cohort of orders submitted after warmup and their eventual filled quantities.

    One asset (F4): the original single-asset fields. Several assets (F5+): run-wide activity fields plus, per
    asset, ``<field>__<TICKER>`` for the quote fields and two raw wave diagnostics: ``price_log_change`` (last
    two-sided midpoint vs the last two-sided midpoint before warmup ends) and ``reference_log_change`` (reference
    path at the same two snapshots). A shock that starts when warmup ends is therefore inside the change. Their
    ratio is a quick look at transmission; the formal wave metrics are D1's.
    """
    cfg = log.meta["config"]
    warmup, end = cfg["warmup_sessions"], cfg["n_sessions"]
    trades = log["trades"].loc[lambda d: d.t >= warmup]
    orders = log["orders"].loc[lambda d: d.t >= warmup]
    activity = {}
    # Population is optional metadata beyond K3; generic K3 logs still support the base diagnostics.
    population = log.meta.get("population")
    if population is not None:
        funds = set(population["fund_ids"])
        investors = {i for i, kind in enumerate(population["kinds"]) if kind != "market_maker"}
        for group, ids in (("fund", investors & funds), ("retail", investors - funds)):
            submitted = orders.loc[orders.agent.isin(ids)]
            activity[f"{group}_agent_count"] = len(ids)
            activity[f"{group}_order_count"] = len(submitted)
            activity[f"{group}_active_agents"] = int(submitted.agent.nunique())
    row = {
        "run_id": log.meta["run_id"], "seed": log.meta["seed"], "wall_time_s": log.meta["wall_time_s"],
        "trade_count": len(trades), "traded_shares": int(trades.qty.sum()), "order_count": len(orders),
    }
    execution = float(orders.filled_qty.sum() / orders.qty.sum()) if orders.qty.sum() else float("nan")
    assets = log.meta.get("assets") or []
    if len(assets) <= 1:
        quote = _quote_fields(log["book"], warmup, end)
        prices = quote.pop("_prices")
        values = log["valuations"].loc[lambda d: d.t >= warmup, "v_mean"]
        mean_value = float(values.iloc[0]) if len(values) else float("nan")
        return {
            **row, **quote,
            "relative_valuation_error": float(np.mean(np.abs(prices - mean_value) / mean_value)) if len(prices) else float("nan"),
            "execution_rate": execution, **activity,
        }
    row["execution_rate"] = execution
    book, valuations = log["book"], log["valuations"]
    for asset, ticker in enumerate(assets):
        own = book.loc[book.asset == asset]
        quote = _quote_fields(own, warmup, end)
        quote.pop("_prices")
        for name, value in quote.items():
            row[f"{name}__{ticker}"] = value
        two_sided = own.loc[(own.best_bid != NO_PRICE) & (own.best_ask != NO_PRICE)]
        before = two_sided.loc[two_sided.t < warmup]
        ref = valuations.loc[valuations.asset == asset].set_index("t")["reference"]
        if len(before) and len(two_sided):
            first, last = before.iloc[-1], two_sided.iloc[-1]
            mid_first = (first.best_bid + first.best_ask) / 2
            mid_last = (last.best_bid + last.best_ask) / 2
            row[f"price_log_change__{ticker}"] = float(math.log(mid_last / mid_first))
            row[f"reference_log_change__{ticker}"] = float(math.log(ref.loc[last.t] / ref.loc[first.t]))
        else:
            row[f"price_log_change__{ticker}"] = row[f"reference_log_change__{ticker}"] = float("nan")
    return {**row, **activity}
