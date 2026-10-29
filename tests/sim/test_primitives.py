"""Behavioral checks for the F4 scheduler, parameters and fixed-resource population."""

from dataclasses import replace

import numpy as np
import pytest

from marketsim.experiments.config import ConfigError


def test_simultaneous_events_keep_insertion_order_and_horizon():
    from marketsim.sim.scheduler import Scheduler

    queue = Scheduler()
    queue.schedule(0.5, "wake", 9)
    queue.schedule(0.2, "sample")
    queue.schedule(0.5, "expire", 3)
    assert [(e.time, e.kind, e.payload) for e in queue.before(0.5)] == [(0.2, "sample", None)]
    assert [(e.time, e.kind, e.payload) for e in queue.before(1)] == [
        (0.5, "wake", 9), (0.5, "expire", 3),
    ]


def test_scheduler_refuses_past_and_nonfinite_events():
    from marketsim.sim.scheduler import Scheduler

    queue = Scheduler()
    queue.schedule(1, "sample")
    list(queue.before(2))
    for t in (0.5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            queue.schedule(t, "wake")


def test_concentration_changes_distribution_without_changing_resources_or_groups():
    from marketsim.sim.population import make_population
    from marketsim.sim.settings import AgentSettings

    settings = AgentSettings.from_dict({
        "valuers": 7, "trend": 4, "noise": 3, "n_funds": 3,
        "total_cash_cents": 10003, "total_shares": 1003, "concentration": 0.2,
    })
    low = make_population(settings, np.random.default_rng(8))
    high = make_population(replace(settings, concentration=0.8), np.random.default_rng(8))
    assert low.kinds == high.kinds
    np.testing.assert_array_equal(low.funds, high.funds)
    assert low.cash[low.funds].sum() == 2000
    assert high.cash[high.funds].sum() == 8002
    assert low.shares[low.funds].sum() == 200
    assert high.shares[high.funds].sum() == 802
    for population in (low, high):
        assert int(population.cash.sum()) == 10003 + settings.market_maker_cash_cents
        assert int(population.shares.sum()) == 1003 + settings.market_maker_shares
        assert (population.cash >= 0).all() and (population.shares >= 0).all()
        assert population.cash.dtype == population.shares.dtype == np.dtype("int64")


@pytest.mark.parametrize("raw", [
    {"typo": 3}, {"rho": -1}, {"rho": float("nan")}, {"valuers": True},
    {"noise": 1.5}, {"initial_price_ticks": 0}, {"herding": {"h": 1.1}},
    {"herding": {"ttl": 0}}, {"herding": {"neigbors": 5}}, {"decision_rate": 0},
    {"n_funds": 100000}, {"tif": "unknown"}, {"total_shares": -1},
    {"valuers_network": 2}, {"market_maker_spread_ticks": 1},
])
def test_invalid_agent_settings_are_rejected(raw):
    from marketsim.sim.settings import AgentSettings

    with pytest.raises(ConfigError):
        AgentSettings.from_dict(raw)
