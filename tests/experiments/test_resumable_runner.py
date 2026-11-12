"""Resuming an interrupted grid and splitting it into shards give the same runs and summary as one execution."""

import shutil

import pandas as pd
import pytest
import yaml

from marketsim.data.schemas import ContractError
from marketsim.experiments import run as runner
from marketsim.experiments.config import expand, load_config
from marketsim.experiments.run import main, parse_shard, run_experiment

TIMING = ["wall_time_s"]


def _grid(tmp_path, seeds=(11, 12, 13), name="resumable_grid"):
    config = {
        "base": {
            "name": name, "exchange_backend": "python", "graph": "single_asset", "external": "flat",
            "use_market_index": False, "n_sessions": 2, "warmup_sessions": 0, "sample_dt": 0.1,
            "agents": {"valuers": 4, "trend": 2, "noise": 2, "n_funds": 1, "herding": {"h": 0.0}},
        }, "seeds": list(seeds), "grid": {"agents.herding.h": [0.0, 0.6]},
    }
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def _meta_times(directory, run_ids):
    return {rid: (directory / rid / "meta.json").stat().st_mtime_ns for rid in run_ids}


@pytest.fixture
def simulations(monkeypatch):
    """Count the runs actually simulated in this process."""
    calls = []
    original = runner.run

    def counting_run(config, seed, out_dir):
        calls.append(seed)
        return original(config, seed, out_dir)

    monkeypatch.setattr(runner, "run", counting_run)
    return calls


def test_resume_reuses_complete_runs_and_keeps_the_summary(tmp_path, simulations):
    path = _grid(tmp_path)
    reference = run_experiment(path, tmp_path / "once")
    simulations.clear()

    out = tmp_path / "chunks"
    first = run_experiment(path, out, limit=2)
    assert len(simulations) == 2
    before = _meta_times(out, first.run_id)
    resumed = run_experiment(path, out)
    assert len(simulations) == 6, "only the four missing runs are simulated"
    assert _meta_times(out, first.run_id) == before, "complete runs are not rewritten"
    pd.testing.assert_frame_equal(resumed.drop(columns=TIMING), reference.drop(columns=TIMING))
    pd.testing.assert_frame_equal(pd.read_csv(out / "summary.csv").drop(columns=TIMING),
                                  pd.read_csv(tmp_path / "once" / "summary.csv").drop(columns=TIMING))

    again = run_experiment(path, out)
    assert len(simulations) == 6, "a finished experiment simulates nothing"
    pd.testing.assert_frame_equal(again, resumed)


def test_parallel_resume_reuses_complete_runs(tmp_path):
    path = _grid(tmp_path)
    out = tmp_path / "par"
    first = run_experiment(path, out, limit=3, workers=2)
    before = _meta_times(out, first.run_id)
    resumed = run_experiment(path, out, workers=2)
    assert _meta_times(out, first.run_id) == before
    assert len(resumed) == 6 and resumed.run_id.is_unique
    pd.testing.assert_frame_equal(resumed.iloc[:3].reset_index(drop=True), first)


@pytest.mark.parametrize("damage", ["table", "meta", "run_id"])
def test_incomplete_run_directory_is_an_error(tmp_path, damage):
    path = _grid(tmp_path)
    out = tmp_path / "runs"
    run_id = run_experiment(path, out, limit=1).run_id[0]
    if damage == "table":
        (out / run_id / "trades.parquet").unlink()
    elif damage == "meta":
        (out / run_id / "meta.json").write_text("{")
    else:
        meta = (out / run_id / "meta.json").read_text()
        (out / run_id / "meta.json").write_text(meta.replace(run_id, "f4_another"))
    with pytest.raises(ContractError, match="incomplete run"):
        run_experiment(path, out)


def test_output_directory_keeps_one_experiment_config(tmp_path):
    out = tmp_path / "runs"
    run_experiment(_grid(tmp_path), out, limit=1)
    with pytest.raises(ValueError, match="different experiment config"):
        run_experiment(_grid(tmp_path, seeds=(21,), name="other_grid"), out, limit=1)


def test_shards_partition_the_grid_in_balanced_interleaved_slices(tmp_path, monkeypatch):
    path = _grid(tmp_path)
    labels = [f"{spec.seed}_{spec.overrides['agents.herding.h']}" for spec in expand(load_config(path))]
    monkeypatch.setattr(runner, "_run_one",
                        lambda spec, out_dir: {"run_id": f"{spec.seed}_{spec.overrides['agents.herding.h']}"})
    for count in (1, 2, 4, 6):
        parts = [run_experiment(path, tmp_path / f"n{count}_{i}", shard=(i, count)).run_id.tolist()
                 for i in range(1, count + 1)]
        assert sorted(rid for part in parts for rid in part) == sorted(labels), "every grid entry is in exactly one shard"
        assert max(map(len, parts)) - min(map(len, parts)) <= 1
        assert parts[0] == labels[::count], "shards interleave, so each gets every grid combination"
    with pytest.raises(ValueError, match="no runs"):
        run_experiment(path, tmp_path / "empty", shard=(7, 7))
    limited = run_experiment(path, tmp_path / "limited", limit=4, shard=(2, 2)).run_id.tolist()
    assert limited == labels[:4][1::2], "limit applies before the shard"


def test_merged_shards_summarize_without_running_again(tmp_path, simulations):
    path = _grid(tmp_path)
    reference = run_experiment(path, tmp_path / "once")
    simulations.clear()

    merged = tmp_path / "merged"
    shard_ids = []
    for index in (1, 2, 3):
        part = run_experiment(path, tmp_path / f"shard{index}", shard=(index, 3))
        assert part.run_id.tolist() == reference.run_id.tolist()[index - 1::3]
        shard_ids += part.run_id.tolist()
        shutil.copytree(tmp_path / f"shard{index}", merged, dirs_exist_ok=True)
    assert sorted(shard_ids) == sorted(reference.run_id) and len(simulations) == 6

    combined = run_experiment(path, merged)
    assert len(simulations) == 6, "merging shards simulates nothing"
    pd.testing.assert_frame_equal(combined.drop(columns=TIMING), reference.drop(columns=TIMING))


def test_shard_cli_and_parsing(tmp_path, capsys):
    path = _grid(tmp_path)
    assert main([str(path), "--out", str(tmp_path / "cli"), "--shard", "2/4"]) == 0
    summary = pd.read_csv(tmp_path / "cli" / "summary.csv")
    assert len(summary) == 2
    assert main([str(path), "--out", str(tmp_path / "cli"), "--shard", "2/4"]) == 0
    assert capsys.readouterr().out.count("(already on disk)") == 2
    assert parse_shard("3/3") == (3, 3)
    for bad in ("0/3", "4/3", "1", "a/b", "1/2/3"):
        with pytest.raises(ValueError, match="shard"):
            parse_shard(bad)
    for bad in ((0, 2), (3, 2), (1.0, 2), (1,)):
        with pytest.raises(ValueError, match="shard"):
            run_experiment(path, tmp_path / "bad", shard=bad)
    with pytest.raises(SystemExit):
        main([str(path), "--out", str(tmp_path / "cli"), "--shard", "5/4"])


def test_run_id_matches_the_directory_written_by_run(tmp_path):
    from marketsim.experiments.config import RunConfig
    from marketsim.sim.api import run, run_id

    graph = RunConfig(
        name="net", exchange_backend="python", graph="graph_chain", external="oil_shock", use_market_index=True,
        n_sessions=2, warmup_sessions=0, sample_dt=0.1,
        agents={"valuers_myopic": 4, "valuers_network": 4, "trend": 2, "noise": 2, "n_funds": 1,
                "total_cash_cents": 3_000_000, "total_shares": 1_200},
    )
    single = expand(load_config(_grid(tmp_path)))[0]
    for config, seed in ((graph, 5), (single.config, single.seed)):
        expected = run_id(config, seed)
        assert run(config, seed, tmp_path / "runs").name == expected


def _two_key_grid(tmp_path, keys, name="two_keys"):
    values = {"agents.herding.h": [0.0, 0.6], "agents.trend": [1, 2]}
    config = {
        "base": {
            "name": name, "exchange_backend": "python", "graph": "single_asset", "external": "flat",
            "use_market_index": False, "n_sessions": 2, "warmup_sessions": 0, "sample_dt": 0.1,
            "agents": {"valuers": 4, "trend": 2, "noise": 2, "n_funds": 1, "herding": {"h": 0.0}},
        }, "seeds": [31], "grid": {key: values[key] for key in keys},
    }
    path = tmp_path / f"{name}_{'_'.join(k.split('.')[-1] for k in keys)}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


@pytest.mark.parametrize("workers", [1, 2])
def test_repeated_run_ids_are_refused_before_anything_runs(tmp_path, workers, simulations):
    out = tmp_path / "runs"
    with pytest.raises(ValueError, match="share run_ids"):
        run_experiment(_grid(tmp_path, seeds=(19, 19)), out, workers=workers)
    assert not simulations and not out.exists()
    with pytest.raises(ValueError, match="share run_ids"):
        run_experiment(_grid(tmp_path, seeds=(19, 19)), out, shard=(1, 2))


def test_a_reordered_grid_cannot_resume_or_merge_shards(tmp_path, simulations):
    first = _two_key_grid(tmp_path, ["agents.herding.h", "agents.trend"])
    swapped = _two_key_grid(tmp_path, ["agents.trend", "agents.herding.h"])
    assert [spec.seed for spec in expand(load_config(first))] == [spec.seed for spec in expand(load_config(swapped))]
    out = tmp_path / "resume"
    run_experiment(first, out, limit=1)
    with pytest.raises(ValueError, match="different order"):
        run_experiment(swapped, out)
    # Shards cut on two machines from differently ordered grids are caught when they are merged.
    run_experiment(first, tmp_path / "s1", shard=(1, 2))
    run_experiment(swapped, tmp_path / "s2", shard=(2, 2))
    merged = tmp_path / "merged"
    for part in ("s1", "s2"):
        shutil.copytree(tmp_path / part, merged, dirs_exist_ok=True)
    with pytest.raises(ValueError, match="different order"):
        run_experiment(first, merged)
    with pytest.raises(ValueError, match="different order"):
        run_experiment(swapped, merged)


def test_manifest_and_summary_are_replaced_atomically(tmp_path, monkeypatch):
    path, out = _grid(tmp_path), tmp_path / "runs"
    run_experiment(path, out, limit=1)
    manifest = (out / "experiment.json").read_text()
    summary = (out / "summary.csv").read_text()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_experiment(path, out, limit=2)
    assert (out / "experiment.json").read_text() == manifest
    assert (out / "summary.csv").read_text() == summary
    assert not [p.name for p in out.iterdir() if p.name.endswith(".tmp")]
    monkeypatch.undo()
    (out / "experiment.json").write_text('{"config": ')
    with pytest.raises(ValueError, match="not valid JSON"):
        run_experiment(path, out)
