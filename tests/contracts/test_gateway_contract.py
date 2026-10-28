"""Contract K1: gateway rules (reservations, settlement of both sides, conservation)."""

from __future__ import annotations

import random

import numpy as np
import pytest

from marketsim.market import Gateway, PyExchange
from marketsim.market.api import Side, Status, TimeInForce
from tests.conftest import skip_if_not_implemented

BUY, SELL = int(Side.BUY), int(Side.SELL)
GTC, IOC = int(TimeInForce.GTC), int(TimeInForce.IOC)


def _gateway(cash, shares):
    return Gateway(PyExchange(np.asarray(shares).shape[1]), np.asarray(cash), np.asarray(shares))


def test_constructor_validates_shapes():
    with pytest.raises(ValueError):
        Gateway(PyExchange(2), np.array([100]), np.array([[1, 2, 3]]))
    with pytest.raises(ValueError):
        Gateway(PyExchange(1), np.array([-1]), np.array([[0]]))


@skip_if_not_implemented
def test_buy_without_cash_is_rejected():
    gw = _gateway([1_000], [[0]])
    report = gw.place(0, 0, BUY, 10, 101, GTC, now=0.0)  # needs 1010 cents
    assert report.status == Status.REJECTED_FUNDS
    assert gw.exchange.top(0).bid == np.iinfo(np.int64).min


@skip_if_not_implemented
def test_sell_without_shares_is_rejected():
    gw = _gateway([0], [[3]])
    assert gw.place(0, 0, SELL, 5, 100, GTC, now=0.0).status == Status.REJECTED_FUNDS


@skip_if_not_implemented
def test_reservation_partial_fill_and_release():
    """Agent 0 has 2000.00 USD and buys up to 10 shares at <= 102; 6 shares are offered at 100."""
    gw = _gateway([200_000, 0], [[0], [6]])
    gw.place(1, 0, SELL, 6, 10_000, GTC, now=0.0)
    report = gw.place(0, 0, BUY, 10, 10_200, GTC, now=1.0)
    assert report.status == Status.PARTIAL_RESTING and report.filled_qty == 6
    p = gw.portfolio(0)
    assert (p.cash_available, p.cash_reserved) == (99_200, 40_800)
    assert int(p.shares_available[0]) == 6
    assert gw.portfolio(1).cash_available == 60_000
    assert gw.cancel(0, report.order_id, now=2.0) == 4
    p = gw.portfolio(0)
    assert (p.cash_available, p.cash_reserved) == (140_000, 0)
    gw.check_invariants()


@skip_if_not_implemented
def test_cannot_cancel_someone_elses_order():
    gw = _gateway([0, 0], [[5], [0]])
    report = gw.place(0, 0, SELL, 5, 100, GTC, now=0.0)
    with pytest.raises(ValueError):
        gw.cancel(1, report.order_id, now=1.0)


@skip_if_not_implemented
def test_expire_after_fill_changes_nothing():
    gw = _gateway([10_000, 0], [[0], [1]])
    resting = gw.place(1, 0, SELL, 1, 100, int(TimeInForce.GTD), now=0.0)
    gw.place(0, 0, BUY, 1, 100, IOC, now=1.0)
    before = gw.portfolio(1)
    assert gw.expire(resting.order_id, now=5.0) == 0
    after = gw.portfolio(1)
    assert (after.cash_available, after.cash_reserved) == (before.cash_available, before.cash_reserved)
    assert (after.shares_available == before.shares_available).all()
    assert (after.shares_reserved == before.shares_reserved).all()


@skip_if_not_implemented
def test_conservation_under_random_trading():
    rng = random.Random(3)
    n_agents, n_assets = 6, 2
    gw = _gateway([1_000_000] * n_agents, [[50] * n_assets] * n_agents)
    for step in range(400):
        gw.place(rng.randrange(n_agents), rng.randrange(n_assets), rng.choice([BUY, SELL]), rng.randint(1, 10),
                 rng.randint(95, 105), rng.choice([GTC, IOC]), now=float(step))
        gw.check_invariants()
    fills = gw.drain_fills()
    assert len(fills) > 0 and len(gw.drain_fills()) == 0
