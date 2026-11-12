"""F6 full network: asset subsets, price scaling, follower-only holdings, target positions, rebalancers and the
risk limit."""

import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from marketsim.agents.strategies import rebalance_order, risk_sale, target_order
from marketsim.experiments.config import ConfigError, RunConfig, expand, load_config
from marketsim.graph.api import load_graph
from marketsim.market.api import PortfolioView, Side, TimeInForce, Top
from marketsim.sim.api import _validate_graph, run
from marketsim.sim.log_schema import RUN_TABLES, load_run
from marketsim.sim.network import NetworkSimulation, make_network_population
from marketsim.sim.settings import AgentSettings, identity_dict


def net_config(external="oil_shock", n_sessions=8, **agents):
    return RunConfig(
        name="net6", exchange_backend="python", graph="graph_chain", external=external,
        use_market_index=True, n_sessions=n_sessions, warmup_sessions=2, sample_dt=0.05,
        agents={"valuers_myopic": 8, "valuers_network": 8, "trend": 4, "noise": 4, "n_funds": 2,
                "total_cash_cents": 6_000_000, "total_shares": 2_400, "debug": True, **agents},
    )


F6 = {"price_scaling": True, "holdings": "followed", "position_sizing": "target", "attention": 2, "rebalancers": 4, "risk_limit": 0.02,
      "risk_cooldown": 1.0, "observation_delay": 0.1, "weight_error_std": 0.2, "herding": {"h": 0.3}}


def test_f6_keys_at_defaults_keep_earlier_identities_and_are_rejected_without_a_graph():
    base = {"valuers_myopic": 3, "valuers_network": 3, "trend": 2, "noise": 2, "n_funds": 2}
    plain = AgentSettings.from_dict(base, graph_mode=True)
    explicit = AgentSettings.from_dict(
        {**base, "assets": [], "price_scaling": False, "holdings": "all", "rebalancers": 0, "risk_limit": 0.0},
        graph_mode=True)
    assert identity_dict(plain) == identity_dict(explicit)
    assert not set(identity_dict(plain)) & {"assets", "price_scaling", "holdings", "rebalancers", "risk_limit",
                                            "rebalance_band", "risk_sell_fraction", "risk_cooldown"}
    for key, value in (("rebalancers", 2), ("risk_limit", 0.1), ("price_scaling", True), ("holdings", "followed"),
                       ("assets", ["COP"]), ("position_sizing", "target"), ("position_tilt", 0.3)):
        with pytest.raises(ConfigError, match="need a graph"):
            AgentSettings.from_dict({key: value})


@pytest.mark.parametrize("agents", [
    {"assets": "COP"}, {"assets": ["COP", "COP"]}, {"assets": ["XXX"]}, {"assets": ["VLO"]},
    {"holdings": "some"}, {"price_scaling": 1}, {"position_sizing": "stock"}, {"position_tilt": 1.5}, {"rebalancers": -1}, {"rebalancers": 2, "rebalance_band": 0.0},
    {"risk_limit": 1.5}, {"risk_limit": 0.1, "risk_sell_fraction": 0.0}, {"risk_cooldown": 0.0},
])
def test_invalid_f6_settings_fail_before_any_output(tmp_path, agents):
    with pytest.raises(ConfigError):
        run(net_config(**agents), 1, tmp_path)
    assert not list(tmp_path.iterdir())


def test_asset_subset_trades_only_the_selected_companies(tmp_path):
    log = load_run(run(net_config(assets=["VLO", "COP"]), 2, tmp_path))
    assert log.meta["assets"] == ["COP", "VLO"]  # graph order, not config order
    assert set(log["trades"].asset) == {0, 1} and set(log["book"].asset) == {0, 1}
    assert log.meta["initial_resources"] == log.meta["final_resources"]
    # VLO keeps its weights: the reference equals the full graph's reference for the same companies.
    full = load_run(run(net_config(), 2, tmp_path / "full"))
    for asset in (0, 1):
        np.testing.assert_allclose(
            log["valuations"].loc[lambda d, a=asset: d.asset == a, "reference"].to_numpy(),
            full["valuations"].loc[lambda d, a=asset: d.asset == a, "reference"].to_numpy())


def test_price_scaling_gives_every_asset_the_same_value_and_relative_quotes(tmp_path):
    log = load_run(run(net_config(price_scaling=True, market_maker_shares=400), 3, tmp_path))
    params, start = log.meta["asset_parameters"], log.meta["start_prices_ticks"]
    graph = load_graph("graph_chain")
    assert start == [round(graph.nodes[c].start_price * 100) for c in graph.companies()]
    for i, price in enumerate(start):
        f = price / 10_000
        assert params["total_shares"][i] == round(2_400 / f)
        assert params["market_maker_shares"][i] == round(400 / f)
        assert params["market_maker_qty"][i] == max(1, round(20 / f))
        assert params["market_maker_spread_ticks"][i] == max(2, round(10 * f))
        assert params["inventory_skew_ticks"][i] == pytest.approx(0.02 * f * f)
    makers = log.meta["resolved_agents"]["market_makers_per_asset"]
    expected = [params["total_shares"][i] + makers * params["market_maker_shares"][i] for i in range(len(start))]
    assert log.meta["initial_resources"]["shares_by_asset"] == expected
    assert log.meta["initial_resources"] == log.meta["final_resources"]
    assert set(log["trades"].asset) == {0, 1, 2}


def test_followed_holdings_give_shares_only_to_investors_that_trade_the_asset():
    settings = AgentSettings.from_dict(
        {"valuers_myopic": 10, "valuers_network": 10, "trend": 5, "noise": 5, "n_funds": 4, "rebalancers": 3,
         "attention": 2, "holdings": "followed", "total_shares": 1_000}, graph_mode=True)
    rng = np.random.default_rng
    population = make_network_population(settings, 5, rng(1), rng(2))
    investors = settings.investors
    assert population.kinds[30:investors] == ["rebalancer"] * 3 and population.funds[30:investors].all()
    assert population.funds[:investors].sum() == 4 + 3
    assert (population.follows[:investors].sum(axis=1) == 2).all()
    assert (population.shares[:investors][~population.follows[:investors]] == 0).all()
    assert (population.shares[:investors].sum(axis=0) == 1_000).all()
    # Same seeds without the F6 options: the four original groups get the same funds and followed assets.
    earlier = make_network_population(replace(settings, rebalancers=0, holdings="all"), 5, rng(1), rng(2))
    np.testing.assert_array_equal(earlier.funds[:30], population.funds[:30])
    np.testing.assert_array_equal(earlier.follows[:30], population.follows[:30])
    lonely = replace(settings, attention=1, valuers_myopic=1, valuers_network=1, trend=1, noise=1, n_funds=1,
                     rebalancers=0)
    with pytest.raises(ConfigError, match="no investor follows"):
        make_network_population(lonely, 5, rng(1), rng(2))


def test_rebalance_order_closes_the_gap_only_outside_the_band():
    settings = AgentSettings(rebalance_band=0.05, aggressive_prob=1.0, protection_fraction=0.01)
    top = Top(9_995, 10, 10_005, 10)
    portfolio = PortfolioView(cash_available=10_000_000, cash_reserved=0,
                              shares_available=np.array([100]), shares_reserved=np.array([0]))
    rng = np.random.default_rng(1)
    assert rebalance_order(0.04, 1_000_000.0, 10_000, top, portfolio, settings, rng, 0) is None
    buy = rebalance_order(0.10, 1_000_000.0, 10_000, top, portfolio, settings, rng, 0)
    assert (buy.side, buy.qty, buy.limit, buy.tif) == (Side.BUY, 10, 10_100, TimeInForce.IOC)
    sell = rebalance_order(-0.30, 5_000_000.0, 10_000, top, portfolio, settings, rng, 0)
    assert (sell.side, sell.qty, sell.limit) == (Side.SELL, 100, 9_900)  # capped by the shares held
    sale = risk_sale(10_000, 7, settings)
    assert (sale.side, sale.qty, sale.limit, sale.tif) == (Side.SELL, 7, 9_900, TimeInForce.IOC)


def test_target_order_closes_the_gap_up_to_the_cap_in_whole_shares():
    settings = AgentSettings(aggressive_prob=1.0, protection_fraction=0.01)
    top = Top(9_995, 10, 10_005, 10)
    portfolio = PortfolioView(cash_available=10_000_000, cash_reserved=0,
                              shares_available=np.array([30]), shares_reserved=np.array([0]))
    rng = np.random.default_rng(1)
    assert target_order(9_999.0, 1_000_000.0, 10_000, top, portfolio, settings, rng, 0) is None  # under one share
    buy = target_order(125_000.0, 1_000_000.0, 10_000, top, portfolio, settings, rng, 0)
    assert (buy.side, buy.qty, buy.limit, buy.tif) == (Side.BUY, 12, 10_100, TimeInForce.IOC)
    capped = target_order(125_000.0, 50_000.0, 10_000, top, portfolio, settings, rng, 0)
    assert capped.qty == 5
    sell = target_order(-900_000.0, 1_000_000.0, 10_000, top, portfolio, settings, rng, 0)
    assert (sell.side, sell.qty) == (Side.SELL, 30)  # capped by the shares held


@pytest.mark.parametrize("external", ["oil_shock", "oil_shock_down"])
def test_target_positions_stay_within_the_tilt_around_the_initial_shares(external):
    # With the flow rule a valuer keeps buying an undervalued asset until its cash is gone; with a target it
    # stops at initial shares * (1 + tilt).
    config = net_config(external=external, n_sessions=20, position_sizing="target", position_tilt=0.4, rho=0.1,
                        noise=0, trend=0, valuers_myopic=12, valuers_network=12, inventory_skew_ticks=0.3)
    checked = _validate_graph(config, 8)
    simulation = NetworkSimulation(config, checked["settings"], 8, checked["graph"], checked["external"],
                                   checked["external_names"], checked["reference"])
    initial = simulation.population.shares[:simulation.investors].copy()
    simulation.execute()
    final = np.array([simulation.gateway.portfolio(a).shares_available for a in range(simulation.investors)])
    assert (final != initial).any()
    assert (final <= np.floor(initial * 1.4) + 1).all()
    assert (final >= np.ceil(initial * 0.6) - 1).all()
    assert simulation.resources()["shares_by_asset"] == [int(x) for x in simulation.population.shares.sum(axis=0)]


def test_rebalancers_are_funds_that_sell_what_went_up(tmp_path):
    config = net_config(n_sessions=12, rebalancers=6, rebalance_band=0.01, inventory_skew_ticks=0.2,
                        herding={"h": 0.5})
    log = load_run(run(config, 4, tmp_path))
    population = log.meta["population"]
    rebalancers = [a for a, kind in enumerate(population["kinds"]) if kind == "rebalancer"]
    assert len(rebalancers) == 6 and set(rebalancers) <= set(population["fund_ids"])
    assert all(population["herding_h"][a] == 0 for a in rebalancers)
    signals = log["signals"].loc[lambda d: d.agent.isin(rebalancers)]
    assert len(signals) and (signals.final_signal == signals.own_signal).all()
    # Oil +20% at session 5 lifts COP (asset 0): its weight exceeds the target and rebalancers sell it.
    orders = log["orders"].loc[lambda d: d.agent.isin(rebalancers) & (d.asset == 0) & (d.t >= 6)]
    assert len(orders) and (orders.side * orders.filled_qty).sum() < 0
    assert log.meta["initial_resources"] == log.meta["final_resources"]


def test_risk_limit_sells_followed_positions_and_blocks_purchases_during_the_cooldown(tmp_path):
    config = net_config(external="oil_shock_down", n_sessions=12, risk_limit=0.01, risk_cooldown=2.0,
                        inventory_skew_ticks=0.2)
    log = load_run(run(config, 5, tmp_path))
    events = log["events"].loc[lambda d: d.kind == "risk_limit"]
    assert len(events) == log.meta["risk_limit_triggers"] > 0
    orders = log["orders"]
    for row in events.itertuples():
        payload = json.loads(row.payload)
        assert payload["drawdown"] > 0.01
        during = orders.loc[(orders.agent == payload["agent"]) & (orders.t >= row.t) & (orders.t < row.t + 2.0)]
        assert len(during) and (during.side == Side.SELL).all() and (during.tif == TimeInForce.IOC).all()
    assert log.meta["initial_resources"] == log.meta["final_resources"]
    calm = load_run(run(replace(config, agents={**config.agents, "risk_limit": 0.9}), 5, tmp_path / "calm"))
    assert calm.meta["risk_limit_triggers"] == 0 and not (calm["events"].kind == "risk_limit").any()


def test_partial_risk_sale_keeps_part_of_every_position(tmp_path):
    config = net_config(external="oil_shock_down", n_sessions=10, risk_limit=0.01, risk_sell_fraction=0.5,
                        risk_cooldown=50.0, trend=0, noise=8)
    log = load_run(run(config, 6, tmp_path))
    events = log["events"].loc[lambda d: d.kind == "risk_limit"]
    assert len(events)
    orders = log["orders"]
    first = events.iloc[0]
    agent = json.loads(first.payload)["agent"]
    sold = orders.loc[(orders.agent == agent) & (orders.t >= first.t)].groupby("asset").filled_qty.sum()
    # Retail investors start with 2400 * 0.5 / 22 = 54 shares of each asset; half of a position is at most ~60.
    assert len(sold) and (sold <= 70).all()


def _simulation(config, seed):
    checked = _validate_graph(config, seed)
    return NetworkSimulation(config, checked["settings"], seed, checked["graph"], checked["external"],
                             checked["external_names"], checked["reference"])


def test_empty_portfolios_neither_divide_by_zero_nor_trade(tmp_path):
    # concentration 0: funds, rebalancers included, start with no cash and no shares.
    config = net_config(n_sessions=6, concentration=0, rebalancers=1, risk_limit=0.2, risk_cooldown=0.5)
    simulation = _simulation(config, 3)
    rebalancer = simulation.population.kinds.index("rebalancer")
    assert simulation._wealth(simulation.gateway.portfolio(rebalancer)) == 0
    assert set(simulation._targets[rebalancer].values()) == {0.0}
    empty = [a for a in range(simulation.investors) if simulation._peak[a] == 0]
    assert empty
    # A zero peak, at the start or after a cooldown, means no drawdown.
    for agent in empty:
        simulation._derisk_until[agent] = 0.25
        assert not simulation._derisking(agent, 1.0)
    log = load_run(run(config, 3, tmp_path))
    assert not (log["orders"].agent == rebalancer).any()
    assert log.meta["initial_resources"] == log.meta["final_resources"]


def test_risk_limit_cuts_every_held_position_with_exact_rounding():
    config = net_config(holdings="all", attention=1, risk_limit=0.01, risk_sell_fraction=0.8)
    simulation = _simulation(config, 4)
    agent = next(a for a in range(simulation.investors) if simulation.population.kinds[a] == "noise")
    portfolio = simulation.gateway.portfolio(agent)
    held = {j: int(portfolio.shares_available[j]) for j in range(len(simulation.assets))}
    assert len(simulation.followed[agent]) == 1 and all(held.values())
    simulation.risk_limited[agent] = True
    simulation._peak[agent] = 2 * simulation._wealth(portfolio)  # a 50% drawdown
    assert simulation._derisking(agent, 1.0)
    floor = simulation._risk_floor[agent]
    assert sorted(floor) == sorted(held)  # unfollowed positions are cut too
    assert floor == {j: (2 * q) // 10 for j, q in held.items()}  # exact: 10 shares at 0.8 keep 2, not 1


def test_risk_sale_of_unfollowed_assets_happens_without_herding(tmp_path):
    config = net_config(external="oil_shock_down", n_sessions=10, holdings="all", attention=1, risk_limit=0.01,
                        risk_cooldown=50.0, trend=0, noise=8)
    log = load_run(run(config, 6, tmp_path))
    followed = log.meta["population"]["followed_assets"]
    events = log["events"].loc[lambda d: d.kind == "risk_limit"]
    assert len(events)
    first = events.iloc[0]
    agent = json.loads(first.payload)["agent"]
    sold = log["orders"].loc[(log["orders"].agent == agent) & (log["orders"].t >= first.t)]
    assert (sold.side == Side.SELL).all() and set(sold.asset) - set(followed[agent])
    unfollowed = log["signals"].loc[(log["signals"].agent == agent) & ~log["signals"].asset.isin(followed[agent])]
    assert len(unfollowed) and (unfollowed.herd_signal == 0).all()
    assert log.meta["initial_resources"] == log.meta["final_resources"]


def test_f6_runs_are_deterministic_and_identical_on_both_exchanges(tmp_path):
    from marketsim.market.cpp_exchange import is_available

    config = net_config(external="oil_shock_down", n_sessions=10, **F6)
    a = load_run(run(config, 7, tmp_path / "a"))
    b = load_run(run(config, 7, tmp_path / "b"))
    assert a.meta["run_id"] == b.meta["run_id"]
    assert a.meta["initial_resources"] == a.meta["final_resources"]
    assert {"risk_limit", "session_start"} <= set(a["events"].kind)
    for spec in RUN_TABLES:
        pd.testing.assert_frame_equal(a[spec.name], b[spec.name], check_exact=True)
    if not is_available():
        pytest.skip("lob_core not built")
    c = load_run(run(replace(config, exchange_backend="cpp"), 7, tmp_path / "cpp"))
    for spec in RUN_TABLES:
        pd.testing.assert_frame_equal(a[spec.name], c[spec.name], check_exact=True)


def test_f6_configs_expand_to_the_planned_grids():
    pilot = expand(load_config("configs/f6_pilot.yaml"))
    assert sorted({len(spec.config.agents["assets"]) for spec in pilot}) == [0, 3, 10]
    e5 = expand(load_config("configs/f6_e5.yaml"))
    assert len(e5) == 270 and len({spec.seed for spec in e5}) == 30
    assert {spec.config.agents["composition"] for spec in e5} == {"network", "mixed", "trend"}
    assert {spec.config.agents["herding"]["h"] for spec in e5} == {0.0, 0.2, 0.4}
    assert not {spec.seed for spec in pilot} & {spec.seed for spec in e5}
    for spec in pilot + e5:
        assert spec.config.graph == "real_v1" and spec.config.external == "real_2024_2025"
        assert spec.config.use_market_index is True
        AgentSettings.from_dict(spec.config.agents, graph_mode=True)
