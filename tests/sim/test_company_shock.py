"""F7 (E6): a company shock scheduled from the configuration is the live mode's valuation shock in a batch run."""

import json
from dataclasses import replace

import pandas as pd
import pytest

from marketsim.experiments.config import ConfigError, RunConfig
from marketsim.sim.api import run, run_id
from marketsim.sim.live import LiveSession, run_with_controls
from marketsim.sim.log_schema import RUN_TABLES, load_run
from marketsim.sim.settings import AgentSettings, identity_dict


def config(n_sessions=6, **agents):
    return RunConfig(
        name="shock", exchange_backend="python", graph="graph_chain", external="flat", use_market_index=True,
        n_sessions=n_sessions, warmup_sessions=1, sample_dt=0.05,
        agents={"valuers_myopic": 8, "valuers_network": 8, "trend": 4, "noise": 4, "n_funds": 2,
                "total_cash_cents": 6_000_000, "total_shares": 2_400, "debug": True,
                "herding": {"h": 0.2}, **agents},
    )


SHOCK = {"asset": "VLO", "change": -0.3, "time": 2.5}


def test_configured_shock_equals_the_live_control(tmp_path):
    batch = load_run(run(config(company_shock=SHOCK), 4, tmp_path / "batch"))
    live = load_run(run_with_controls(config(), 4, [{"t": 2.5, "kind": "shock_valuation", "asset": "VLO",
                                                      "change": -0.3}], tmp_path / "live"))
    for spec in RUN_TABLES:
        pd.testing.assert_frame_equal(batch[spec.name], live[spec.name], check_exact=True, obj=spec.name)
    shock = batch["events"].query("kind == 'shock_valuation'")
    assert shock["t"].tolist() == [2.5]
    assert json.loads(shock["payload"].iloc[0]) == {"asset": "VLO", "change": -0.3, "asset_index": 1}


def test_the_run_is_unchanged_before_the_shock(tmp_path):
    base = load_run(run(config(), 7, tmp_path / "base"))
    shocked = load_run(run(config(company_shock=SHOCK), 7, tmp_path / "shock"))
    for name in ("trades", "orders", "signals"):
        # Status and filled quantity of an order are its last ones; an order placed before the shock may fill
        # after it.
        a, b = (log[name].drop(columns=["status", "filled_qty"], errors="ignore") for log in (base, shocked))
        pd.testing.assert_frame_equal(a[a.t < 2.5].reset_index(drop=True), b[b.t < 2.5].reset_index(drop=True))
    v, v0 = shocked["valuations"], base["valuations"]
    late = (v.asset == 1) & (v.t >= 4)
    assert v.loc[late, "v_mean"].mean() < 0.85 * v0.loc[late, "v_mean"].mean()


def test_no_shock_keeps_the_run_identity_and_a_zero_change_keeps_the_log(tmp_path):
    # Configs written before company_shock existed keep their run ids: the default is left out of the identity.
    assert "company_shock" not in identity_dict(AgentSettings.from_dict(config().agents, graph_mode=True))
    zero = load_run(run(config(company_shock={**SHOCK, "change": 0.0}), 3, tmp_path / "zero"))
    base = load_run(run(config(), 3, tmp_path / "base"))
    for spec in RUN_TABLES:
        pd.testing.assert_frame_equal(zero[spec.name], base[spec.name], obj=spec.name)


@pytest.mark.parametrize("shock,match", [
    ({"asset": "VLO", "change": -0.2, "when": 2}, "only asset, change, time"),
    ({"asset": "XXX", "change": -0.2, "time": 2}, "not a company"),
    ({"asset": "VLO", "change": -1.0, "time": 2}, "change > -1"),
    ({"asset": "VLO", "change": -0.2, "time": 6}, "before the end"),
    ({"asset": "VLO", "change": float("nan"), "time": 2}, "finite"),
])
def test_invalid_shocks_are_rejected_before_any_output(shock, match, tmp_path):
    with pytest.raises(ConfigError, match=match):
        run(config(company_shock=shock), 1, tmp_path)
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_single_asset_market_and_live_mode_refuse_a_configured_shock():
    single = RunConfig(name="f4", exchange_backend="python", graph="single_asset", external="flat",
                       use_market_index=False, n_sessions=2, warmup_sessions=0, sample_dt=0.1,
                       agents={"company_shock": SHOCK})
    with pytest.raises(ConfigError, match="need a graph"):
        run_id(single, 1)
    with pytest.raises(ConfigError, match="company news from the page"):
        LiveSession(replace(config(), agents={**config().agents, "company_shock": SHOCK}), 1)
