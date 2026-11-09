"""Parallel grid execution gives the same logs and summary as the sequential one."""

import pandas as pd
import pytest
import yaml

from marketsim.experiments.run import run_experiment
from marketsim.sim.log_schema import RUN_TABLES, load_run


def _grid(tmp_path, agents_extra=None):
    agents = {"valuers": 4, "trend": 2, "noise": 2, "n_funds": 1, "herding": {"h": 0.0}} | (agents_extra or {})
    config = {
        "base": {
            "name": "parallel_grid", "exchange_backend": "python", "graph": "single_asset", "external": "flat",
            "use_market_index": False, "n_sessions": 2, "warmup_sessions": 0, "sample_dt": 0.1, "agents": agents,
        }, "seeds": [11, 12], "grid": {"agents.herding.h": [0.0, 0.6]},
    }
    path = tmp_path / "grid.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def test_parallel_runs_match_sequential_runs(tmp_path):
    path = _grid(tmp_path)
    sequential = run_experiment(path, tmp_path / "seq")
    parallel = run_experiment(path, tmp_path / "par", workers=2)

    timing = ["wall_time_s"]
    pd.testing.assert_frame_equal(sequential.drop(columns=timing), parallel.drop(columns=timing))
    on_disk = pd.read_csv(tmp_path / "par" / "summary.csv")
    assert on_disk.run_id.tolist() == sequential.run_id.tolist()
    for run_id in sequential.run_id:
        seq_log, par_log = load_run(tmp_path / "seq" / run_id), load_run(tmp_path / "par" / run_id)
        for spec in RUN_TABLES:
            pd.testing.assert_frame_equal(seq_log[spec.name], par_log[spec.name])


def test_worker_errors_reach_the_caller(tmp_path):
    with pytest.raises(Exception, match="unknown agents keys"):
        run_experiment(_grid(tmp_path, {"not_a_parameter": 1}), tmp_path / "par", workers=2)


@pytest.mark.parametrize("workers", [0, -1, 1.5])
def test_workers_must_be_a_positive_integer(tmp_path, workers):
    with pytest.raises(ValueError, match="workers"):
        run_experiment(_grid(tmp_path), tmp_path / "par", workers=workers)
