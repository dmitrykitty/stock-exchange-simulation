"""End-to-end F4 runs through the real gateway and the frozen K3 reader."""

import copy
import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from marketsim.experiments.config import ConfigError, RunConfig
from marketsim.market.api import NO_PRICE, Status
from marketsim.sim.api import run
from marketsim.sim.log_schema import RUN_TABLES, load_run


def small_config(**agents):
    return RunConfig(
        name="test", exchange_backend="python", graph="single_asset", external="flat",
        use_market_index=False, n_sessions=3, warmup_sessions=1, sample_dt=0.05,
        agents={"valuers": 8, "trend": 4, "noise": 4, "n_funds": 2, "rho": 0.05,
                "debug": True, **agents},
    )


def test_run_trades_and_writes_all_valid_tables_with_conserved_resources(tmp_path):
    config = small_config()
    original = copy.deepcopy(config)
    directory = run(config, 8, tmp_path)
    log = load_run(directory)
    assert config == original
    assert set(log.tables) == {s.name for s in RUN_TABLES}
    assert len(log["trades"]) > 0 and len(log["signals"]) > 0
    assert log["book"].t.iloc[0] == 0.0 and log["book"].t.iloc[-1] == 3.0
    assert (log["trades"].price > 0).all()
    assert (log["trades"].taker_owner != log["trades"].maker_owner).all()
    assert np.isfinite(log["signals"][["own_signal", "herd_signal", "final_signal"]]).all().all()
    assert (log["signals"].final_signal.abs() <= 1).all()
    assert (log["valuations"].reference == 10000.0).all()
    initial, final = log.meta["initial_resources"], log.meta["final_resources"]
    assert initial["cash_cents"] == final["cash_cents"]
    assert initial["shares"] == final["shares"]
    assert final["cash_reserved"] == final["shares_reserved"] == 0
    assert log.meta["invariant_checks"] > 10
    assert (log["book"].bid_qty >= 0).all()


def test_same_config_seed_produces_identical_tables_and_different_seed_changes_decisions(tmp_path):
    config = small_config()
    a = load_run(run(config, 8, tmp_path / "a"))
    b = load_run(run(config, 8, tmp_path / "b"))
    c = load_run(run(config, 9, tmp_path / "c"))
    assert a.meta["run_id"] == b.meta["run_id"] != c.meta["run_id"]
    for spec in RUN_TABLES:
        pd.testing.assert_frame_equal(a[spec.name], b[spec.name], check_exact=True)
    assert not a["signals"].equals(c["signals"])


def test_zero_herding_ignores_neighbors_and_gtd_expiry_is_logged(tmp_path):
    log = load_run(run(small_config(order_lifetime=0.001, herding={"h": 0.0}), 2, tmp_path))
    np.testing.assert_array_equal(log["signals"].own_signal, log["signals"].final_signal)
    expired = log["events"].query("kind == 'order_expired'")
    assert len(expired) > 0
    assert all(json.loads(p)["remaining_qty"] > 0 for p in expired.payload)


def test_maker_fills_are_counted_and_final_orders_match_both_trade_sides(tmp_path):
    log = load_run(run(small_config(), 8, tmp_path))
    orders = log["orders"]
    assert not (orders.status == int(Status.REJECTED_SELF_TRADE)).any()
    # Every executed share belongs to one buy and one sell order, including passive makers.
    assert orders.filled_qty.sum() == 2 * log["trades"].qty.sum()
    assert (orders.filled_qty <= orders.qty).all()
    assert (orders.loc[orders.filled_qty == orders.qty, "status"] == int(Status.FILLED)).all()


def test_empty_market_and_no_valuers_still_produce_typed_valid_logs(tmp_path):
    log = load_run(run(small_config(
        valuers=0, trend=4, noise=0, market_makers_per_asset=0, n_funds=1,
    ), 3, tmp_path))
    assert log["trades"].empty and log["orders"].empty
    assert (log["book"].best_bid == NO_PRICE).all()
    assert (log["valuations"].v_mean == 10000.0).all()


@pytest.mark.parametrize("change", [
    {"graph": "graph_chain"}, {"external": "oil_shock"}, {"sample_dt": float("nan")},
    {"n_sessions": 2.5}, {"agents": {"valuers": -1}}, {"use_market_index": True},
    {"sample_dt": None}, {"n_sessions": "3"}, {"warmup_sessions": True},
])
def test_invalid_or_unsupported_config_fails_before_creating_output(tmp_path, change):
    output = tmp_path / "output"
    with pytest.raises(ConfigError):
        run(replace(small_config(), **change), 1, output)
    assert not output.exists()


def test_existing_run_is_never_overwritten(tmp_path):
    path = run(small_config(), 8, tmp_path)
    meta = (path / "meta.json").read_bytes()
    with pytest.raises(FileExistsError):
        run(small_config(), 8, tmp_path)
    assert (path / "meta.json").read_bytes() == meta


@pytest.mark.parametrize("concentration", [0.0, 1.0])
def test_extreme_concentration_preserves_resources(tmp_path, concentration):
    log = load_run(run(small_config(concentration=concentration), 8, tmp_path))
    assert log.meta["initial_resources"] == log.meta["final_resources"]


def test_ablations_disable_herding_and_replace_trend_without_changing_population(tmp_path):
    log = load_run(run(small_config(ablation="no_herding", herding={"h": 0.6}), 8, tmp_path / "h"))
    np.testing.assert_array_equal(log["signals"].own_signal, log["signals"].final_signal)
    a = load_run(run(small_config(valuers=0, trend=4, noise=0, n_funds=1), 8, tmp_path / "a"))
    b = load_run(run(small_config(valuers=0, trend=4, noise=0, n_funds=1, ablation="no_trend"), 8, tmp_path / "b"))
    assert a.meta["population"] == b.meta["population"]
    assert a.meta["initial_resources"] == b.meta["initial_resources"]
    assert (a["signals"].own_signal == 0).all()
    assert (b["signals"].own_signal != 0).any()


def test_cpp_and_python_runs_have_identical_tables(tmp_path):
    from marketsim.market.cpp_exchange import is_available

    if not is_available():
        pytest.skip("lob_core not built")
    config = small_config()
    a = load_run(run(config, 8, tmp_path / "python"))
    b = load_run(run(replace(config, exchange_backend="cpp"), 8, tmp_path / "cpp"))
    for spec in RUN_TABLES:
        pd.testing.assert_frame_equal(a[spec.name], b[spec.name], check_exact=True)


def test_agents_without_resources_do_not_influence_neighbors(tmp_path):
    log = load_run(run(small_config(
        valuers=0, trend=0, noise=4, n_funds=1, market_makers_per_asset=0,
        total_cash_cents=0, total_shares=0, herding={"h": 0.6, "neighbors": 3},
    ), 8, tmp_path))
    assert log["orders"].empty
    assert (log["signals"].own_signal != 0).any()
    assert (log["signals"].herd_signal == 0).all()
    np.testing.assert_allclose(log["signals"].final_signal, 0.4 * log["signals"].own_signal)
