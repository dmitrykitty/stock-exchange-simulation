"""Single-asset metrics on small hand-built K3 logs (two assets, values computed by hand)."""

import itertools
import math
import statistics
import subprocess
import sys

import pandas as pd
import pytest
import yaml

from marketsim import paths
from marketsim.market.api import NO_PRICE
from marketsim.metrics import stability as m
from marketsim.sim.log_schema import RunLog

NAN_METRICS = (m.max_drawdown, m.empty_side_fraction, m.mean_spread, m.mean_depth5, m.execution_rate,
               m.valuation_error, m.quote_impact, m.volatility)


def _book(asset, t, mids=None, bid=None, ask=None, depth_bid=None, depth_ask=None):
    if mids is not None:
        bid, ask = [p - 50 for p in mids], [p + 50 for p in mids]
    n = len(t)
    return pd.DataFrame({
        "t": [float(x) for x in t], "asset": [asset] * n, "best_bid": bid, "best_ask": ask,
        "bid_qty": [1] * n, "ask_qty": [1] * n,
        "depth5_bid": depth_bid or [10] * n, "depth5_ask": depth_ask or [10] * n,
    })


def _trades(rows):
    """rows: (t, asset, taker_side, qty)."""
    df = pd.DataFrame(rows, columns=["t", "asset", "taker_side", "qty"])
    return df.assign(seq=range(len(df)), price=10000, taker_owner=0, maker_owner=1).astype({"t": float})


def _orders(rows):
    """rows: (t, asset, qty, filled_qty)."""
    df = pd.DataFrame(rows, columns=["t", "asset", "qty", "filled_qty"])
    return df.assign(order_id=range(len(df)), agent=0, side=1, limit=10000, tif=0, status=0).astype({"t": float})


def _valuations(rows):
    """rows: (t, asset, v_mean)."""
    return pd.DataFrame(rows, columns=["t", "asset", "v_mean"]).assign(v_std=0.0, reference=None)


@pytest.fixture
def log():
    """Window [1, 4] (warmup 1 of 4 sessions).

    Asset 0 is the hand-checked case of tests/experiments/test_f4_runner.py: one empty-bid snapshot
    and a fall from mid 10000 to 9000. Asset 1 is always two-sided, mids 10000, 10100, 10000, 10200
    in the window, and is driven by signed order flow of exactly 10 ticks per share.
    """
    book = pd.concat([
        _book(0, [0, 1, 2, 3], bid=[1, 9900, NO_PRICE, 8900], ask=[2, 10100, 10100, 9100],
              depth_bid=[1, 20, 0, 10], depth_ask=[1, 30, 30, 10]),
        _book(1, [0, 1, 2, 3, 4], mids=[5000, 10000, 10100, 10000, 10200]),
    ], ignore_index=True)
    trades = _trades([
        (0.5, 1, 1, 500),               # warmup: ignored
        (1.2, 1, 1, 10),                # interval [1, 2): +10 -> +100 ticks
        (1.5, 0, 1, 999),               # other asset: ignored
        (2.5, 1, -1, 10),               # interval [2, 3): -10 -> -100 ticks
        (3.1, 1, 1, 25), (3.9, 1, -1, 5),  # interval [3, 4): +20 -> +200 ticks
        (4.0, 1, 1, 777),               # after the last quote: no interval
    ])
    orders = _orders([(0.5, 0, 1000, 1000), (1.5, 0, 10, 3), (0.5, 1, 9, 9), (2.0, 1, 4, 4), (2.5, 1, 4, 0)])
    valuations = _valuations([(0, 0, 10000.0), (0, 1, 10000.0), (3, 1, 10200.0)])
    meta = {"run_id": "hand", "seed": 7, "assets": ["AAA", "BBB"],
            "config": {"n_sessions": 4, "warmup_sessions": 1}}
    return RunLog(paths.PROJECT_ROOT, meta, {"book": book, "trades": trades, "orders": orders,
                                             "valuations": valuations})


def test_asset0_matches_hand_checked_diagnostics(log):
    assert m.empty_side_fraction(log, 0) == pytest.approx(1 / 3)
    assert m.max_drawdown(log, 0) == pytest.approx(0.1)
    assert m.mean_spread(log, 0) == pytest.approx(200)
    assert m.mean_depth5(log, 0) == pytest.approx((50 + 30 + 20) / 3)
    assert m.execution_rate(log, 0) == pytest.approx(0.3)
    assert m.valuation_error(log, 0) == pytest.approx(0.05)
    # Only two two-sided snapshots: one return, one interval.
    assert math.isnan(m.volatility(log, 0))
    assert math.isnan(m.quote_impact(log, 0))


def test_asset1_is_computed_independently(log):
    assert m.empty_side_fraction(log, 1) == 0
    assert m.max_drawdown(log, 1) == pytest.approx(1 - 10000 / 10100)
    assert m.mean_spread(log, 1) == pytest.approx(100)
    assert m.mean_depth5(log, 1) == pytest.approx(20)
    assert m.execution_rate(log, 1) == pytest.approx(0.5)


def test_valuation_error_uses_valuation_at_snapshot_time(log):
    # V = 10000 until t=3, then 10200; mids 10000, 10100, 10000, 10200 at t = 1..4.
    assert m.valuation_error(log, 1) == pytest.approx((0 + 0.01 + 200 / 10200 + 0) / 4)


def test_quote_impact_is_ticks_per_share_of_signed_flow(log):
    assert m.quote_impact(log, 1) == pytest.approx(10.0)


def test_quote_impact_sign_and_no_flow():
    book = _book(0, [0, 1, 2, 3], mids=[10000, 9900, 10000, 9800])
    base = {"book": book, "orders": _orders([]), "valuations": _valuations([])}
    meta = {"run_id": "x", "seed": 0, "assets": ["A"], "config": {"n_sessions": 3, "warmup_sessions": 0}}
    selling = RunLog(paths.PROJECT_ROOT, meta, base | {"trades": _trades([(0.5, 0, 1, 1), (1.5, 0, -1, 1),
                                                                           (2.5, 0, 1, 1)])})
    assert m.quote_impact(selling, 0) < 0  # quotes moved against the flow
    silent = RunLog(paths.PROJECT_ROOT, meta, base | {"trades": _trades([])})
    assert math.isnan(m.quote_impact(silent, 0))


def test_volatility_per_snapshot_and_per_horizon(log):
    mids = [10000, 10100, 10000, 10200]
    expected = statistics.stdev(math.log(b / a) for a, b in itertools.pairwise(mids))
    assert m.volatility(log, 1) == pytest.approx(expected)
    assert m.volatility(log, 1, horizon=1.0) == pytest.approx(expected)
    # Every half session: the latest quote at or before each grid point is repeated.
    sampled = [10000, 10000, 10100, 10100, 10000, 10000, 10200]
    expected_half = statistics.stdev(math.log(b / a) for a, b in itertools.pairwise(sampled))
    assert m.volatility(log, 1, horizon=0.5) == pytest.approx(expected_half)
    assert math.isnan(m.volatility(log, 1, horizon=2.0))  # grid 1, 3: a single return
    with pytest.raises(ValueError):
        m.volatility(log, 1, horizon=0)


def test_stability_metrics_has_one_row_per_asset(log):
    table = m.stability_metrics(log)
    assert table["asset"].tolist() == [0, 1]
    assert table["ticker"].tolist() == ["AAA", "BBB"]
    assert set(m.METRICS) <= set(table.columns)
    assert table.loc[1, "quote_impact"] == pytest.approx(10.0)


def test_empty_asset_gives_nan_not_errors(log):
    for fn in NAN_METRICS:
        assert math.isnan(fn(log, 5))


def test_metrics_do_not_import_simulation_code():
    code = ("import sys, marketsim.metrics; "
            "bad = sorted(n for n in sys.modules if n.startswith(('marketsim.sim.', 'marketsim.agents', "
            "'marketsim.experiments')) and n != 'marketsim.sim.log_schema'); print(bad); assert not bad")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_run_log_matches_s_diagnostics(tmp_path):
    from marketsim.experiments.diagnostics import diagnose_run
    from marketsim.experiments.run import run_experiment
    from marketsim.sim.log_schema import load_run

    config = {
        "base": {
            "name": "metrics_check", "exchange_backend": "python", "graph": "single_asset", "external": "flat",
            "use_market_index": False, "n_sessions": 3, "warmup_sessions": 1, "sample_dt": 0.05,
            "agents": {"valuers": 8, "trend": 2, "noise": 4, "n_funds": 1, "herding": {"h": 0.0}},
        }, "seeds": [3],
    }
    path = tmp_path / "grid.yaml"
    path.write_text(yaml.safe_dump(config))
    summary = run_experiment(path, tmp_path / "runs")
    run = load_run(tmp_path / "runs" / summary.run_id[0])
    table = m.stability_metrics(run)
    row = diagnose_run(run)
    assert len(table) == len(run.meta["assets"]) == 1
    for name in ("empty_side_fraction", "mean_spread_ticks", "mean_depth5", "max_drawdown", "execution_rate"):
        assert table.loc[0, name] == pytest.approx(row[name], nan_ok=True)
    assert table.loc[0, "volatility"] >= 0
