"""Engine details beyond the contract tests: self-trade bookkeeping, gateway settlement of every side."""

from __future__ import annotations

import numpy as np
import pytest

from marketsim.market import Gateway, PyExchange
from marketsim.market.api import NO_PRICE, Side, Status, TimeInForce
from marketsim.market.cpp_exchange import CppExchange, is_available

BUY, SELL = int(Side.BUY), int(Side.SELL)
GTC, IOC = int(TimeInForce.GTC), int(TimeInForce.IOC)

BACKENDS = [pytest.param(PyExchange, id="python"),
            pytest.param(CppExchange, id="cpp", marks=pytest.mark.skipif(not is_available(), reason="lob_core not built"))]


@pytest.fixture(params=BACKENDS)
def make(request):
    return request.param


def test_self_trade_lock_is_released_after_cancel(make):
    ex = make(1)
    own, _ = ex.submit(0, 1, SELL, 5, 101, GTC)
    assert ex.submit(0, 1, BUY, 5, 101, GTC)[0].status == Status.REJECTED_SELF_TRADE
    ex.cancel(own.order_id)
    ex.submit(0, 2, SELL, 5, 101, GTC)
    report, fills = ex.submit(0, 1, BUY, 5, 101, GTC)
    assert report.status == Status.FILLED and int(fills[0]["maker_owner"]) == 2


def test_self_trade_lock_is_released_after_fill(make):
    ex = make(1)
    ex.submit(0, 1, SELL, 5, 101, GTC)
    ex.submit(0, 2, BUY, 5, 101, GTC)           # owner 1's ask is gone
    ex.submit(0, 3, SELL, 5, 101, GTC)
    assert ex.submit(0, 1, BUY, 5, 101, GTC)[0].status == Status.FILLED


def test_self_trade_check_uses_the_best_own_price(make):
    ex = make(1)
    ex.submit(0, 1, BUY, 1, 90, GTC)
    ex.submit(0, 1, BUY, 1, 95, GTC)
    assert ex.submit(0, 1, SELL, 1, 96, GTC)[0].status == Status.RESTING          # 96 > own best bid 95
    assert ex.submit(0, 1, SELL, 1, 95, GTC)[0].status == Status.REJECTED_SELF_TRADE


def test_level_quantity_after_partial_fill_and_cancel(make):
    ex = make(1)
    a, _ = ex.submit(0, 1, SELL, 5, 101, GTC)
    ex.submit(0, 2, SELL, 7, 101, GTC)
    ex.submit(0, 3, BUY, 2, 101, GTC)
    assert ex.top(0).ask_qty == 10
    ex.cancel(a.order_id)
    assert ex.top(0).ask_qty == 7
    ex.submit(0, 3, BUY, 7, 101, GTC)
    assert ex.top(0).ask == NO_PRICE and ex.top(0).ask_qty == 0


def test_rejected_and_ioc_orders_never_rest(make):
    ex = make(1)
    ex.submit(0, 1, BUY, 5, 0, GTC)              # invalid
    ex.submit(0, 1, BUY, 5, 100, IOC)            # nothing to match
    ex.submit(0, 1, BUY, 5, 100, 7)              # unknown time in force
    assert (ex.depth(0, 2)["bid"] == NO_PRICE).all()


def _gw(cash, shares):
    return Gateway(PyExchange(np.asarray(shares).shape[1]), np.asarray(cash), np.asarray(shares))


def test_taker_sell_into_resting_buy_settles_both_sides():
    gw = _gw([10_000, 0], [[0], [3]])
    gw.place(0, 0, BUY, 3, 1_000, GTC, now=0.0)                  # reserves 3000
    report = gw.place(1, 0, SELL, 2, 900, IOC, now=1.0)          # trades at the resting price 1000
    assert report.status == Status.FILLED and report.avg_price == 1_000
    buyer, seller = gw.portfolio(0), gw.portfolio(1)
    assert (buyer.cash_available, buyer.cash_reserved, int(buyer.shares_available[0])) == (7_000, 1_000, 2)
    assert (seller.cash_available, int(seller.shares_available[0]), int(seller.shares_reserved[0])) == (2_000, 1, 0)
    gw.check_invariants()


def test_self_trade_rejection_releases_the_reservation():
    gw = _gw([10_000], [[5]])
    gw.place(0, 0, SELL, 5, 100, GTC, now=0.0)
    report = gw.place(0, 0, BUY, 5, 100, GTC, now=1.0)
    assert report.status == Status.REJECTED_SELF_TRADE
    p = gw.portfolio(0)
    assert (p.cash_available, p.cash_reserved) == (10_000, 0)
    gw.check_invariants()


def test_ioc_partial_fill_releases_the_rest():
    gw = _gw([100_000, 0], [[0], [2]])
    gw.place(1, 0, SELL, 2, 100, GTC, now=0.0)
    report = gw.place(0, 0, BUY, 5, 110, IOC, now=1.0)
    assert report.status == Status.PARTIAL_CANCELLED
    p = gw.portfolio(0)
    assert (p.cash_available, p.cash_reserved) == (100_000 - 200, 0)
    gw.check_invariants()


def test_malformed_order_reaches_the_exchange_and_reserves_nothing():
    gw = _gw([100], [[1]])
    report = gw.place(0, 0, BUY, -1, 100, GTC, now=0.0)
    assert report.status == Status.REJECTED_INVALID and report.order_id == 1
    assert gw.portfolio(0).cash_reserved == 0
