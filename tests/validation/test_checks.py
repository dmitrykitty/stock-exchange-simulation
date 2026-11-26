"""F8 checks on a synthetic "reality" built on the real_v1 graph: known answers without the data files."""

import numpy as np
import pandas as pd
import pytest

from marketsim.graph.api import load_graph
from marketsim.graph.reference import reference_path
from marketsim.validation import checks
from marketsim.validation.reality import Reality, SimRun


@pytest.fixture(scope="module")
def reality():
    graph = load_graph("real_v1")
    tickers = graph.companies()
    rng = np.random.default_rng(0)
    n = 300
    names = graph.externals()
    levels = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.015, (n, len(names))), axis=0)), columns=names,
                          index=pd.RangeIndex(n, name="session"))
    dates = pd.bdate_range("2024-01-02", periods=n)
    ref = np.log(reference_path(graph, levels, mode="full")).diff().iloc[1:][tickers]
    ref.index = dates[1:]
    signals = np.log(levels).diff().iloc[1:]
    signals.index = dates[1:]
    real = ref + rng.normal(0, 0.01, ref.shape)
    real = real.join(signals)
    prices = pd.DataFrame(100 * np.exp(np.vstack([np.zeros(len(tickers)), real[tickers].cumsum().to_numpy()])),
                          columns=tickers)
    volume = pd.DataFrame(1e6, index=prices.index, columns=tickers)
    beta = pd.Series(1.0, index=tickers)
    beta_only = pd.DataFrame({t: real["sp500"] for t in tickers})
    return Reality(graph, tickers, levels, real, prices, volume, real, beta, ref, beta_only)


def _sim(reality, returns, run_id="s", seed=1):
    prices = pd.DataFrame(100 * np.exp(np.vstack([np.zeros(len(reality.tickers)),
                                                   returns[reality.tickers].fillna(0).cumsum().to_numpy()])),
                          columns=reality.tickers)
    volume = pd.DataFrame(np.random.default_rng(seed).integers(1, 100, prices.shape), columns=reality.tickers)
    return SimRun(run_id, returns[reality.tickers], prices, volume)


def test_a_simulation_that_is_the_real_market_passes_every_comparison(reality):
    sims = {"copy": [_sim(reality, reality.real)]}
    structure = checks.correlation_structure(reality, sims, warmup=0).set_index("series")
    assert structure.loc["sim: copy", "corr with real matrix"] == pytest.approx(1.0)
    assert structure.loc["sim: copy", "mean |difference|"] == pytest.approx(0.0, abs=1e-12)
    bands = checks.bands(reality, [_sim(reality, reality.real, seed=k) for k in range(3)], warmup=0)
    assert (bands["inside band"] == 1.0).all()
    table, detail = checks.event_study(reality, sims, [], "wti", warmup=0)
    row = table.set_index("model").loc["sim: copy"]
    assert row["corr across companies"] == pytest.approx(1.0) and row["size ratio"] == pytest.approx(1.0)
    assert len(detail) == len(reality.tickers)


def test_the_graph_is_rebuilt_from_returns_that_follow_it(reality):
    summary, edges = checks.graph_recovery(reality, {"copy": [_sim(reality, reality.real)]})
    s = summary.set_index("series")
    assert s.loc["sim: copy", "weight corr"] > 0.8
    assert s.loc["sim: copy", "wrong sign"] < 0.1
    assert set(edges.columns) >= {"edge", "planted (2021-2023)", "sim: copy"}


def test_lag_test_sees_a_market_one_day_behind(reality):
    # Prices that take their parents' moves one day late: yesterday's parent predicts today's child.
    late = reality.ref.shift(1) + np.random.default_rng(1).normal(0, 0.002, reality.ref.shape)
    lag = checks.lag_test(reality, {"late": [_sim(reality, late)]}, warmup=1).set_index("series")
    assert lag.loc["sim: late", "share |t| > 2"] > lag.loc["real", "share |t| > 2"] + 0.3


def test_stylised_facts_have_one_row_per_series(reality):
    facts = checks.stylised_facts(reality, {"copy": [_sim(reality, reality.real)]}, warmup=0)
    assert list(facts["series"]) == ["real 2024-2025", "R (real_v1)", "sim: copy"]
    assert facts.set_index("series").loc["sim: copy", "corr with real returns"] == pytest.approx(1.0)


def test_band_paths_and_correlation_pairs_have_every_session_and_pair(reality):
    runs = [_sim(reality, reality.real, seed=k) for k in range(3)]
    paths = checks.band_paths(reality, runs)
    assert len(paths) == len(reality.prices) * len(reality.tickers)
    assert (paths.sim_lo <= paths.sim_median).all() and (paths.sim_median <= paths.sim_hi).all()
    first = paths.loc[paths.session == 0]
    np.testing.assert_allclose(first.R, first.real)
    pairs = checks.correlation_pairs(reality, runs, warmup=0)
    assert len(pairs) == len(reality.tickers) ** 2
    np.testing.assert_allclose(pairs.loc[pairs.a == pairs.b, ["real", "sim"]], 1.0)
    np.testing.assert_allclose(pairs.real, pairs.sim)


def test_comovement_is_near_zero_for_independent_noise_and_high_for_a_common_one(reality):
    rng = np.random.default_rng(5)
    noise = reality.real.copy()
    noise[reality.tickers] = reality.ref + rng.normal(0, 0.01, reality.ref.shape)
    common = reality.real.copy()
    common[reality.tickers] = reality.ref.to_numpy() + rng.normal(0, 0.01, (len(reality.ref), 1))
    table = checks.comovement(reality, {"noise": [_sim(reality, noise)], "common": [_sim(reality, common)]},
                              warmup=0).set_index("series")
    assert abs(table.loc["real 2024-2025", "resid_unrelated"]) < 0.05
    assert abs(table.loc["sim: noise", "resid_unrelated"]) < 0.05
    assert table.loc["sim: common", "resid_unrelated"] > 0.9
    assert table.loc["sim: common", "false_links"] == pytest.approx(1.0)


def test_out_of_sample_reports_r_without_company_edges(reality):
    per, summary = checks.out_of_sample(reality, [reality.beta_only])
    assert checks.DIRECT in per.columns
    assert checks.DIRECT in set(summary.model)
    # the synthetic real returns follow the full R, so the full R fits at least as well as the direct one
    assert per["R (real_v1)"].median() >= per[checks.DIRECT].median() - 1e-12
