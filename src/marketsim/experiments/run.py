"""Sequential F4 grid runner: python -m marketsim.experiments.run CONFIG --out DIRECTORY."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from marketsim.experiments.config import expand, load_config
from marketsim.experiments.diagnostics import diagnose_run
from marketsim.sim.api import run
from marketsim.sim.log_schema import load_run


def run_experiment(config_path: str | Path, out_dir: Path, limit: int | None = None) -> pd.DataFrame:
    """Execute a grid in its declared order, retaining configs, overrides and a CSV summary.

    Existing run directories are errors; use a new output directory for another execution.
    The resumable, parallel runner is deliberately deferred to F7.
    """
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("limit must be a positive integer")
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
    rows = []
    for index, spec in enumerate(specs, 1):
        directory = run(spec.config, spec.seed, out_dir)
        rows.append(diagnose_run(load_run(directory)) | spec.overrides)
        pd.DataFrame(rows).to_csv(out_dir / "summary.csv", index=False)
        print(f"{index}/{len(specs)} {directory.name}", flush=True)
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="run only the first N grid entries (smoke test)")
    args = parser.parse_args(argv)
    summary = run_experiment(args.config, args.out, args.limit)
    print(f"Completed {len(summary)} runs; summary: {args.out / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
