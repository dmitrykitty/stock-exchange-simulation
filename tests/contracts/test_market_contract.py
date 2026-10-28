"""Contract K1: exchange semantics. Every Exchange implementation must pass these tests.

They run for the Python reference (PyExchange) and, when ``lob_core`` is built, for the C++ engine.
Prices are integer ticks; owner ids are arbitrary integers.
"""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

from marketsim.market import api
from marketsim.market.api import NO_PRICE, ORDER_DTYPE, Side, Status, TimeInForce
from marketsim.market.cpp_exchange import CppExchange, is_available
from marketsim.market.py_exchange import PyExchange
from tests.conftest import skip_if_not_implemented

BUY, SELL = int(Side.BUY), int(Side.SELL)
GTC, IOC = int(TimeInForce.GTC), int(TimeInForce.IOC)

BACKENDS = [pytest.param(PyExchange, id="python")]
BACKENDS.append(pytest.param(CppExchange, id="cpp", marks=pytest.mark.skipif(not is_available(), reason="lob_core not built")))


@pytest.fixture(params=BACKENDS)
def make(request):
    return request.param


def test_enum_values_match_cpp_header():
    """Python constants must mirror cpp/include/lob_core/types.hpp."""
    assert [int(s) for s in Status] == [0, 1, 2, 3, 4, 10, 11, 12]
    assert (int(Side.BUY), int(Side.SELL)) == (1, -1)
    assert [int(t) for t in TimeInForce] == [0, 1, 2]
    assert NO_PRICE == np.iinfo(np.int64).min


@skip_if_not_implemented
def test_resting_orders_do_not_trade_when_not_crossed(make):
    ex = make(1)
    r1, f1 = ex.submit(0, 1, SELL, 10, 101, GTC)
    r2, f2 = ex.submit(0, 2, BUY, 5, 100, GTC)
    assert (r1.status, r2.status) == (Status.RESTING, Status.RESTING)
    assert len(f1) == len(f2) == 0
    assert ex.top(0) == api.Top(100, 5, 101, 10)


@skip_if_not_implemented
def test_price_priority(make):
    ex = make(1)
    ex.submit(0, 1, SELL, 5, 102, GTC)
    r_cheap, _ = ex.submit(0, 2, SELL, 5, 101, GTC)
    report, fills = ex.submit(0, 3, BUY, 5, 105, GTC)
    assert report.status == Status.FILLED
    assert len(fills) == 1
    assert fills[0]["price"] == 101 and fills[0]["maker_order"] == r_cheap.order_id


@skip_if_not_implemented
def test_time_priority_at_same_price(make):
    ex = make(1)
    first, _ = ex.submit(0, 1, SELL, 5, 101, GTC)
    ex.submit(0, 2, SELL, 5, 101, GTC)
    _, fills = ex.submit(0, 3, BUY, 3, 101, GTC)
    assert len(fills) == 1 and fills[0]["maker_order"] == first.order_id and fills[0]["qty"] == 3


@skip_if_not_implemented
def test_partial_fill_rests_remainder(make):
    ex = make(1)
    ex.submit(0, 1, SELL, 5, 101, GTC)
    report, fills = ex.submit(0, 2, BUY, 9, 102, GTC)
    assert report.status == Status.PARTIAL_RESTING
    assert (report.filled_qty, report.resting_qty) == (5, 4)
    assert fills[0]["price"] == 101
    assert ex.top(0) == api.Top(102, 4, NO_PRICE, 0)


@skip_if_not_implemented
def test_sweep_several_levels_and_average_price(make):
    ex = make(1)
    ex.submit(0, 1, SELL, 5, 101, GTC)
    ex.submit(0, 2, SELL, 5, 102, GTC)
    report, fills = ex.submit(0, 3, BUY, 8, 103, IOC)
    assert report.status == Status.FILLED
    assert [(f["price"], f["qty"]) for f in fills] == [(101, 5), (102, 3)]
    assert math.isclose(report.avg_price, (101 * 5 + 102 * 3) / 8)
    assert ex.top(0).ask == 102 and ex.top(0).ask_qty == 2


@skip_if_not_implemented
def test_ioc_remainder_is_cancelled(make):
    ex = make(1)
    ex.submit(0, 1, SELL, 5, 101, GTC)
    report, _ = ex.submit(0, 2, BUY, 8, 101, IOC)
    assert report.status == Status.PARTIAL_CANCELLED
    assert (report.filled_qty, report.resting_qty) == (5, 0)
    assert ex.top(0).bid == NO_PRICE


@skip_if_not_implemented
def test_ioc_on_empty_book(make):
    ex = make(1)
    report, fills = ex.submit(0, 1, BUY, 5, 100, IOC)
    assert report.status == Status.CANCELLED_IOC and len(fills) == 0 and math.isnan(report.avg_price)


@skip_if_not_implemented
def test_trade_price_is_resting_price(make):
    ex = make(1)
    ex.submit(0, 1, BUY, 5, 103, GTC)
    _, fills = ex.submit(0, 2, SELL, 5, 100, GTC)
    assert fills[0]["price"] == 103 and fills[0]["taker_side"] == SELL


@skip_if_not_implemented
def test_cancel_returns_remaining_quantity(make):
    ex = make(1)
    resting, _ = ex.submit(0, 1, SELL, 10, 101, GTC)
    ex.submit(0, 2, BUY, 4, 101, GTC)
    assert ex.cancel(resting.order_id) == 6
    assert ex.cancel(resting.order_id) == 0
    assert ex.cancel(10_000) == 0
    assert ex.top(0).ask == NO_PRICE


@skip_if_not_implemented
def test_self_trade_is_rejected_without_state_change(make):
    ex = make(1)
    ex.submit(0, 1, SELL, 5, 101, GTC)
    before = ex.depth(0, 3).copy()
    report, fills = ex.submit(0, 1, BUY, 5, 101, GTC)
    assert report.status == Status.REJECTED_SELF_TRADE and len(fills) == 0
    assert (ex.depth(0, 3) == before).all()
    ok, _ = ex.submit(0, 1, BUY, 5, 100, GTC)  # does not cross the own ask: allowed
    assert ok.status == Status.RESTING


@pytest.mark.parametrize("asset,side,qty,limit", [(0, BUY, 0, 100), (0, 0, 5, 100), (5, BUY, 5, 100), (0, BUY, 5, 0)])
@skip_if_not_implemented
def test_invalid_orders_are_rejected(make, asset, side, qty, limit):
    ex = make(1)
    report, fills = ex.submit(asset, 1, side, qty, limit, GTC)
    assert report.status == Status.REJECTED_INVALID and len(fills) == 0
    assert ex.top(0) == api.Top(NO_PRICE, 0, NO_PRICE, 0)


@skip_if_not_implemented
def test_order_ids_and_trade_seq_are_sequential(make):
    ex = make(2)
    r1, _ = ex.submit(0, 1, SELL, 5, 101, GTC)
    r2, _ = ex.submit(0, 2, BUY, 0, 101, GTC)  # rejected, still gets an id
    r3, f3 = ex.submit(0, 2, BUY, 2, 101, GTC)
    r4, _ = ex.submit(1, 3, SELL, 1, 50, GTC)
    r5, f5 = ex.submit(1, 4, BUY, 1, 50, GTC)
    assert [r.order_id for r in (r1, r2, r3, r4, r5)] == [1, 2, 3, 4, 5]
    assert [int(f["seq"]) for f in np.concatenate([f3, f5])] == [1, 2]
    assert ex.last_trade(0) == (1, 101) and ex.last_trade(1) == (2, 50)


@skip_if_not_implemented
def test_assets_are_independent(make):
    ex = make(2)
    ex.submit(0, 1, SELL, 5, 101, GTC)
    report, fills = ex.submit(1, 2, BUY, 5, 200, GTC)
    assert report.status == Status.RESTING and len(fills) == 0
    assert ex.last_trade(0) == (0, NO_PRICE)


@skip_if_not_implemented
def test_depth_has_exact_length_and_padding(make):
    ex = make(1)
    ex.submit(0, 1, BUY, 3, 99, GTC)
    ex.submit(0, 2, BUY, 4, 100, GTC)
    ex.submit(0, 3, BUY, 1, 100, GTC)
    d = ex.depth(0, 3)
    assert d.dtype == api.DEPTH_DTYPE and len(d) == 3
    assert [(int(x["bid"]), int(x["bid_qty"])) for x in d] == [(100, 5), (99, 3), (NO_PRICE, 0)]
    assert all(int(x["ask"]) == NO_PRICE and int(x["ask_qty"]) == 0 for x in d)


def _random_orders(n: int, n_assets: int, seed: int) -> np.ndarray:
    rng = random.Random(seed)
    rows = [(rng.randrange(n_assets), rng.randrange(5), rng.choice([BUY, SELL]), rng.randint(1, 20),
             rng.randint(95, 105), rng.choice([GTC, GTC, IOC])) for _ in range(n)]
    return np.array(rows, dtype=ORDER_DTYPE)


@skip_if_not_implemented
def test_batch_equals_sequential(make):
    orders = _random_orders(300, 2, seed=7)
    seq_ex, batch_ex = make(2), make(2)
    seq_reports, seq_fills = [], []
    for o in orders:
        r, f = seq_ex.submit(int(o["asset"]), int(o["owner"]), int(o["side"]), int(o["qty"]), int(o["limit"]), int(o["tif"]))
        seq_reports.append((r.order_id, int(r.status), r.filled_qty, r.resting_qty))
        seq_fills.append(f)
    reports, fills = batch_ex.submit_batch(orders)
    assert reports.dtype == api.REPORT_DTYPE and fills.dtype == api.FILL_DTYPE
    assert [(int(r["order_id"]), int(r["status"]), int(r["filled_qty"]), int(r["resting_qty"])) for r in reports] == seq_reports
    assert (np.concatenate(seq_fills) == fills).all()
    for a in range(2):
        assert (seq_ex.depth(a, 10) == batch_ex.depth(a, 10)).all()


@pytest.mark.skipif(not is_available(), reason="lob_core not built")
@skip_if_not_implemented
def test_cpp_matches_python_reference():
    """Differential test: identical random order streams must give identical trades and books."""
    for seed in range(20):
        orders = _random_orders(500, 3, seed)
        py, cpp = PyExchange(3), CppExchange(3)
        py_r, py_f = py.submit_batch(orders)
        cpp_r, cpp_f = cpp.submit_batch(orders)
        assert (py_r[["order_id", "status", "filled_qty", "resting_qty"]] == cpp_r[["order_id", "status", "filled_qty", "resting_qty"]]).all()
        assert (py_f == cpp_f).all()
        for a in range(3):
            assert (py.depth(a, 20) == cpp.depth(a, 20)).all()
