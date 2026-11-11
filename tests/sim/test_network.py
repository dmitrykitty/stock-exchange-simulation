"""F5 network market: graph valuers, several assets, reference path in the log, heterogeneous herding."""

import json
import shutil
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from marketsim import paths
from marketsim.data.api import load_external
from marketsim.experiments.config import ConfigError, RunConfig, expand, load_config
from marketsim.experiments.diagnostics import diagnose_run
from marketsim.graph.api import load_graph
from marketsim.graph.reference import reference_path
from marketsim.market.api import NO_PRICE
from marketsim.sim.api import run
from marketsim.sim.log_schema import RUN_TABLES, load_run


def net_config(graph="graph_chain", external="oil_shock", n_sessions=8, use_market_index=True, **agents):
    return RunConfig(
        name="net", exchange_backend="python", graph=graph, external=external,
        use_market_index=use_market_index, n_sessions=n_sessions, warmup_sessions=2, sample_dt=0.05,
        agents={"valuers_myopic": 8, "valuers_network": 8, "trend": 4, "noise": 4, "n_funds": 2,
                "total_cash_cents": 6_000_000, "total_shares": 2_400, "debug": True, **agents},
    )


def mids(log, asset):
    book = log["book"]
    book = book.loc[(book.asset == asset) & (book.best_bid != NO_PRICE) & (book.best_ask != NO_PRICE)]
    return book.t.to_numpy(), ((book.best_bid + book.best_ask) / 2).to_numpy()


@pytest.fixture
def wave_data(tmp_path, monkeypatch):
    """x -> A -> B -> C with weights 0.8; only A sees x directly (B and C have company parents only)."""
    root = tmp_path / "data"
    graph = root / "graph" / "wave"
    graph.mkdir(parents=True)
    (graph / "nodes.csv").write_text(
        "node,kind,ticker,bea_code,start_price\nx,external,,,\nA,company,A,1,100.0\nB,company,B,2,100.0\n"
        "C,company,C,3,100.0\n")
    (graph / "edges.csv").write_text(
        "parent,child,w_direct,w_full,t_stat_direct,t_stat_full,source,justification\n"
        "x,A,0.8,0.8,10,10,commodity,test\nA,B,,0.8,,10,bea,test\nB,C,,0.8,,10,bea,test\n")
    (graph / "meta.json").write_text("{}")
    fixtures = root / "fixtures"
    fixtures.mkdir()
    rows = ["session,date,x"] + [f"{s},,{100.0 if s < 2 else 130.0}" for s in range(10)]
    (fixtures / "external_x_shock.csv").write_text("\n".join(rows) + "\n")
    shutil.copytree(paths.fixtures_dir() / "graph_chain", fixtures / "graph_chain")
    for name in ("oil_shock", "flat"):
        shutil.copy(paths.fixtures_dir() / f"external_{name}.csv", fixtures / f"external_{name}.csv")
    monkeypatch.setenv("MARKETSIM_DATA", str(root))
    return root


def test_graph_run_is_valid_conserves_resources_and_logs_the_reference(tmp_path):
    log = load_run(run(net_config(), 3, tmp_path))
    assert log.meta["assets"] == ["COP", "VLO", "DAL"]
    assert log.meta["simulation_version"] == "f5-v1" and log.meta["run_id"].startswith("f5_")
    assert set(log["trades"].asset) == {0, 1, 2}
    assert set(log["orders"].asset) == {0, 1, 2}
    assert (log["book"].groupby("t").size() == 3).all()
    initial, final = log.meta["initial_resources"], log.meta["final_resources"]
    assert initial == final and final["cash_reserved"] == final["shares_reserved"] == 0
    assert log.meta["invariant_checks"] > 10
    assert log["orders"].filled_qty.sum() == 2 * log["trades"].qty.sum()
    assert (log["trades"].taker_owner != log["trades"].maker_owner).all()
    # The logged reference is R of the full-weight graph, constant within a session.
    graph = load_graph("graph_chain")
    external = load_external("oil_shock").drop(columns="date")
    expected = reference_path(graph, external, mode="full")[graph.companies()] * 100
    v = log["valuations"].loc[lambda d: d.t < 8]
    sessions = np.floor(v.t).astype(int)
    np.testing.assert_allclose(v.reference.to_numpy(), expected.to_numpy()[sessions, v.asset])


def test_graph_runs_are_deterministic_and_seed_sensitive(tmp_path):
    a = load_run(run(net_config(), 3, tmp_path / "a"))
    b = load_run(run(net_config(), 3, tmp_path / "b"))
    c = load_run(run(net_config(), 4, tmp_path / "c"))
    assert a.meta["run_id"] == b.meta["run_id"] != c.meta["run_id"]
    for spec in RUN_TABLES:
        pd.testing.assert_frame_equal(a[spec.name], b[spec.name], check_exact=True)
    assert not a["trades"].equals(c["trades"])


def test_graph_cpp_and_python_runs_have_identical_tables(tmp_path):
    from marketsim.market.cpp_exchange import is_available

    if not is_available():
        pytest.skip("lob_core not built")
    config = net_config(attention=2, observation_delay=0.1, weight_error_std=0.2, herding={"h": 0.4, "followers": 0.5})
    a = load_run(run(config, 5, tmp_path / "python"))
    b = load_run(run(replace(config, exchange_backend="cpp"), 5, tmp_path / "cpp"))
    for spec in RUN_TABLES:
        pd.testing.assert_frame_equal(a[spec.name], b[spec.name], check_exact=True)


def _wave(tmp_path, myopic, network):
    # A maker that leans on its inventory lets one-sided demand move the price within a few sessions.
    config = net_config(graph="wave", external="x_shock", n_sessions=20, valuers_myopic=myopic,
                        valuers_network=network, trend=0, noise=2, n_funds=2, debug=False,
                        inventory_skew_ticks=0.2, herding={"h": 0.0})
    log = load_run(run(config, 11, tmp_path / f"{myopic}_{network}"))
    out = {}
    for asset, name in enumerate(log.meta["assets"]):
        times, prices = mids(log, asset)
        before = prices[times < 2][-1]
        after = prices[times >= 15].mean()
        ref = log["valuations"].loc[lambda d, a=asset: d.asset == a]
        out[name] = (np.log(after / before), np.log(ref.reference.iloc[-1] / ref.reference.iloc[0]))
    return out


def test_the_wave_reaches_later_links_only_through_network_valuers(tmp_path, wave_data):
    network = _wave(tmp_path, myopic=0, network=24)
    myopic = _wave(tmp_path, myopic=24, network=0)
    for name in ("A", "B", "C"):
        moved, ref = network[name]
        assert ref > 0.1 and moved > 0.5 * ref, (name, moved, ref)
    # Myopic valuers see the oil price, so A moves; B and C have no external parents and barely move.
    assert myopic["A"][0] > 0.5 * myopic["A"][1]
    for name in ("B", "C"):
        moved, ref = myopic[name]
        assert abs(moved) < 0.25 * ref, (name, moved, ref)


def test_observation_delay_slows_the_valuation_response(tmp_path, wave_data):
    def valuation_change(delay):
        config = net_config(graph="wave", external="x_shock", n_sessions=4, valuers_myopic=24, valuers_network=0,
                            trend=0, noise=2, observation_delay=delay, valuation_std=0.0, herding={"h": 0.0})
        v = load_run(run(config, 2, tmp_path / str(delay)))["valuations"]
        a = v.loc[v.asset == 0].set_index("t").v_mean
        return np.log(a.loc[a.index >= 2.5 - 1e-9].iloc[0] / a.loc[a.index < 2].iloc[-1])

    # Half a session after the shock most valuers without delay have seen it; delayed ones mostly have not.
    immediate, delayed = valuation_change(0.0), valuation_change(0.5)
    assert immediate > 0.08
    assert delayed < 0.6 * immediate


def test_weight_errors_spread_valuations(tmp_path, wave_data):
    def spread(error):
        # Six sessions after the shock every valuer has decided again, so the spread is that of beliefs only.
        config = net_config(graph="wave", external="x_shock", n_sessions=8, valuers_myopic=24, valuers_network=0,
                            trend=0, noise=2, weight_error_std=error, valuation_std=0.0, herding={"h": 0.0})
        v = load_run(run(config, 2, tmp_path / str(error)))["valuations"]
        return v.loc[(v.asset == 0) & (v.t == 8.0), "v_std"].iloc[0]

    assert spread(0.0) < 1e-6
    assert spread(0.5) > 50  # ticks: perceived weights 0.8 * (1 +- 0.5) on a 30% oil move


def test_hidden_market_index_is_ignored_by_agents_and_the_reference(tmp_path, wave_data):
    fixtures = wave_data / "fixtures"
    rows = ["session,date,wti,sp500"] + [f"{s},,100.0,{100.0 if s < 2 else 150.0}" for s in range(6)]
    (fixtures / "external_index_jump.csv").write_text("\n".join(rows) + "\n")
    hidden = load_run(run(net_config(external="index_jump", n_sessions=4, use_market_index=False), 1, tmp_path / "h"))
    shown = load_run(run(net_config(external="index_jump", n_sessions=4, use_market_index=True), 1, tmp_path / "s"))
    np.testing.assert_allclose(hidden["valuations"].reference, 10_000)  # start prices 100.00, never changing
    assert shown["valuations"].reference.nunique() > 1


def test_attention_limits_each_investor_to_its_assets(tmp_path):
    log = load_run(run(net_config(attention=1), 3, tmp_path))
    followed = log.meta["population"]["followed_assets"]
    assert all(len(assets) == 1 for assets in followed)
    for frame in (log["signals"], log["orders"].loc[lambda d: d.agent < len(followed)]):
        for agent, assets in frame.groupby("agent").asset:
            assert set(assets) <= set(followed[agent])


def test_heterogeneous_herding_followers_and_spread(tmp_path):
    log = load_run(run(net_config(herding={"h": 0.6, "followers": 0.5}), 3, tmp_path / "a"))
    h = np.array(log.meta["population"]["herding_h"])
    assert (h == 0).sum() == 12 and (h == 0.6).sum() == 12
    log = load_run(run(net_config(herding={"h": 0.5, "h_std": 0.3}), 3, tmp_path / "b"))
    h = np.array(log.meta["population"]["herding_h"])
    assert h.min() >= 0 and h.max() <= 1 and h.std() > 0.1
    log = load_run(run(net_config(herding={"h": 0.9, "followers": 0.0}), 3, tmp_path / "c"))
    np.testing.assert_array_equal(log["signals"].own_signal, log["signals"].final_signal)


def small_config(**agents):
    return RunConfig(
        name="test", exchange_backend="python", graph="single_asset", external="flat",
        use_market_index=False, n_sessions=3, warmup_sessions=1, sample_dt=0.05,
        agents={"valuers": 8, "trend": 4, "noise": 4, "n_funds": 2, "rho": 0.05, "debug": True, **agents},
    )


def test_f4_market_supports_followers_without_changing_the_default(tmp_path):
    log = load_run(run(small_config(herding={"h": 0.9, "followers": 0.0}), 8, tmp_path / "f"))
    np.testing.assert_array_equal(log["signals"].own_signal, log["signals"].final_signal)
    a = load_run(run(small_config(herding={"h": 0.4}), 8, tmp_path / "a"))
    b = load_run(run(small_config(herding={"h": 0.4, "followers": 1.0, "h_std": 0.0}), 8, tmp_path / "b"))
    for spec in RUN_TABLES:  # run_ids differ only because the raw configs differ
        pd.testing.assert_frame_equal(a[spec.name], b[spec.name], check_exact=True)


def test_external_scenario_shorter_than_the_run_keeps_its_last_level(tmp_path):
    log = load_run(run(net_config(n_sessions=12), 3, tmp_path))
    starts = [json.loads(p) for p in log["events"].query("kind == 'session_start'").payload]
    assert [s["session"] for s in starts] == list(range(12))
    assert starts[5]["wti"] == starts[11]["wti"] == 120.0 and starts[4]["wti"] == 100.0


@pytest.mark.parametrize("change", [
    {"graph": "no_such_graph"}, {"graph": "real_v1"}, {"agents": {"valuers": 4, "noise": 4, "n_funds": 1}},
    {"agents": {"valuers_network": 4, "noise": 4, "n_funds": 1, "composition": "nonsense"}},
    {"agents": {"valuers_network": 4, "noise": 4, "n_funds": 1, "herding": {"followers": 1.5}}},
    {"use_market_index": "yes"},
])
def test_invalid_graph_configs_fail_before_creating_output(tmp_path, change):
    output = tmp_path / "output"
    with pytest.raises(ConfigError):
        run(replace(net_config(), **change), 1, output)
    assert not output.exists()


def test_compositions_keep_the_investor_total():
    from marketsim.sim.settings import AgentSettings

    raw = {"valuers_myopic": 36, "valuers_network": 36, "trend": 24, "noise": 24, "n_funds": 12}
    for name in ("network", "mixed", "trend"):
        s = AgentSettings.from_dict(raw | {"composition": name}, graph_mode=True)
        assert s.investors == 120
    trend = AgentSettings.from_dict(raw | {"composition": "trend"}, graph_mode=True)
    network = AgentSettings.from_dict(raw | {"composition": "network"}, graph_mode=True)
    assert trend.trend > network.trend and network.valuers_network > trend.valuers_network


def test_k4_pilot_example_runs_in_graph_mode(tmp_path):
    spec = expand(load_config(paths.PROJECT_ROOT / "configs" / "pilot.yaml"))[0]
    log = load_run(run(replace(spec.config, n_sessions=2, warmup_sessions=1), spec.seed, tmp_path))
    assert log.meta["assets"] == ["COP", "VLO", "DAL"] and len(log["trades"]) > 0


def test_diagnostics_report_every_asset(tmp_path):
    row = diagnose_run(load_run(run(net_config(), 3, tmp_path)))
    for ticker in ("COP", "VLO", "DAL"):
        for field in ("empty_side_fraction", "max_drawdown", "price_log_change", "reference_log_change"):
            assert np.isfinite(row[f"{field}__{ticker}"])
    assert row["reference_log_change__COP"] == pytest.approx(0.516 * np.log(1.2))


def test_herding_network_counts_only_neighbors_following_the_asset():
    from marketsim.agents.herding import HerdingNetwork

    follows = np.array([[True, True], [True, False], [True, True]])
    net = HerdingNetwork(3, 2, 1.0, np.random.default_rng(0), n_assets=2, follows=follows)
    net.publish(1, 1, 0.0, asset=0)
    net.publish(2, -1, 0.0, asset=1)
    herd0, _ = net.mix(0, 0.0, 0.5, 1.0, asset=0)
    herd1, _ = net.mix(0, 0.0, 0.5, 1.0, asset=1)
    assert herd0 == pytest.approx(0.5)   # neighbors 1 and 2 follow asset 0; one bought
    assert herd1 == pytest.approx(-1.0)  # only neighbor 2 follows asset 1; it sold
