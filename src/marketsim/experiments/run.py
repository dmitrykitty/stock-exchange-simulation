"""F4 grid runner: python -m marketsim.experiments.run CONFIG --out DIRECTORY [--workers N].

Runs are independent and deterministic per seed, so ``--workers N`` executes them in N processes.
The run logs and summary.csv are the same as in a sequential execution (only timing metadata differs).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from marketsim.experiments.config import RunSpec, expand, load_config
from marketsim.experiments.diagnostics import diagnose_run
from marketsim.sim.api import run
from marketsim.sim.log_schema import load_run


def _run_one(spec: RunSpec, out_dir: Path) -> dict:
    """One grid entry: simulate, then summarize the log. Module-level so worker processes can import it."""
    directory = run(spec.config, spec.seed, out_dir)
    return diagnose_run(load_run(directory)) | spec.overrides


def _run_parallel(specs: list[RunSpec], out_dir: Path, workers: int) -> list[dict]:
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
                pd.DataFrame([rows[i] for i in sorted(rows)]).to_csv(out_dir / "summary.csv", index=False)
                print(f"{len(rows)}/{len(specs)} {rows[index]['run_id']}", flush=True)
            if first_error is None:
                while len(pending) < workers and next_index < len(specs):
                    pending[pool.submit(_run_one, specs[next_index], out_dir)] = next_index
                    next_index += 1
    if first_error is not None:
        raise first_error
    return [rows[i] for i in range(len(specs))]


def run_experiment(
    config_path: str | Path, out_dir: Path, limit: int | None = None, workers: int = 1,
) -> pd.DataFrame:
    """Execute a grid, retaining configs, overrides and a CSV summary in the declared grid order.

    Existing run directories are errors; use a new output directory for another execution.
    ``workers > 1`` runs the grid in that many processes; resuming an interrupted grid is not supported yet.
    """
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("limit must be a positive integer")
    if type(workers) is not int or workers <= 0:
        raise ValueError("workers must be a positive integer")
    experiment = load_config(config_path)
    specs = expand(experiment)
    if limit is not None:
        specs = specs[:limit]
    if not specs:
        raise ValueError("experiment expands to no runs")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "experiment.json"
    with manifest.open("x", encoding="utf-8") as handle:
        json.dump({"config": asdict(experiment) | {"seeds": list(experiment.seeds)}, "limit": limit},
                  handle, indent=2, sort_keys=True)
        handle.write("\n")
    if workers > 1:
        return pd.DataFrame(_run_parallel(specs, out_dir, min(workers, len(specs))))
    rows = []
    for index, spec in enumerate(specs, 1):
        rows.append(_run_one(spec, out_dir))
        pd.DataFrame(rows).to_csv(out_dir / "summary.csv", index=False)
        print(f"{index}/{len(specs)} {rows[-1]['run_id']}", flush=True)
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="run only the first N grid entries (smoke test)")
    parser.add_argument("--workers", type=int, default=1, help="number of worker processes (default 1)")
    args = parser.parse_args(argv)
    summary = run_experiment(args.config, args.out, args.limit, args.workers)
    print(f"Completed {len(summary)} runs; summary: {args.out / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
