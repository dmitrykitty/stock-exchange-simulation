"""Parallel grid execution gives the same logs and summary as the sequential one."""

import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event

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


def test_summary_is_saved_before_all_runs_finish(tmp_path, monkeypatch):
    runner = importlib.import_module("marketsim.experiments.run")
    release = Event()
    written = Event()
    original_to_csv = pd.DataFrame.to_csv

    def run_one(spec, out_dir):
        if spec.seed != 11 or spec.overrides["agents.herding.h"] != 0.0:
            assert release.wait(10), "unfinished runs were not released"
        return {"run_id": f"{spec.seed}_{spec.overrides['agents.herding.h']}"}

    def write_summary(frame, *args, **kwargs):
        result = original_to_csv(frame, *args, **kwargs)
        written.set()
        return result

    # Exercise the real scheduling and writing loop; threads let the test hold tasks with Events.
    monkeypatch.setattr(runner, "ProcessPoolExecutor", lambda **kw: ThreadPoolExecutor(kw["max_workers"]))
    monkeypatch.setattr(runner, "_run_one", run_one)
    monkeypatch.setattr(pd.DataFrame, "to_csv", write_summary)
    output = tmp_path / "par"
    with ThreadPoolExecutor(1) as caller:
        result = caller.submit(run_experiment, _grid(tmp_path), output, workers=2)
        try:
            assert written.wait(3), "a completed run must be saved while other runs are blocked"
            assert pd.read_csv(output / "summary.csv").run_id.tolist() == ["11_0.0"]
            assert not result.done()
        finally:
            release.set()
            result.result(timeout=10)


@pytest.mark.parametrize("workers", [0, -1, 1.5])
def test_workers_must_be_a_positive_integer(tmp_path, workers):
    with pytest.raises(ValueError, match="workers"):
        run_experiment(_grid(tmp_path), tmp_path / "par", workers=workers)
