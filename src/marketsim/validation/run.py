"""F8 on a study root: prepare the control inputs, then compute every check and write it to ``<root>/validation``.

    python -m marketsim.validation.run prepare                      # control graphs and the flat scenario
    python -m marketsim.validation.run checks --root runs/main      # after the runs: the tables

Groups of runs: the main cell of E5 (mixed investors, h = 0.2; collapsed runs left out by the rule of
``marketsim.analysis.runs``), the same cell on the seeds of the controls, and the controls in
``<root>/controls/{random_graph_1,random_graph_2,flat_inputs}`` (the main cell on a random graph or with every
external signal frozen at 2 January 2024, same seeds).
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.analysis.runs import COLLAPSE_GAP, daily
from marketsim.data import api
from marketsim.data import schemas as S
from marketsim.graph.api import save_graph
from marketsim.sim.log_schema import load_run
from marketsim.validation import checks
from marketsim.validation.reality import Reality, SimRun, load_reality, load_sim, random_graphs

FLAT_SCENARIO = "f6_flat_2024"
CONTROLS = ("random_graph_1", "random_graph_2", "flat_inputs")
MAIN_CELL = ("mixed", 0.2)
CONTROL_GRAPHS = Path("runs/main/controls/graphs")  # the path in configs/main/controls/random_graph_*.yaml


def prepare(graphs: Path = CONTROL_GRAPHS, reality: Reality | None = None) -> list[Path]:
    """Write the two control graphs (the first random graphs of ``random_graphs``; the same every time) to
    ``graphs``, where ``configs/main/controls`` read them, and the flat external scenario (every level of 2 January
    2024 kept for 502 sessions) to data/processed."""
    reality = reality or load_reality()
    written = []
    for k, (graph, _) in enumerate(random_graphs(reality, n=2), 1):
        written.append(save_graph(graph, graphs / f"g{k}"))
    if not api.table_path(S.EXTERNAL, scenario=FLAT_SCENARIO).exists():
        levels = reality.levels
        flat = pd.DataFrame({c: np.full(len(levels), float(levels[c].iloc[0])) for c in levels.columns},
                            index=pd.RangeIndex(len(levels), name="session"))
        flat.insert(0, "date", pd.Series(pd.NaT, index=flat.index, dtype="datetime64[ns]"))
        written.append(api.write_table(flat, S.EXTERNAL, scenario=FLAT_SCENARIO))
    return written


def _collapsed(run_dir: Path) -> bool:
    log = load_run(run_dir)
    P, R = daily(log)
    gap = np.log(P / R).iloc[log.meta["config"]["warmup_sessions"]:]
    return bool(gap.abs().to_numpy().max() > COLLAPSE_GAP)


def _load(paths: list[Path], reality: Reality, workers: int) -> list[SimRun]:
    if workers > 1 and len(paths) > 1:
        with ProcessPoolExecutor(workers) as pool:
            return list(pool.map(partial(load_sim, reality=reality), paths, chunksize=1))
    return [load_sim(p, reality) for p in paths]


def groups(root: Path, reality: Reality, workers: int = 1) -> tuple[dict[str, list[SimRun]], dict[str, str]]:
    """The groups of runs of the checks and, for each, how many runs were left out as collapsed."""
    summary = pd.read_csv(root / "e5" / "summary.csv")
    cell = summary.loc[(summary["agents.composition"] == MAIN_CELL[0]) & (summary["agents.herding.h"] == MAIN_CELL[1])]
    paths = [root / "e5" / r for r in cell.run_id]
    with ProcessPoolExecutor(max(workers, 1)) as pool:
        collapsed = dict(zip(cell.run_id, pool.map(_collapsed, paths)))
    kept = cell.loc[~cell.run_id.map(collapsed)]
    name = f"{MAIN_CELL[0]}, h={MAIN_CELL[1]}"
    out = {name: _load([root / "e5" / r for r in kept.run_id], reality, workers)}
    notes = {name: f"{len(cell) - len(kept)} of {len(cell)} collapsed"}
    seeds = None
    for control in CONTROLS:
        d = root / "controls" / control
        if (d / "summary.csv").exists():
            s = pd.read_csv(d / "summary.csv")
            seeds = set(s.seed) if seeds is None else seeds & set(s.seed)
            out[control.replace("_", " ")] = _load([d / r for r in s.run_id], reality, workers)
    if seeds:
        same = kept.loc[kept.seed.isin(seeds)]
        out[f"{name}, control seeds"] = _load([root / "e5" / r for r in same.run_id], reality, workers)
    return out, notes


def run_checks(root: Path, workers: int = 8, n_random: int = 200) -> dict[str, pd.DataFrame]:
    reality = load_reality()
    random_refs = [reality.reference_returns(g) for g, _ in random_graphs(reality, n=n_random)]
    sims, notes = groups(root, reality, workers)
    main = next(iter(sims))
    per, oos = checks.out_of_sample(reality, random_refs)
    tables = {"out_of_sample": oos, "out_of_sample_by_company": per.rename_axis("ticker").reset_index()}
    for column, days in (("wti", 1), ("wti", 2), ("copper", 2)):
        table, detail = checks.event_study(reality, sims, random_refs, column, days)
        tables[f"events_{column}_{days}d"], tables[f"events_{column}_{days}d_by_company"] = table, detail
    tables["stylised_facts"] = checks.stylised_facts(reality, sims)
    tables["lag_test"] = checks.lag_test(reality, sims)
    tables["graph_recovery"], tables["graph_recovery_by_edge"] = checks.graph_recovery(reality, sims)
    tables["bands"] = checks.bands(reality, sims[main])
    tables["band_paths"] = checks.band_paths(reality, sims[main])
    tables["correlation_pairs"] = checks.correlation_pairs(reality, sims[main])
    tables["correlation_structure"] = checks.correlation_structure(reality, sims)
    tables["comovement"] = checks.comovement(reality, sims)
    tables["groups"] = pd.DataFrame([{"group": k, "runs": len(v), "note": notes.get(k, "")} for k, v in sims.items()])
    out = root / "validation"
    out.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(out / f"{name}.csv", index=False)
    return tables


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="F8 checks of the main study against the real data 2024-2025.")
    parser.add_argument("step", choices=("prepare", "checks"))
    parser.add_argument("--root", type=Path, default=Path("runs/main"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--random-graphs", type=int, default=200, help="control graphs for the R-based checks")
    args = parser.parse_args(argv)
    if args.step == "prepare":
        for path in prepare():
            print(path)
        return
    for name, table in run_checks(args.root, args.workers, args.random_graphs).items():
        print(f"{name}: {len(table)} rows")


if __name__ == "__main__":
    main()
