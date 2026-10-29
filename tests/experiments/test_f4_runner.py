"""Small real parameter grids and hand-computed diagnostics, without network data."""

import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from marketsim import paths
from marketsim.experiments.config import expand, load_config
from marketsim.sim.log_schema import RunLog


def test_diagnostics_exclude_warmup_and_weight_empty_time():
    from marketsim.experiments.diagnostics import diagnose_run

    book = pd.DataFrame({
        "t": [0.0, 1.0, 2.0, 3.0], "best_bid": [1, 9900, np.iinfo(np.int64).min, 8900],
        "best_ask": [2, 10100, 10100, 9100], "depth5_bid": [1, 20, 0, 10],
        "depth5_ask": [1, 30, 30, 10],
    })
    trades = pd.DataFrame({"t": [0.5, 1.5], "qty": [1000, 3]})
    orders = pd.DataFrame({"t": [0.5, 1.5], "qty": [1000, 10], "filled_qty": [1000, 3]})
    valuations = pd.DataFrame({"t": [0, 1, 2, 3], "v_mean": [10000.0] * 4})
    log = RunLog(paths.PROJECT_ROOT, {
        "run_id": "hand_checked", "seed": 7, "wall_time_s": 1,
        "config": {"n_sessions": 4, "warmup_sessions": 1},
    }, {"book": book, "trades": trades, "orders": orders, "valuations": valuations})
    row = diagnose_run(log)
    assert row["empty_side_fraction"] == pytest.approx(1 / 3)
    assert row["max_drawdown"] == pytest.approx(0.1)
    assert row["execution_rate"] == pytest.approx(0.3)
    assert row["trade_count"] == 1 and row["traded_shares"] == 3
    assert row["relative_valuation_error"] == pytest.approx(0.05)


def test_small_grid_runs_and_writes_summary_with_overrides(tmp_path):
    from marketsim.experiments.run import run_experiment

    config = {
        "base": {
            "name": "test_grid", "exchange_backend": "python", "graph": "single_asset", "external": "flat",
            "use_market_index": False, "n_sessions": 2, "warmup_sessions": 0, "sample_dt": 0.1,
            "agents": {"valuers": 4, "trend": 2, "noise": 2, "n_funds": 1, "herding": {"h": 0.0}},
        }, "seeds": [11], "grid": {"agents.herding.h": [0.0, 0.6]},
    }
    path = tmp_path / "grid.yaml"
    path.write_text(yaml.safe_dump(config))
    summary = run_experiment(path, tmp_path / "runs")
    assert len(summary) == 2
    assert summary["agents.herding.h"].tolist() == [0.0, 0.6]
    assert (tmp_path / "runs" / "summary.csv").exists()
    assert all((tmp_path / "runs" / rid / "meta.json").exists() for rid in summary.run_id)
    metadata = json.loads((tmp_path / "runs" / "experiment.json").read_text())
    assert metadata["config"] == config


def test_empty_time_includes_interval_straddling_warmup():
    from marketsim.experiments.diagnostics import diagnose_run

    missing = np.iinfo(np.int64).min
    book = pd.DataFrame({
        "t": [0, 0.6, 1.2, 1.8, 2.0], "best_bid": [missing, missing, 9900, 9900, 9900],
        "best_ask": [10100] * 5, "depth5_bid": [0, 0, 10, 10, 10], "depth5_ask": [10] * 5,
    })
    log = RunLog(paths.PROJECT_ROOT, {
        "run_id": "off_grid", "seed": 1, "wall_time_s": 0,
        "config": {"warmup_sessions": 1, "n_sessions": 2},
    }, {"book": book, "valuations": pd.DataFrame({"t": [0, 1.2], "v_mean": [10000.0, 10000.0]}),
        "trades": pd.DataFrame({"t": [], "qty": []}),
        "orders": pd.DataFrame({"t": [], "qty": [], "filled_qty": []})})
    assert diagnose_run(log)["empty_side_fraction"] == pytest.approx(0.2)


def test_pilot_cli_produces_a_valid_run_and_summary(tmp_path):
    result = subprocess.run([
        sys.executable, "-m", "marketsim.experiments.run", "configs/f4_pilot.yaml",
        "--out", str(tmp_path / "pilot"), "--limit", "1",
    ], check=False, cwd=paths.PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(pd.read_csv(tmp_path / "pilot" / "summary.csv")) == 1


def test_main_experiment_grids_have_30_fresh_seeds_and_expected_sizes():
    pilot_seeds = set(load_config(paths.PROJECT_ROOT / "configs/f4_pilot.yaml").seeds)
    for name, expected in (("e1", 270), ("e2", 90), ("e3", 90)):
        config = load_config(paths.PROJECT_ROOT / f"configs/f4_{name}.yaml")
        assert len(config.seeds) == 30 and not pilot_seeds.intersection(config.seeds)
        assert len(expand(config)) == expected
