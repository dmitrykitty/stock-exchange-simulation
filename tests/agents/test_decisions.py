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


@pytest.mark.parametrize("signal", [0.1, 0.4, -0.1, -0.4])
@pytest.mark.parametrize("aggressive_prob", [0.0, 1.0])
def test_fractional_target_has_one_share_minimum_when_resources_allow(signal, aggressive_prob):
    from marketsim.agents.strategies import make_order
    from marketsim.sim.settings import AgentSettings

    settings = AgentSettings.from_dict({"aggressive_prob": aggressive_prob})
    # A C=0.8 retail portfolio: about $4,400, so even 2% of full wealth is under one share.
    portfolio = PortfolioView(220_000, 0, np.array([22]), np.array([0]))
    assert settings.rho * abs(signal) * 440_000 / 10_000 < 1
    order = make_order(signal, 10_000, Top(9995, 10, 10005, 10), portfolio,
                       settings, np.random.default_rng(1))
    assert order is not None
    assert order.qty == 1
    assert order.side == (1 if signal > 0 else -1)


@pytest.mark.parametrize("signal", [0.0, 0.05, -0.05, 0.04, -0.04])
def test_minimum_lot_does_not_override_the_signal_threshold(signal):
    from marketsim.agents.strategies import make_order
    from marketsim.sim.settings import AgentSettings

    portfolio = PortfolioView(220_000, 0, np.array([22]), np.array([0]))
    assert make_order(signal, 10_000, Top(9995, 10, 10005, 10), portfolio,
                      AgentSettings(), np.random.default_rng(1)) is None


@pytest.mark.parametrize("signal", [1.0, -1.0])
def test_zero_rho_still_disables_orders(signal):
    from marketsim.agents.strategies import make_order
    from marketsim.sim.settings import AgentSettings

    portfolio = PortfolioView(220_000, 0, np.array([22]), np.array([0]))
    settings = AgentSettings.from_dict({"rho": 0.0})
    assert make_order(signal, 10_000, Top(9995, 10, 10005, 10), portfolio,
                      settings, np.random.default_rng(1)) is None


@pytest.mark.parametrize("signal,cash,shares", [(0.4, 9994, 0), (-0.4, 0, 0)])
def test_minimum_lot_cannot_spend_reserved_resources(signal, cash, shares):
    from marketsim.agents.strategies import make_order
    from marketsim.sim.settings import AgentSettings

    portfolio = PortfolioView(cash, 220_000, np.array([shares]), np.array([22]))
    settings = AgentSettings.from_dict({"aggressive_prob": 0.0})
    assert make_order(signal, 10_000, Top(9995, 10, 10005, 10), portfolio,
                      settings, np.random.default_rng(1)) is None


def test_larger_targets_still_round_down():
    from marketsim.agents.strategies import make_order
    from marketsim.sim.settings import AgentSettings

    portfolio = PortfolioView(1_000_000, 0, np.array([100]), np.array([0]))
    order = make_order(0.9, 10_000, Top(9995, 10, 10005, 10), portfolio,
                       AgentSettings(), np.random.default_rng(1))
    assert order.qty == 3  # floor(0.02 * 0.9 * 2,000,000 / 10,000)


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
