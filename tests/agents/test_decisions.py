"""Strategy direction, time-limited imitation and resource-constrained real orders."""

import numpy as np
import pytest

from marketsim.market import Gateway, PyExchange
from marketsim.market.api import NO_PRICE, PortfolioView, Side, TimeInForce, Top


def test_valuation_and_trend_signals_have_correct_direction_and_saturation():
    from marketsim.agents.strategies import trend_signal, valuer_signal

    assert valuer_signal(11000.0, 10000, 0.05) == 1.0
    assert valuer_signal(9750.0, 10000, 0.05) == -0.5
    assert trend_signal(10100, 10000, 0.02) == pytest.approx(0.5)
    assert trend_signal(9900, 10000, 0.02) == pytest.approx(-0.5)


def test_noise_uses_its_generator_and_stays_bounded():
    from marketsim.agents.strategies import noise_signal

    a, b = np.random.default_rng(8), np.random.default_rng(8)
    first = [noise_signal(a) for _ in range(50)]
    assert first == [noise_signal(b) for _ in range(50)]
    assert min(first) < 0 < max(first) and all(-1 <= v <= 1 for v in first)


def test_herding_uses_published_directions_and_expires_them():
    from marketsim.agents.herding import HerdingNetwork

    network = HerdingNetwork(3, 9, 0.5, np.random.default_rng(8))
    network.publish(1, 1, 0.0)
    network.publish(2, -1, 0.2)
    assert network.mix(0, 0.8, 0.2, 0.5) == (0.0, 0.4)
    assert network.mix(0, 0.8, 0.6, 0.5) == (-0.5, pytest.approx(0.15))
    assert network.mix(0, 0.8, 0.8, 0.0) == (0.0, 0.8)


def test_order_quantity_is_capped_by_available_resources_and_limit_price():
    from marketsim.agents.strategies import make_order
    from marketsim.sim.settings import AgentSettings

    s = AgentSettings.from_dict({"rho": 1.0, "aggressive_prob": 1.0})
    portfolio = PortfolioView(10000, 50000, np.array([3]), np.array([7]))
    top = Top(9900, 10, 10000, 10)
    buy = make_order(1.0, 10000, top, portfolio, s, np.random.default_rng(1))
    assert buy is None  # cannot afford even one share at the protection price
    sell = make_order(-1.0, 10000, top, portfolio, s, np.random.default_rng(1))
    assert (sell.side, sell.qty, sell.limit, sell.tif) == (-1, 3, 9900, int(TimeInForce.IOC))
    gw = Gateway(PyExchange(1), np.array([10000]), np.array([[3]]))
    assert gw.place(0, 0, sell.side, sell.qty, sell.limit, sell.tif, 0).filled_qty == 0
    gw.check_invariants()


def test_empty_book_has_integer_fallback_quotes_and_hold_sends_nothing():
    from marketsim.agents.strategies import make_order, market_price
    from marketsim.sim.settings import AgentSettings

    s = AgentSettings.from_dict({"aggressive_prob": 0.0})
    portfolio = PortfolioView(1000000, 0, np.array([100]), np.array([0]))
    top = Top(NO_PRICE, 0, NO_PRICE, 0)
    ex = PyExchange(1)
    assert market_price(ex, 10000) == 10000
    intent = make_order(0.5, 10000, top, portfolio, s, np.random.default_rng(8))
    assert intent.side == int(Side.BUY) and intent.limit == 9995 and type(intent.limit) is int
    assert make_order(0.01, 10000, top, portfolio, s, np.random.default_rng(8)) is None


def test_inventory_skew_lowers_both_quotes_when_maker_holds_too_many_shares():
    from marketsim.agents.strategies import maker_orders
    from marketsim.sim.settings import AgentSettings

    s = AgentSettings.from_dict({})
    p = PortfolioView(1_000_000, 0, np.array([2100]), np.array([0]))
    bid, ask = maker_orders(10000, p, 2000, s)
    assert (bid.limit, ask.limit) == (9993, 10003)
    assert bid.qty * bid.limit <= p.cash_available and ask.qty <= 2100
