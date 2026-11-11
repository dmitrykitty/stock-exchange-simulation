"""Wave metrics and price-series helper tested on hand-built logs and the explainer §8.2 example."""

import math
import statistics

import pandas as pd
import pytest

from marketsim import paths
from marketsim.market.api import NO_PRICE
from marketsim.metrics import (
    chain_amplification,
    price_series,
    reference_series,
    shock_time,
    wave_metrics,
    wave_summary,
)
from marketsim.sim.log_schema import RunLog


def _make_log(book_rows, val_rows, trade_rows=(), assets=("VLO",), warmup=1, n_sessions=7):
    book = pd.DataFrame(book_rows, columns=["t", "asset", "best_bid", "best_ask"]).assign(
        bid_qty=10, ask_qty=10, depth5_bid=50, depth5_ask=50,
    ).astype({"t": float})
    valuations = pd.DataFrame(val_rows, columns=["t", "asset", "reference"]).assign(
        v_mean=lambda d: d["reference"], v_std=0.0,
    ).astype({"t": float, "reference": float})
    trades = pd.DataFrame(trade_rows, columns=["t", "seq", "asset", "price"]).assign(
        qty=1, taker_owner=0, maker_owner=1, taker_side=1,
    ).astype({"t": float})
    orders = pd.DataFrame(columns=["t", "order_id", "agent", "asset", "side", "qty", "limit", "tif",
                                   "status", "filled_qty"])
    meta = {
        "run_id": "wave_hand", "seed": 42, "assets": list(assets),
        "config": {"warmup_sessions": warmup, "n_sessions": n_sessions},
    }
    return RunLog(paths.PROJECT_ROOT, meta, {
        "book": book, "valuations": valuations, "trades": trades, "orders": orders,
    })


def test_explainer_section_8_2_numerical_example():
    """Numerical example from docs/model/explainer.md §8.2 (VLO after oil +20% shock).

    Pre-shock (t=0, before shock at t=1): R = 100.00 (10000 ticks), P = 100.00 (10000 ticks).
    Steps 0..6 after shock (t = 1..7):
      R = [108.7, 108.7, 108.7, 108.7, 108.7, 108.7, 108.7] USD -> 10870 ticks
      P = [100,   102,   107,   112,   110,   109,   108.7] USD -> ticks * 100
    """
    p_ticks = [10000, 10000, 10200, 10700, 11200, 11000, 10900, 10870]
    r_ticks = [10000, 10870, 10870, 10870, 10870, 10870, 10870, 10870]
    times = list(range(len(p_ticks)))  # t = 0 is pre-shock; shock at t = 1; steps 0..6 are t = 1..7
    book_rows = [(t, 0, p - 5, p + 5) for t, p in zip(times, p_ticks, strict=True)]
    val_rows = [(t, 0, r) for t, r in zip(times, r_ticks, strict=True)]
    log = _make_log(book_rows, val_rows, assets=("VLO",), warmup=1, n_sessions=7)

    assert shock_time(log) == 1.0
    res = wave_metrics(log, 0)
    assert res["completeness"] == pytest.approx(1.0)
    assert res["overshoot"] == pytest.approx((112.0 - 108.7) / 108.7)  # ~3.04%
    assert res["lag_sessions"] == pytest.approx(2.0)
    # Error at step 0 is 8.7/108.7 (~8.0%), half is 4.35/108.7 (~4.0%).
    # Step 1 error is 6.7/108.7, step 2 error is 1.7/108.7 -> crosses half at 1 + (6.7 - 4.35) / (6.7 - 1.7) = 1.47.
    assert res["error_half_life_sessions"] == pytest.approx(1.47)


def test_three_asset_chain_amplification_and_summary():
    """Chain COP (0) -> VLO (1) -> DAL (2), where DAL moves down and amplifies the wave 1.5x."""
    times = [0, 1, 2, 3]
    # Asset 0 (COP): R 10000 -> 11000, P 10000 -> 11000 (completeness = 1.0)
    # Asset 1 (VLO): R 10000 -> 10800, P 10000 -> 10400 (damped)
    # Asset 2 (DAL): R 10000 -> 9900 (down), P 10000 -> 9800 (overshoots downward to 9700 at t=2)
    paths_p = {
        0: [10000, 10000, 10600, 11000],
        1: [10000, 10000, 10200, 10400],
        2: [10000, 10000, 9700, 9850],
    }
    paths_r = {
        0: [10000, 11000, 11000, 11000],
        1: [10000, 10800, 10800, 10800],
        2: [10000, 9900, 9900, 9900],
    }
    book_rows = [(t, a, p - 5, p + 5) for a, ps in paths_p.items() for t, p in zip(times, ps, strict=True)]
    val_rows = [(t, a, r) for a, rs in paths_r.items() for t, r in zip(times, rs, strict=True)]
    log = _make_log(book_rows, val_rows, assets=("COP", "VLO", "DAL"), warmup=1, n_sessions=3)

    dal = wave_metrics(log, 2)
    expected_dal_comp = math.log(9850 / 10000) / math.log(9900 / 10000)
    assert dal["completeness"] == pytest.approx(expected_dal_comp)
    assert dal["overshoot"] == pytest.approx((9900 - 9700) / 9900)
    assert chain_amplification(log) == pytest.approx(expected_dal_comp / 1.0)

    summary = wave_summary(log)
    assert summary["shock_time"] == 1.0
    assert summary["completeness__COP"] == pytest.approx(1.0)
    assert summary["completeness__DAL"] == pytest.approx(expected_dal_comp)
    assert summary["chain_amplification"] == pytest.approx(expected_dal_comp)


def test_price_series_empty_book_policies():
    """Midpoint with explicit fallback when a book side is empty."""
    book_rows = [
        (0.0, 0, NO_PRICE, 10010),  # empty bid before any trade -> nan
        (1.0, 0, 9990, 10010),      # two-sided: mid = 10000
        (2.0, 0, NO_PRICE, 10050),  # empty bid: last trade at t=1.5 is 10030; hold is 10000
        (3.0, 0, 10090, 10110),     # two-sided: mid = 10100
    ]
    trades = [
        (1.5, 0, 0, 10020),
        (1.5, 1, 0, 10030),  # second trade at same t=1.5 wins by seq order
    ]
    log = _make_log(book_rows, [(0.0, 0, 10000.0)], trade_rows=trades, warmup=0, n_sessions=3)

    s_trade = price_series(log, 0, kind="mid", empty="last_trade")
    assert math.isnan(s_trade.loc[0.0])
    assert list(s_trade.loc[[1.0, 2.0, 3.0]]) == pytest.approx([10000.0, 10030.0, 10100.0])

    s_hold = price_series(log, 0, kind="mid", empty="hold")
    assert math.isnan(s_hold.loc[0.0])
    assert list(s_hold.loc[[1.0, 2.0, 3.0]]) == pytest.approx([10000.0, 10000.0, 10100.0])

    s_nan = price_series(log, 0, kind="mid", empty="nan")
    assert math.isnan(s_nan.loc[0.0]) and math.isnan(s_nan.loc[2.0])
    assert list(s_nan.loc[[1.0, 3.0]]) == pytest.approx([10000.0, 10100.0])

    s_last = price_series(log, 0, kind="last_trade")
    assert math.isnan(s_last.loc[0.0]) and math.isnan(s_last.loc[1.0])
    assert list(s_last.loc[[2.0, 3.0]]) == pytest.approx([10030.0, 10030.0])

    with pytest.raises(ValueError):
        price_series(log, 0, empty="bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        price_series(log, 0, kind="bad")  # type: ignore[arg-type]


def test_volatility_amplification_and_end_window():
    times = [0, 1, 2, 3, 4]
    r_vals = [10000, 10200, 10100, 10300, 10300]
    p_vals = [10000, 10400, 10000, 10600, 10200]
    book_rows = [(t, 0, p - 5, p + 5) for t, p in zip(times, p_vals, strict=True)]
    val_rows = [(t, 0, r) for t, r in zip(times, r_vals, strict=True)]
    log = _make_log(book_rows, val_rows, warmup=1, n_sessions=4)

    res = wave_metrics(log, 0, t0=1.0, end_window=1.0)
    rp = [math.log(p_vals[i + 1] / p_vals[i]) for i in range(len(p_vals) - 1)]
    rr = [math.log(r_vals[i + 1] / r_vals[i]) for i in range(len(r_vals) - 1)]
    assert res["volatility_amplification"] == pytest.approx(statistics.stdev(rp) / statistics.stdev(rr))
    # end_window=1.0 averages t in [3, 4] -> mean(ln(10600), ln(10200))
    expected_dp = (math.log(10600) + math.log(10200)) / 2 - math.log(10000)
    expected_dr = math.log(10300) - math.log(10000)
    assert res["completeness"] == pytest.approx(expected_dp / expected_dr)
    assert len(reference_series(log, 0)) == 5


def test_flat_reference_gives_nan_wave_metrics():
    times = [0, 1, 2, 3]
    book_rows = [(t, 0, 9995, 10005) for t in times]
    val_rows = [(t, 0, 10000) for t in times]
    log = _make_log(book_rows, val_rows, warmup=1, n_sessions=3)

    assert math.isnan(shock_time(log))
    res = wave_metrics(log, 0)
    assert all(math.isnan(v) for v in res.values())
