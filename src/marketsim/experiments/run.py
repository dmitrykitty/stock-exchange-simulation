"""Grid runner: python -m marketsim.experiments.run CONFIG --out DIRECTORY [--workers N] [--shard I/N].

Runs are independent and deterministic per seed, so ``--workers N`` executes them in N processes.
The run logs and summary.csv are the same as in a sequential execution (only timing metadata differs).

Resuming: runs already complete in DIRECTORY are not simulated again, only summarized, so an interrupted
experiment continues with the same command. ``--shard I/N`` takes every N-th grid entry starting at the I-th
(1-based); shards can run on different machines, and their run directories copied into one DIRECTORY form the
whole experiment, which the same command without ``--shard`` then summarizes.

Shards are taken by position in the expanded grid, so experiment.json keeps the ordered run_ids of the grid and
every shard also leaves experiment.shard-I-of-N.json; a later invocation (resume or merge) whose grid expands in
another order, e.g. after swapping two grid keys, is an error instead of repeating some runs and skipping others.
experiment.json and summary.csv are replaced atomically, so an interruption leaves the previous version.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from marketsim.data.schemas import ContractError
from marketsim.experiments.config import RunSpec, expand, load_config
from marketsim.experiments.diagnostics import diagnose_run
from marketsim.sim.api import run, run_id
from marketsim.sim.log_schema import RUN_TABLES, load_run, validate_meta


def _check_complete(directory: Path, expected_id: str) -> None:
    """Raise unless ``directory`` holds a finished run: valid meta.json of that run and every K3 table."""
    try:
        meta = validate_meta(json.loads((directory / "meta.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, ContractError) as exc:
        raise ContractError(f"{directory}: incomplete run ({exc}); delete it to run it again") from exc
    missing = [spec.file for spec in RUN_TABLES if not (directory / spec.file).is_file()]
    if meta["run_id"] != expected_id or missing:
        raise ContractError(f"{directory}: incomplete run (run_id {meta['run_id']}, missing {missing}); "
                            "delete it to run it again")


def _run_one(spec: RunSpec, out_dir: Path) -> dict:
    """One grid entry: simulate unless already complete, then summarize the log. Module-level for workers."""
    directory = Path(out_dir) / run_id(spec.config, spec.seed)
    if directory.exists():
        _check_complete(directory, directory.name)
    else:
        directory = run(spec.config, spec.seed, out_dir)
    return diagnose_run(load_run(directory)) | spec.overrides


def parse_shard(text: str) -> tuple[int, int]:
    """``"I/N"`` -> (I, N) with 1 <= I <= N."""
    try:
        index, count = (int(part) for part in text.split("/"))
    except ValueError:
        raise ValueError(f"shard must look like I/N, got {text!r}") from None
    if not 1 <= index <= count:
        raise ValueError(f"shard {text!r}: need 1 <= I <= N")
    return index, count


def _replace_text(path: Path, text: str) -> None:
    """Write ``text`` to a temporary file next to ``path`` and rename it over ``path`` (atomic on one disk)."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_summary(path: Path, rows: list[dict]) -> None:
    _replace_text(path, pd.DataFrame(rows).to_csv(index=False))


def _write_manifest(path: Path, experiment, limit: int | None, shard: tuple[int, int] | None,
                    runs: list[str]) -> None:
    """experiment.json: the config and the ordered run_ids of the grid (both fixed for the directory), and the
    options of the latest invocation.

    The config is compared as data, so a different key order in the YAML passes; the run_id list is compared in
    order, because shards are taken by position and a reordered grid would repeat some runs and skip others.
    """
    config = asdict(experiment) | {"seeds": list(experiment.seeds)}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{path} is not valid JSON ({exc}); restore it or use another output directory") from exc
        if previous.get("config") != json.loads(json.dumps(config)):
            raise ValueError(f"{path} belongs to a different experiment config; use another output directory")
        if previous.get("runs") is not None and previous["runs"] != runs:
            raise ValueError(f"{path}: the grid expands in a different order; "
                             "keep the grid keys in the order of the first invocation")
    for shard_file in sorted(path.parent.glob("experiment.shard-*.json")):
        try:
            recorded = json.loads(shard_file.read_text(encoding="utf-8")).get("runs")
        except ValueError as exc:
            raise ValueError(f"{shard_file} is not valid JSON ({exc})") from exc
        if recorded != runs:
            raise ValueError(f"{shard_file}: that shard was cut from a grid in a different order; "
                             "its runs do not partition this grid")
    _replace_text(path, json.dumps({"config": config, "runs": runs, "limit": limit,
                                    "shard": list(shard) if shard else None}, indent=2, sort_keys=True) + "\n")
    if shard is not None:
        _replace_text(path.with_name(f"experiment.shard-{shard[0]}-of-{shard[1]}.json"),
                      json.dumps({"runs": runs, "shard": list(shard)}, indent=2) + "\n")


def _progress(done: int, total: int, row: dict, existing: frozenset[str]) -> None:
    note = " (already on disk)" if row["run_id"] in existing else ""
    print(f"{done}/{total} {row['run_id']}{note}", flush=True)


def _run_parallel(specs: list[RunSpec], out_dir: Path, workers: int, existing: frozenset[str]) -> list[dict]:
    """Run specs in worker processes; summary rows always keep the grid order, whatever finishes first."""
    rows: dict[int, dict] = {}
    first_error: BaseException | None = None
    # "spawn" behaves the same on Linux, macOS and Windows and does not copy the parent's state.
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        # Keep unstarted specs outside the executor so a failure cannot launch queued simulations.
        pending = {pool.submit(_run_one, spec, out_dir): index for index, spec in enumerate(specs[:workers])}
        next_index = len(pending)
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=pending.get):
                index = pending.pop(future)
                if future.cancelled():
                    continue
                error = future.exception()
                if error is not None:
                    if first_error is None:
                        first_error = error
                    for unfinished in pending:
                        unfinished.cancel()
                    continue
                rows[index] = future.result()
                _write_summary(out_dir / "summary.csv", [rows[i] for i in sorted(rows)])
                _progress(len(rows), len(specs), rows[index], existing)
            if first_error is None:
                while len(pending) < workers and next_index < len(specs):
                    pending[pool.submit(_run_one, specs[next_index], out_dir)] = next_index
                    next_index += 1
    if first_error is not None:
        raise first_error
    return [rows[i] for i in range(len(specs))]


def run_experiment(
    config_path: str | Path, out_dir: Path, limit: int | None = None, workers: int = 1,
    shard: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """Execute a grid, retaining configs, overrides and a CSV summary in the declared grid order.

    Runs already complete in ``out_dir`` are reused (summarized, not simulated); a run directory that exists but
    is incomplete is an error. ``out_dir`` holds one experiment config. ``limit`` keeps the first N grid entries,
    then ``shard = (I, N)`` keeps entries I, I+N, I+2N, ... (1-based). ``workers > 1`` runs in that many processes.
    """
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("limit must be a positive integer")
    if type(workers) is not int or workers <= 0:
        raise ValueError("workers must be a positive integer")
    if shard is not None and not (len(shard) == 2 and all(type(v) is int for v in shard)
                                  and 1 <= shard[0] <= shard[1]):
        raise ValueError("shard must be (I, N) with integers 1 <= I <= N")
    experiment = load_config(config_path)
    specs = expand(experiment)
    # Two grid entries with one run_id (e.g. a repeated seed) would share a run directory: refuse before the grid
    # is cut or any worker starts. The ordered ids of the whole grid go into the manifest.
    ids = [run_id(spec.config, spec.seed) for spec in specs]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"grid entries share run_ids {duplicates}: remove repeated seeds or grid values")
    if limit is not None:
        specs = specs[:limit]
    if shard is not None:
        specs = specs[shard[0] - 1::shard[1]]
    if not specs:
        raise ValueError("experiment expands to no runs")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(out_dir / "experiment.json", experiment, limit, shard, ids)
    existing = frozenset(path.name for path in out_dir.iterdir() if path.is_dir())
    if workers > 1:
        return pd.DataFrame(_run_parallel(specs, out_dir, min(workers, len(specs)), existing))
    rows = []
    for index, spec in enumerate(specs, 1):
        rows.append(_run_one(spec, out_dir))
        _write_summary(out_dir / "summary.csv", rows)
        _progress(index, len(specs), rows[-1], existing)
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="run only the first N grid entries (smoke test)")
    parser.add_argument("--workers", type=int, default=1, help="number of worker processes (default 1)")
    parser.add_argument("--shard", type=_shard_argument, metavar="I/N",
                        help="run only grid entries I, I+N, I+2N, ... (1-based), e.g. 2/3 on the second of three machines")
    args = parser.parse_args(argv)
    summary = run_experiment(args.config, args.out, args.limit, args.workers, args.shard)
    print(f"Completed {len(summary)} runs; summary: {args.out / 'summary.csv'}")
    return 0


def _shard_argument(text: str) -> tuple[int, int]:
    try:
        return parse_shard(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
