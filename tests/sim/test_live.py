"""F9 live mode: control events change the running market, are logged and replay into the same log."""

import json

import numpy as np
import pandas as pd
import pytest

from marketsim.experiments.config import ConfigError, RunConfig
from marketsim.sim.api import run
from marketsim.sim.live import LiveSession, check, run_with_controls
from marketsim.sim.log_schema import RUN_TABLES, load_run


def live_config(n_sessions=6, **agents):
    return RunConfig(
        name="live", exchange_backend="python", graph="graph_chain", external="flat", use_market_index=True,
        n_sessions=n_sessions, warmup_sessions=1, sample_dt=0.05,
        agents={"valuers_myopic": 8, "valuers_network": 8, "trend": 4, "noise": 4, "n_funds": 2,
                "total_cash_cents": 6_000_000, "total_shares": 2_400, "debug": True,
                "herding": {"h": 0.2, "followers": 0.5, "h_std": 0.1}, **agents},
    )


def assert_same_tables(a, b):
    for spec in RUN_TABLES:
        pd.testing.assert_frame_equal(a[spec.name], b[spec.name], check_exact=True, obj=spec.name)


def live_run(config, seed, out_dir, stop_after=None):
    """Steps with controls entered between them, as the live page would, at uneven moments."""
    session = LiveSession(config, seed)
    for step in range(10_000):
        if session.done or (stop_after is not None and session.now >= stop_after):
            break
        if step == 23:
            session.control("shock_external", node="wti", change=0.3)
        if step == 41:
            session.control("set_herding", h=0.6)
            session.control("shock_valuation", asset="VLO", change=-0.2)
        if step == 77:
            session.control("set_herding", h=0.0)
        session.step()
        session.frame()
    session.stop()
    return session, session.write(out_dir)


def test_controls_are_logged_and_replay_into_the_same_log(tmp_path):
    config = live_config()
    _, path = live_run(config, 5, tmp_path / "live")
    log = load_run(path)
    controls = log.meta["controls"]
    assert [c["kind"] for c in controls] == ["shock_external", "set_herding", "shock_valuation", "set_herding"]
    # The oil shock starts at the next session open, the others at the step they were entered.
    assert controls[0]["t"] == 2.0 and controls[1]["t"] == pytest.approx(41 * 0.05)
    events = log["events"]
    logged = events[events.kind.isin(["shock_external", "shock_valuation", "set_herding"])]
    assert logged["kind"].tolist() == [c["kind"] for c in controls]
    np.testing.assert_allclose(logged["t"], [c["t"] for c in controls])
    shock = json.loads(logged.iloc[0]["payload"])
    assert shock["node"] == "wti" and shock["session"] == 2
    assert shock["level_after"] == pytest.approx(1.3 * shock["level_before"])
    # The same seed and the same timed controls, all scheduled before the start, give the same log and run id.
    again = run_with_controls(RunConfig(**log.meta["config"]), log.meta["seed"], controls, tmp_path / "replay")
    assert again.name == path.name
    assert_same_tables(log, load_run(again))
    assert log.meta["initial_resources"] == log.meta["final_resources"]
    assert check(path) == []  # python -m marketsim.sim.live <run_dir>


def test_display_pace_and_stop_point_do_not_change_the_log(tmp_path):
    """Stopping early equals a run configured with fewer sessions; a longer configured run gives the same
    sessions."""
    short, path_short = live_run(live_config(n_sessions=6), 9, tmp_path / "a")
    long, path_long = live_run(live_config(n_sessions=12), 9, tmp_path / "b", stop_after=5.3)
    assert short.end == long.end == 6
    assert path_short.name == path_long.name
    assert_same_tables(load_run(path_short), load_run(path_long))


def test_without_controls_the_live_log_equals_a_batch_run(tmp_path):
    config = live_config(n_sessions=4)
    batch = load_run(run(config, 11, tmp_path / "batch"))
    session = LiveSession(config, 11)
    while not session.done:
        session.step()
    assert_same_tables(batch, load_run(session.write(tmp_path / "live")))


def test_each_control_changes_what_it_should(tmp_path):
    config = live_config(n_sessions=8)
    base = load_run(run_with_controls(config, 4, [], tmp_path / "base"))
    oil = load_run(run_with_controls(config, 4, [{"t": 3.0, "kind": "shock_external", "node": "wti", "change": 0.3}],
                                     tmp_path / "oil"))
    # Session levels and the reference move from session 3 on, not before.
    starts = [json.loads(p) for p in oil["events"].query("kind == 'session_start'")["payload"]]
    assert [s["wti"] for s in starts[:3]] == [starts[0]["wti"]] * 3
    assert starts[3]["wti"] == pytest.approx(1.3 * starts[0]["wti"])
    ref, ref0 = oil["valuations"], base["valuations"]
    before, after = ref.t < 3, ref.t >= 3
    np.testing.assert_allclose(ref.loc[before, "reference"], ref0.loc[before, "reference"])
    assert (ref.loc[after & (ref.asset == 0), "reference"] > ref0.loc[after & (ref0.asset == 0), "reference"]).all()

    crash = load_run(run_with_controls(
        config, 4, [{"t": 2.0, "kind": "shock_valuation", "asset": "VLO", "change": -0.3}], tmp_path / "crash"))
    vlo = crash.meta["assets"].index("VLO")

    def late(log):
        v = log["valuations"]
        return v.loc[(v.asset == vlo) & (v.t >= 4), "v_mean"].mean()

    assert late(crash) < 0.85 * late(base)

    herd = load_run(run_with_controls(config, 4, [{"t": 1.0, "kind": "set_herding", "h": 0.9}], tmp_path / "h"))
    payload = json.loads(herd["events"].query("kind == 'set_herding'")["payload"].iloc[0])
    assert payload["followers"] == 12  # half of the 24 investors
    assert payload["mean_h"] == pytest.approx(0.9, abs=0.1)
    # Signals before the change are identical; afterwards the followers lean on their neighbours.
    s, s0 = herd["signals"], base["signals"]
    pd.testing.assert_frame_equal(s[s.t < 1].reset_index(drop=True), s0[s0.t < 1].reset_index(drop=True))
    assert not s[s.t >= 1].reset_index(drop=True).equals(s0[s0.t >= 1].reset_index(drop=True))


@pytest.mark.parametrize("time,kind,params,match", [
    (2.5, "shock_external", {"node": "wti", "change": 0.3}, "session open"),
    (2.0, "shock_external", {"node": "nickel", "change": 0.3}, "external signal"),
    (2.0, "shock_external", {"node": "wti", "change": -1.0}, "change"),
    (2.0, "shock_valuation", {"asset": "XXX", "change": -0.2}, "not an asset"),
    (2.0, "set_herding", {"h": 1.5}, r"\[0, 1\]"),
    (2.0, "set_herding", {"h": 0.5, "extra": 1}, "keys"),
    (9.0, "set_herding", {"h": 0.5}, "must be in"),
    (2.0, "buy_everything", {}, "kind"),
])
def test_invalid_controls_are_rejected(time, kind, params, match):
    session = LiveSession(live_config(), 1)
    with pytest.raises(ConfigError, match=match):
        session.sim.schedule_control(time, kind, params)


def test_a_control_cannot_be_scheduled_in_the_simulated_past():
    session = LiveSession(live_config(), 1)
    for _ in range(30):
        session.step()
    with pytest.raises(ConfigError, match="must be in"):
        session.sim.schedule_control(1.0, "set_herding", {"h": 0.5})
    assert session.control("shock_external", node="wti", change=0.1)["t"] == 2.0
    assert session.control("set_herding", h=0.4)["t"] == session.now


def test_frame_has_one_value_per_asset_and_external():
    session = LiveSession(live_config(), 2)
    for _ in range(25):
        session.step()
    frame = session.frame()
    n = len(session.assets)
    for key in ("price", "bid", "ask", "depth_bid", "depth_ask", "vmean", "ref"):
        assert len(frame[key]) == n, key
    assert len(frame["ext"]) == len(session.external_names)
    assert frame["session"] == 1 and frame["t"] == pytest.approx(1.25)
    assert session.frame()["candles"] == []  # trades are reported once
    json.dumps(frame, allow_nan=False)
