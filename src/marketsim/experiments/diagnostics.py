"""F4 pilot diagnostics read only K3. Formal research metrics remain owned by D1."""

from __future__ import annotations

import numpy as np

from marketsim.market.api import NO_PRICE
from marketsim.sim.log_schema import RunLog


def diagnose_run(log: RunLog) -> dict:
    """Summarize post-warmup activity and sampled quote behavior, with explicit missing values.

    Drawdown and valuation error use only two-sided midpoint observations. Execution rate refers
    to the cohort of orders submitted after warmup and their eventual filled quantities.
    """
    cfg = log.meta["config"]
    warmup, end = cfg["warmup_sessions"], cfg["n_sessions"]
    all_book = log["book"]
    all_valid = (all_book.best_bid != NO_PRICE) & (all_book.best_ask != NO_PRICE)
    # A snapshot represents [t, next_t). Retain the part crossing an off-grid warmup.
    times = all_book.t.to_numpy()
    durations = np.diff(np.clip(np.append(times, end), warmup, end))
    duration = float(durations.sum())
    book = all_book.loc[lambda d: (d.t >= warmup) & (d.t <= end)]
    trades = log["trades"].loc[lambda d: d.t >= warmup]
    orders = log["orders"].loc[lambda d: d.t >= warmup]
    valid = (book.best_bid != NO_PRICE) & (book.best_ask != NO_PRICE)
    quotes = book.loc[valid]
    prices = (quotes.best_bid.to_numpy() + quotes.best_ask.to_numpy()) / 2
    values = log["valuations"].loc[lambda d: d.t >= warmup, "v_mean"]
    mean_value = float(values.iloc[0]) if len(values) else float("nan")
    return {
        "run_id": log.meta["run_id"], "seed": log.meta["seed"], "wall_time_s": log.meta["wall_time_s"],
        "trade_count": len(trades), "traded_shares": int(trades.qty.sum()), "order_count": len(orders),
        "empty_side_fraction": float(np.sum(durations * (~all_valid).to_numpy()) / duration) if duration else float("nan"),
        "mean_spread_ticks": float((quotes.best_ask - quotes.best_bid).mean()) if len(quotes) else float("nan"),
        "mean_depth5": float((book.depth5_bid + book.depth5_ask).mean()) if len(book) else float("nan"),
        "max_drawdown": float(np.max(1 - prices / np.maximum.accumulate(prices))) if len(prices) else float("nan"),
        "relative_valuation_error": float(np.mean(np.abs(prices - mean_value) / mean_value)) if len(prices) else float("nan"),
        "execution_rate": float(orders.filled_qty.sum() / orders.qty.sum()) if orders.qty.sum() else float("nan"),
    }
