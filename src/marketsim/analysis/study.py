"""Tables of the main study (F7): E5 (P5), E6 (P6) and E7 (P7) from the run directories of one study root.

Layout of a study root (``docs/plans/main_study_protocol.md``; ``scripts/run_main_study.sh`` writes it)::

    <root>/e5/                                  investor mix x herding (summary.csv + runs)
    <root>/e6/channels/{no_stoploss,no_rebalancing,neither}/   wallet channels switched off, herding 0 / 0.4
    <root>/e6/shock/                            company news: NVDA valuations change 0 / -50%, herding 0 / 0.6
    <root>/e7/network{0,25,50}/                 share of network valuers

Cells shared by two experiments are run once: the mixed E5 cell at h = 0.2 is the 30% column of E7, and the mixed
E5 cells at h = 0 and 0.4 are the "all channels" cells of E6. Every table is written next to its runs:
``p5_*.csv`` in ``e5``, ``p6_*.csv`` in ``e6`` and ``p7_*.csv`` in ``e7``. Per-run metrics are cached in the
``*_metrics.csv`` files and computed only for new runs.

    python -m marketsim.analysis.study --root runs/main --workers 8
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.analysis.runs import COLLAPSE_GAP, daily, run_metrics, shock_groups, shock_response
from marketsim.analysis.stats import did, fisher_greater, one_sample, paired, wilson
from marketsim.graph.api import load_graph
from marketsim.sim.log_schema import load_run

MIXES = ("mixed", "network", "trend")
P5_METRICS = ("day0", "day1", "day1_d1", "day1_d2", "day1_d3", "lag", "vol_amp", "corr_R", "gap_days", "acf1")
# secondary E5 metrics outside the protocol's decision rules (P6 on the E5 grid); own random stream, so adding
# them leaves every protocol table unchanged
P5_EXTRA_METRICS = ("resid_unrelated", "resid_unrelated_stress", "false_links")
P6_METRICS = ("resid_unrelated", "resid_unrelated_stress", "resid_related", "false_links", "excess_unrelated",
              "risk_triggers", "corr_R")
P7_METRICS = ("day0", "day1", "pass2_d1", "pass2_d2", "pass2_d3", "day1_d2", "corr_d2", "pass2_tech", "corr_tech",
              "lag", "vol_amp", "corr_R", "gap_days", "acf1")
CHANNELS = ("no_stoploss", "no_rebalancing", "neither")
SHOCK_HORIZONS = (1, 5, 20)
COMPOSITION, HERDING, SHOCK = "agents.composition", "agents.herding.h", "agents.company_shock.change"


def _summary(directory: Path, columns: tuple[str, ...] = ()) -> pd.DataFrame:
    s = pd.read_csv(directory / "summary.csv")
    return s[["run_id", "seed", *columns]].assign(directory=str(directory))


def _metrics(sources: pd.DataFrame, cache: Path, workers: int, compute: Callable = run_metrics) -> pd.DataFrame:
    """Per-run metrics of ``sources`` (run_id, directory), cached in ``cache`` by run_id."""
    known = pd.read_csv(cache) if cache.exists() else pd.DataFrame(columns=["run_id"])
    todo = sources.loc[~sources.run_id.isin(set(known.run_id))]
    if len(todo):
        paths = [str(Path(d) / r) for d, r in zip(todo.directory, todo.run_id)]
        if workers > 1 and len(paths) > 1:
            with ProcessPoolExecutor(workers) as pool:
                new = list(pool.map(compute, paths, chunksize=1))
        else:
            new = [compute(p) for p in paths]
        known = pd.concat([known, pd.DataFrame(new)], ignore_index=True)
        known.to_csv(cache, index=False)
    out = sources.merge(known, on="run_id", how="left", validate="one_to_one")
    if "collapse" in out:
        out["collapse"] = out["collapse"].astype(bool)
    return out


def _collapses(runs: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for key, g in runs.groupby(keys):
        k, n = int(g.collapse.sum()), len(g)
        lo, hi = wilson(k, n)
        rows.append({**dict(zip(keys, key if isinstance(key, tuple) else (key,))), "collapsed": k, "runs": n,
                     "rate": k / n, "ci_low": lo, "ci_high": hi})
    return pd.DataFrame(rows)


# ----- E5 (P5) -----------------------------------------------------------------------------------------------------
def e5(root: Path, workers: int = 1, seed: int = 2026) -> dict[str, pd.DataFrame]:
    """P5: paired contrasts of the investor mixes against ``mixed`` at every h and of every h > 0 against h = 0 in
    every mix (Bonferroni over these contrasts per metric), the herding x mix interaction at the largest h, and
    the collapse rate of every cell with Fisher's test of trend-with-herding against the rest."""
    d = root / "e5"
    runs = _metrics(_summary(d, (COMPOSITION, HERDING)), d / "p5_metrics.csv", workers).rename(
        columns={COMPOSITION: "mix", HERDING: "h"})
    hs = sorted(runs.h.unique())
    cell = {key: g for key, g in runs.groupby(["mix", "h"])}
    mixes = [m for m in MIXES if m in set(runs.mix)]
    plan = ([("mix", f"{m} − mixed", f"h={h}", (m, h), ("mixed", h)) for h in hs for m in mixes if m != "mixed"]
            + [("herding", f"h={h} − h=0", m, (m, h), (m, hs[0])) for m in mixes for h in hs[1:]])
    rng = np.random.default_rng(seed)
    contrasts = pd.DataFrame([
        {"metric": metric, "factor": factor, "contrast": name, "at": at,
         **paired(cell[a], cell[b], metric, rng, family=len(plan))}
        for metric in P5_METRICS for factor, name, at, a, b in plan if a in cell and b in cell])
    top = hs[-1]
    interaction = pd.DataFrame([
        {"mix": m, "metric": metric, "h": top,
         **did(cell[(m, top)], cell[(m, hs[0])], cell[("mixed", top)], cell[("mixed", hs[0])], metric, rng)}
        for m in mixes if m != "mixed" for metric in ("day1", "lag", "vol_amp", "corr_R", "gap_days", "acf1")])
    collapses = _collapses(runs, ["mix", "h"])
    group = (runs.mix == "trend") & (runs.h > 0)
    a, n1 = int(runs.loc[group, "collapse"].sum()), int(group.sum())
    b, n2 = int(runs.loc[~group, "collapse"].sum()), int((~group).sum())
    test = pd.DataFrame([{"group": "trend, h > 0", "collapsed": a, "runs": n1, "rest_collapsed": b, "rest_runs": n2,
                          "fisher_p_one_sided": fisher_greater(a, n1, b, n2) if n1 and n2 else np.nan}])
    extra_rng = np.random.default_rng(seed + 1)
    extra = pd.DataFrame([
        {"metric": metric, "factor": factor, "contrast": name, "at": at,
         **paired(cell[a], cell[b], metric, extra_rng, family=len(plan))}
        for metric in P5_EXTRA_METRICS if metric in runs
        for factor, name, at, a, b in plan if a in cell and b in cell])
    tables = {"p5_contrasts": contrasts, "p5_interaction": interaction, "p5_collapses": collapses,
              "p5_collapse_test": test, "p5_contrasts_extra": extra}
    for name, table in tables.items():
        table.to_csv(d / f"{name}.csv", index=False)
    return {"runs": runs, **tables}


# ----- E7 (P7) -----------------------------------------------------------------------------------------------------
def e7(root: Path, workers: int = 1, seed: int = 2027) -> dict[str, pd.DataFrame]:
    """P7: cells 0%, 25%, 50% network valuers and the mixed E5 cell at h = 0.2 as 30%; contrasts 25−0, 50−0, 50−25
    (Bonferroni over the three)."""
    d = root / "e7"
    parts = [_summary(sub).assign(share=int(sub.name.removeprefix("network")))
             for sub in sorted(d.glob("network*")) if (sub / "summary.csv").exists()]
    if (root / "e5" / "summary.csv").exists():
        e5_runs = _summary(root / "e5", (COMPOSITION, HERDING))
        e5_runs = e5_runs.loc[(e5_runs[COMPOSITION] == "mixed") & (e5_runs[HERDING] == 0.2), ["run_id", "seed",
                                                                                                "directory"]]
        parts.append(e5_runs.assign(share=30))
    runs = _metrics(pd.concat(parts, ignore_index=True), d / "p7_metrics.csv", workers)
    cell = {share: g for share, g in runs.groupby("share")}
    pairs = [(a, b) for a, b in ((25, 0), (50, 0), (50, 25)) if a in cell and b in cell]
    rng = np.random.default_rng(seed)
    contrasts = pd.DataFrame([{"metric": m, "contrast": f"{a}% − {b}%",
                               **paired(cell[a], cell[b], m, rng, family=len(pairs))}
                              for m in P7_METRICS for a, b in pairs])
    collapses = _collapses(runs, ["share"])
    # Secondary, outside the protocol (own random stream): the shares above are of all 500 investors; half of the
    # 300 valuers is the E5 mixed cell (30% of the investors), so 30% − 0% is "half the valuers know the graph".
    extra_rng = np.random.default_rng(seed + 1)
    extra = pd.DataFrame([{"metric": m, "contrast": "30% − 0%", "network share of valuers": "50% − 0%",
                           **paired(cell[30], cell[0], m, extra_rng)}
                          for m in P7_METRICS if 30 in cell and 0 in cell])
    contrasts.to_csv(d / "p7_contrasts.csv", index=False)
    collapses.to_csv(d / "p7_collapses.csv", index=False)
    extra.to_csv(d / "p7_contrasts_extra.csv", index=False)
    return {"runs": runs, "p7_contrasts": contrasts, "p7_collapses": collapses, "p7_contrasts_extra": extra}


# ----- E6 (P6) -----------------------------------------------------------------------------------------------------
def e6_channels(root: Path, workers: int = 1, seed: int = 2028) -> dict[str, pd.DataFrame]:
    """P6, part 1, on the real 2024–2025 signals: excess comovement of the companies the graph does not relate,
    with every wallet channel on (mixed E5 cells) and with the stop-loss, the rebalancing or both switched off, at
    two herding strengths. Contrasts: each switch against all channels at every h, and the largest h against 0
    for every channel setting (Bonferroni over all of them)."""
    d = root / "e6"
    parts = [_summary(d / "channels" / c, (HERDING,)).assign(channels=c)
             for c in CHANNELS if (d / "channels" / c / "summary.csv").exists()]
    hs = sorted({h for p in parts for h in p[HERDING]})
    if (root / "e5" / "summary.csv").exists():
        e5_runs = _summary(root / "e5", (COMPOSITION, HERDING))
        e5_runs = e5_runs.loc[(e5_runs[COMPOSITION] == "mixed") & e5_runs[HERDING].isin(hs)]
        parts.append(e5_runs.drop(columns=COMPOSITION).assign(channels="all"))
    runs = _metrics(pd.concat(parts, ignore_index=True), d / "p6_metrics.csv", workers).rename(
        columns={HERDING: "h"})
    cell = {key: g for key, g in runs.groupby(["channels", "h"])}
    plan = ([("channels", f"{c} − all", f"h={h}", (c, h), ("all", h)) for h in hs for c in CHANNELS]
            + [("herding", f"h={hs[-1]} − h={hs[0]}", c, (c, hs[-1]), (c, hs[0])) for c in ("all", *CHANNELS)])
    plan = [p for p in plan if p[3] in cell and p[4] in cell]
    rng = np.random.default_rng(seed)
    contrasts = pd.DataFrame([
        {"metric": metric, "factor": factor, "contrast": name, "at": at,
         **paired(cell[a], cell[b], metric, rng, family=len(plan))}
        for metric in P6_METRICS for factor, name, at, a, b in plan])
    means = pd.DataFrame([
        {"channels": c, "h": h, "metric": metric, **one_sample(g.loc[~g.collapse], metric, rng)}
        for (c, h), g in cell.items() for metric in P6_METRICS])
    contrasts.to_csv(d / "p6_contrasts.csv", index=False)
    means.to_csv(d / "p6_means.csv", index=False)
    return {"runs": runs, "p6_contrasts": contrasts, "p6_means": means}


def _response(pair: tuple[str, str]) -> dict:
    return shock_response(pair[0], pair[1], SHOCK_HORIZONS)


def e6_shock(root: Path, workers: int = 1, seed: int = 2029) -> dict[str, pd.DataFrame]:
    """P6, part 2, on frozen signals: response to company news (every valuer's valuation of one company changes)
    against the same seed without the news, for the company, the companies the graph links to it and those it does
    not; the response is tested against zero at every h and between the two h."""
    d = root / "e6" / "shock"
    s = _summary(d, (SHOCK, HERDING)).rename(columns={SHOCK: "change", HERDING: "h"})
    base = s.loc[s.change == 0].set_index(["h", "seed"]).run_id
    shocked = s.loc[s.change != 0]
    pairs = [(str(d / r), str(d / base[(h, sd)])) for r, h, sd in zip(shocked.run_id, shocked.h, shocked.seed)]
    cache = d.parent / "p6_shock_metrics.csv"
    known = pd.read_csv(cache) if cache.exists() else pd.DataFrame(columns=["run_id"])
    todo = [p for p in pairs if Path(p[0]).name not in set(known.run_id)]
    if todo:
        if workers > 1 and len(todo) > 1:
            with ProcessPoolExecutor(workers) as pool:
                new = list(pool.map(_response, todo, chunksize=1))
        else:
            new = [_response(p) for p in todo]
        known = pd.concat([known, pd.DataFrame(new)], ignore_index=True)
        known.to_csv(cache, index=False)
    runs = shocked[["run_id", "seed", "change", "h"]].merge(known, on="run_id", validate="one_to_one")
    metrics = [c for c in known.columns if c.split("_")[-1].isdigit() or c == "risk_triggers_extra"]
    rng = np.random.default_rng(seed)
    cell = {key: g for key, g in runs.groupby(["change", "h"])}
    effects = pd.DataFrame([{"change": c, "h": h, "metric": m, **one_sample(g, m, rng, family=len(cell))}
                            for (c, h), g in cell.items() for m in metrics])
    hs = sorted(runs.h.unique())
    herding = pd.DataFrame([{"change": c, "metric": m, "contrast": f"h={hs[-1]} − h={hs[0]}",
                             **paired(cell[(c, hs[-1])], cell[(c, hs[0])], m, rng)}
                            for c in sorted(runs.change.unique()) for m in metrics
                            if (c, hs[-1]) in cell and (c, hs[0]) in cell and len(hs) > 1])
    effects.to_csv(d.parent / "p6_shock_effects.csv", index=False)
    herding.to_csv(d.parent / "p6_shock_herding.csv", index=False)
    # Secondary, outside the protocol (own random stream): the horizon k above is the close k sessions after the
    # close of the session the news happens in; this adds that first close (k = 0) and counts the runs that cross
    # the collapse rule, which a news shock crosses by design (R does not contain the news).
    if workers > 1:
        with ProcessPoolExecutor(workers) as pool:
            first = pd.DataFrame(list(pool.map(_first_close, pairs, chunksize=1)))
    else:
        first = pd.DataFrame([_first_close(p) for p in pairs])
    first = shocked[["run_id", "seed", "change", "h"]].merge(first, on="run_id", validate="one_to_one")
    extra_rng = np.random.default_rng(seed + 1)
    cells = {key: g for key, g in first.groupby(["change", "h"])}
    extra = pd.DataFrame(
        [{"change": c, "h": h, "metric": m, **one_sample(g, m, extra_rng, family=len(cells))}
         for (c, h), g in cells.items() for m in ("target_0", "linked_0", "unrelated_0")]
        + [{"change": c, "h": h, "metric": "collapse rule crossed", "pairs": len(g), "mean": float(g.crossed.mean()),
            "count": int(g.crossed.sum())} for (c, h), g in cells.items()])
    extra.to_csv(d.parent / "p6_shock_extra.csv", index=False)
    return {"runs": runs, "p6_shock_effects": effects, "p6_shock_herding": herding, "p6_shock_extra": extra}


def _first_close(pair: tuple[str, str]) -> dict:
    """Response at the close of the session the news happens in, and whether the shocked run crosses the collapse
    rule max |ln P/R| > ln 2 after the warm-up."""
    shocked, base = load_run(pair[0]), load_run(pair[1])
    shock = shocked.meta["resolved_agents"]["company_shock"]
    p1, r1 = daily(shocked)
    p0, _ = daily(base)
    diff = np.log(p1) - np.log(p0)
    groups = shock_groups(load_graph(shocked.meta["config"]["graph"]), list(p1.columns), shock["asset"])
    k = int(np.floor(shock["time"]))
    out = {"run_id": Path(pair[0]).name}
    for name, members in groups.items():
        out[f"{name}_0"] = float(diff[members].iloc[k].mean()) if members else float("nan")
    gap = np.log(p1 / r1).iloc[shocked.meta["config"]["warmup_sessions"]:]
    out["crossed"] = bool(gap.abs().to_numpy().max() > COLLAPSE_GAP)
    return out


STEPS = {"e5": e5, "e7": e7, "e6_channels": e6_channels, "e6_shock": e6_shock}
NEEDS = {"e5": "e5/summary.csv", "e7": "e7", "e6_channels": "e6/channels", "e6_shock": "e6/shock/summary.csv"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Tables of the main study (P5, P6, P7) from its run directories.")
    parser.add_argument("--root", type=Path, default=Path("runs/main"), help="study root (see the module docstring)")
    parser.add_argument("--only", nargs="*", choices=sorted(STEPS), help="steps to compute (default: all present)")
    parser.add_argument("--workers", type=int, default=8, help="processes for the per-run metrics")
    args = parser.parse_args(argv)
    steps = args.only or [k for k in STEPS if (args.root / NEEDS[k]).exists()]
    if not steps:
        parser.error(f"no experiment found under {args.root}")
    for name in steps:
        tables = STEPS[name](args.root, args.workers)
        print(f"{name}: {len(tables['runs'])} runs -> " + ", ".join(k for k in tables if k != "runs"), flush=True)


if __name__ == "__main__":
    main()
