"""F7 analysis: graph relations, residual comovement, paired statistics and the study tables from real runs."""

import json
import math

import numpy as np
import pandas as pd
import pytest
import yaml

from marketsim import paths
from marketsim.analysis import runs as rm
from marketsim.analysis import stats, study
from marketsim.experiments.run import run_experiment
from marketsim.graph.api import DependencyGraph, Edge, Node, load_graph
from marketsim.sim.log_schema import RunLog


def test_relations_on_real_v1_ignore_the_market_index():
    g = load_graph("real_v1")
    assert rm.related(g, "COP", "VLO")            # VLO buys from COP
    assert rm.related(g, "COP", "NEE")            # both follow natural gas
    assert rm.related(g, "GOOGL", "META")         # both buy chips from NVDA and AMD
    assert not rm.related(g, "JPM", "COP")        # only the index connects them
    assert not rm.related(g, "FCX", "NEM")        # copper and gold are different parents
    groups = rm.shock_groups(g, g.companies(), "NVDA")
    assert groups["linked"] == ["GOOGL", "META"]
    assert "JPM" in groups["unrelated"] and "NVDA" not in groups["unrelated"]
    assert not set(groups["linked"]) & set(groups["unrelated"])


def test_residuals_remove_what_the_own_reference_explains():
    rng = np.random.default_rng(1)
    q = pd.DataFrame(rng.normal(0, 0.01, (400, 2)), columns=["A", "B"])
    e = pd.DataFrame(rng.normal(0, 0.002, (400, 2)), columns=["A", "B"])
    r = 0.8 * q + 0.2 * q.shift(1) + e
    u = rm.residuals(r, q)
    assert u.iloc[0].isna().all()
    for c in ("A", "B"):
        assert np.corrcoef(u[c].iloc[1:], e[c].iloc[1:])[0, 1] > 0.99
        assert abs(np.corrcoef(u[c].iloc[1:], q[c].iloc[1:])[0, 1]) < 1e-8


def _graph():
    nodes = {n: Node(n, "external") for n in ("x", "y", "z", "sp500")}
    nodes |= {c: Node(c, "company", c, None, 100.0) for c in ("A", "B", "C")}
    edges = [Edge(p, c, 0.5, 0.5, None, None, "commodity", "test") for p, c in (("x", "A"), ("y", "B"), ("z", "C"))]
    edges += [Edge("sp500", c, 0.5, 0.5, None, None, "index", "test") for c in ("A", "B", "C")]
    return DependencyGraph(nodes, edges, {}).validate()


def _log(common: float, n: int = 300, seed: int = 3) -> RunLog:
    """Three companies the graph does not relate; A and B share a part ``common`` of their own price noise."""
    rng = np.random.default_rng(seed)
    q = rng.normal(0, 0.01, (n, 3))
    shared = rng.normal(0, 0.01, n)
    noise = rng.normal(0, 0.01, (n, 3))
    noise[:, :2] = common * shared[:, None] + math.sqrt(1 - common**2) * noise[:, :2]
    ref = 10_000 * np.exp(np.cumsum(q, axis=0))
    price = 10_000 * np.exp(np.cumsum(q + noise, axis=0))
    t = np.repeat(np.arange(n) + 0.5, 3)
    asset = np.tile(np.arange(3), n)
    mid = np.round(price.ravel())
    book = pd.DataFrame({"t": t, "asset": asset, "best_bid": mid - 1, "best_ask": mid + 1, "bid_qty": 10,
                         "ask_qty": 10, "depth5_bid": 50, "depth5_ask": 50})
    valuations = pd.DataFrame({"t": t, "asset": asset, "reference": ref.ravel(), "v_mean": ref.ravel(), "v_std": 0.0})
    market = 1000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    events = pd.DataFrame({"t": np.arange(n, dtype=float), "kind": "session_start",
                           "payload": [json.dumps({"session": k, "sp500": market[k]}) for k in range(n)]})
    meta = {"run_id": "hand", "assets": ["A", "B", "C"], "config": {"n_sessions": n, "warmup_sessions": 5}}
    trades = pd.DataFrame(columns=["t", "seq", "asset", "price", "qty", "taker_owner", "maker_owner", "taker_side"])
    return RunLog(paths.PROJECT_ROOT, meta, {"book": book, "valuations": valuations, "events": events,
                                             "trades": trades})


def test_residual_comovement_finds_a_shared_price_noise():
    g = _graph()
    shared = rm.comovement_metrics(_log(common=0.9), 5, g)
    alone = rm.comovement_metrics(_log(common=0.0), 5, g)
    assert shared["resid_unrelated"] == pytest.approx(0.81 / 3, abs=0.08)   # one pair of three shares the noise
    assert abs(alone["resid_unrelated"]) < 0.12
    assert math.isnan(shared["resid_related"])                             # no pair is related
    assert shared["false_links"] == pytest.approx(1 / 3)
    assert not math.isnan(shared["resid_unrelated_stress"])


def test_paired_contrast_uses_common_seeds_without_collapses():
    rng = np.random.default_rng(0)
    a = pd.DataFrame({"seed": [1, 2, 3, 4], "m": [2.0, 3.0, 4.0, 100.0], "collapse": [False, False, False, True]})
    b = pd.DataFrame({"seed": [1, 2, 3, 4, 5], "m": [1.0, 1.0, 1.0, 1.0, 1.0], "collapse": False})
    out = stats.paired(a, b, "m", rng, family=3)
    assert out["pairs"] == 3 and out["mean"] == pytest.approx(2.0)
    assert out["ci_low"] >= 1.0 and out["ci_high"] <= 3.0 and out["clear"]
    with pytest.raises(ValueError, match="one run per seed"):
        stats.paired(pd.concat([a, a]), b, "m", rng)
    d = stats.did(a, b, b, b, "m", rng)
    assert d["pairs"] == 3 and d["mean"] == pytest.approx(2.0)


def test_wilson_and_fisher_match_known_values():
    lo, hi = stats.wilson(6, 60)
    assert (round(lo, 3), round(hi, 3)) == (0.047, 0.201)
    assert stats.wilson(0, 30)[0] == 0.0
    assert stats.fisher_greater(6, 60, 0, 210) == pytest.approx(9.84e-5, rel=0.01)
    assert stats.fisher_greater(0, 10, 0, 10) == 1.0


def _experiment(tmp_path, name, grid, agents=None, seeds=(1, 2, 3)):
    base = {"name": name, "exchange_backend": "python", "graph": "graph_chain", "external": "oil_shock",
            "use_market_index": True, "n_sessions": 8, "warmup_sessions": 2, "sample_dt": 0.05,
            "agents": {"valuers_myopic": 8, "valuers_network": 8, "trend": 4, "noise": 4, "n_funds": 2,
                       "total_cash_cents": 6_000_000, "total_shares": 2_400, "position_sizing": "target",
                       "herding": {"h": 0.0}, **(agents or {})}}
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump({"base": base, "seeds": list(seeds), "grid": grid}), encoding="utf-8")
    return path


def test_study_tables_from_small_runs(tmp_path):
    root = tmp_path / "study"
    run_experiment(_experiment(tmp_path, "e5", {"agents.composition": ["mixed", "trend"],
                                                "agents.herding.h": [0.0, 0.2]}, {"composition": "mixed"}),
                   root / "e5")
    tables = study.e5(root)
    c = tables["p5_contrasts"]
    assert set(c.factor) == {"mix", "herding"} and (c.pairs <= 3).all()
    assert set(c.metric) == set(study.P5_METRICS)
    assert (root / "e5" / "p5_metrics.csv").exists() and len(tables["p5_collapses"]) == 4
    # A second call reads the cache: the metrics are not computed again.
    assert len(study.e5(root)["runs"]) == 12

    shock = {"company_shock": {"asset": "COP", "change": -0.3, "time": 4.0}}
    run_experiment(_experiment(tmp_path, "e6", {"agents.company_shock.change": [0.0, -0.3],
                                                "agents.herding.h": [0.0, 0.4]}, shock), root / "e6" / "shock")
    effects = study.e6_shock(root)["p6_shock_effects"]
    target = effects.loc[(effects.metric == "target_1")]
    assert len(target) == 2 and (target["ci_high"] < 0).all()
    by = effects.set_index(["h", "metric"])["mean"]
    assert np.isfinite(by.xs("linked_1", level="metric")).all()      # VLO and DAL descend from COP
    assert by.xs("unrelated_1", level="metric").isna().all()         # the chain has no unrelated company
