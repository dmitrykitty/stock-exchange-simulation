"""Comparison of experiments as one self-contained HTML page (F9, panel "experiment comparison").

For every experiment the page shows, per metric: a heatmap of the cell means (the two factors of the grid), box
plots with every run as a point (collapsed runs marked and left out of the means), the paired contrasts with their
95% bootstrap intervals and the collapse rate of every cell.

It reads only the summary tables of F7: the runner's ``summary.csv`` and the per-run metrics and contrasts that the
analysis notebooks write next to the runs (``notebooks/07_e5_propagation.ipynb`` -> ``p5_metrics.csv`` and
``p5_contrasts.csv``, ``notebooks/08_e7_network_share.ipynb`` -> ``p7_metrics.csv`` and ``p7_contrasts.csv``). It
imports nothing from the simulation and reads no run log.

    python -m marketsim.viz.compare -o runs/replay/experiments.html
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import pandas as pd

# Metric descriptions shared by the experiments: label, explanation, and which direction is closer to the
# reference R (``"high"``, ``"low"``, a target value, or ``None`` when there is no "better").
METRICS = {
    "day0": ("Same-day share", ("Pooled regression of daily returns on the same-day change of R; 1 = the market prices "
             "the graph at once"), 1.0),
    "day1": ("Next-day share", "Part of a move of R that reaches the price only the next day; 0 = no delay", 0.0),
    "day1_d1": ("Next-day share, 1 link", "COP, EOG: one link from oil", 0.0),
    "day1_d2": ("Next-day share, 2 links", "VLO, MPC (refiners): two links from oil", 0.0),
    "day1_d3": ("Next-day share, 3 links", "DAL, UAL, ODFL: three links from oil", 0.0),
    "pass2_d1": ("Passed in 2 days, 1 link", ("Share of a move of R reached within two days by COP, EOG "
                 "(1 = the whole move, > 1 = overshoot)"), 1.0),
    "pass2_d2": ("Passed in 2 days, 2 links", "The same for VLO, MPC (refiners)", 1.0),
    "pass2_d3": ("Passed in 2 days, 3 links", "The same for DAL, UAL, ODFL", 1.0),
    "pass2_tech": ("Passed in 2 days, GOOGL & META", ("Their company parents NVDA, AMD are known only to network "
                   "valuers"), 1.0),
    "corr_d2": ("Correlation with R, refiners", "Correlation of daily returns of VLO, MPC with their R", "high"),
    "corr_tech": ("Correlation with R, GOOGL & META", "Correlation of daily returns with R", "high"),
    "lag": ("Lag behind R (D1), sessions", ("marketsim.metrics.wave_metrics: shift with the largest cross-correlation "
            "of changes of ln R and ln P on a 0.25-session grid; mean over companies"), "low"),
    "vol_amp": ("Volatility amplification (D1)", ("σ(Δ ln P) / σ(Δ ln R) per session, median over companies; "
                "> 1 = the market amplifies the moves of R"), 1.0),
    "corr_R": ("Correlation with R", "Correlation of daily returns of P and R, median over companies", "high"),
    "gap_days": ("Company-days off R by > 10%", "Company-days with |ln P/R| > 10%, of about 8900 per run", "low"),
    "acf1": ("Return autocorrelation", ("Lag-1 autocorrelation of daily returns, mean over companies "
             "(real stocks: −0.05)"), None),
}


@dataclass
class Experiment:
    """One experiment of the page: runs with two factors (rows x columns) and per-run metrics."""

    key: str
    title: str
    description: str
    row_name: str
    col_name: str
    runs: pd.DataFrame  # columns: run_id, seed, row, col, collapse, <metrics>
    rows: list
    cols: list
    col_labels: list[str]
    contrasts: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)


def _finite(values) -> list[float | None]:
    return [None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), 5)
            for v in values]


def _metrics(frame: pd.DataFrame) -> list[str]:
    return [m for m in METRICS if m in frame.columns]


def load_e5(directory: str | Path) -> Experiment:
    """E5 (investor mix x herding) from ``summary.csv`` and the ``p5_*`` tables in ``directory``."""
    d = Path(directory)
    summary = pd.read_csv(d / "summary.csv")[["run_id", "seed", "agents.composition", "agents.herding.h"]]
    runs = summary.rename(columns={"agents.composition": "row", "agents.herding.h": "col"}).merge(
        pd.read_csv(d / "p5_metrics.csv"), on="run_id", validate="one_to_one")
    rows = [m for m in ("mixed", "network", "trend") if m in set(runs.row)]
    cols = sorted(set(runs.col))
    contrasts = pd.read_csv(d / "p5_contrasts.csv") if (d / "p5_contrasts.csv").exists() else None
    return Experiment(
        "e5", "E5 — investor mix × herding",
        "18 companies of real_v1 on the real commodity and S&P 500 series of 2024–2025, 502 sessions; "
        f"{len(runs)} runs ({runs.groupby(['row', 'col']).size().max()} seeds per cell). Question P5: how the "
        "investors change the propagation of shocks.",
        "investor mix", "herding h", runs, rows, cols, [f"h = {c:g}" for c in cols], contrasts,
        [("mixed 30/30/20/20, network 15/45/20/20, trend 35/15/30/20 % of myopic valuers / network valuers / trend "
         "followers / noise traders, plus 25 rebalancing funds")])


def load_e7(directory: str | Path, e5_directory: str | Path | None = None) -> Experiment:
    """E7 (share of network valuers) from ``network<share>/summary.csv`` and the ``p7_*`` tables in ``directory``;
    with ``e5_directory`` the mixed E5 cell at h = 0.2 (30% network valuers) joins as an extra column."""
    d = Path(directory)
    parts = []
    for sub in sorted(d.glob("network*/summary.csv")):
        share = int(sub.parent.name.removeprefix("network"))
        parts.append(pd.read_csv(sub)[["run_id", "seed"]].assign(col=share))
    if e5_directory is not None and (Path(e5_directory) / "summary.csv").exists():
        e5 = pd.read_csv(Path(e5_directory) / "summary.csv")
        e5 = e5.loc[(e5["agents.composition"] == "mixed") & (e5["agents.herding.h"] == 0.2), ["run_id", "seed"]]
        parts.append(e5.assign(col=30))
    metrics = pd.read_csv(d / "p7_metrics.csv")
    runs = pd.concat(parts, ignore_index=True).assign(row="300 valuers").merge(metrics, on="run_id", how="inner",
                                                                                validate="one_to_one")
    cols = sorted(set(runs.col))
    contrasts = pd.read_csv(d / "p7_contrasts.csv") if (d / "p7_contrasts.csv").exists() else None
    if contrasts is not None and "at" not in contrasts:
        contrasts = contrasts.assign(factor="network share", at="h = 0.2")
    return Experiment(
        "e7", "E7 — share of network valuers",
        "The E5 market with 300 valuers, 100 trend followers and 100 noise traders, herding h = 0.2; only the split "
        f"of the valuers into network and myopic ones changes. {len(runs)} runs. Question P7: how many investors "
        "must know the graph for the wave to pass, and do they cause overshoot?",
        "", "network valuers among the investors", runs, ["300 valuers"], cols,
        [f"{c}%" + (" (E5)" if c == 30 else "") for c in cols], contrasts,
        ["the 30% column is the mixed cell of E5 at h = 0.2: the same market and seeds with 150 network valuers"])


def _experiment_payload(e: Experiment) -> dict:
    runs = e.runs.copy()
    runs["collapse"] = runs["collapse"].astype(bool)
    metrics = _metrics(runs)
    out = {
        "key": e.key, "title": e.title, "description": e.description, "notes": e.notes,
        "rowName": e.row_name, "colName": e.col_name, "rows": [str(r) for r in e.rows],
        "cols": [str(c) for c in e.cols], "colLabels": e.col_labels,
        "metrics": [{"key": m, "label": METRICS[m][0], "help": METRICS[m][1], "target": METRICS[m][2]}
                    for m in metrics],
        "runs": {
            "run_id": runs.run_id.tolist(), "seed": [int(s) for s in runs.seed],
            "row": [e.rows.index(r) for r in runs.row], "col": [e.cols.index(c) for c in runs.col],
            "collapse": runs.collapse.tolist(),
            **{m: _finite(runs[m].tolist()) for m in metrics},
        },
        "contrasts": [],
    }
    if e.contrasts is not None:
        for r in e.contrasts.to_dict("records"):
            if r["metric"] not in metrics:
                continue
            out["contrasts"].append({
                "metric": r["metric"], "contrast": str(r["contrast"]), "at": str(r.get("at", "")),
                "factor": str(r.get("factor", "")), "pairs": int(r["pairs"]),
                "mean": _finite([r["mean"]])[0], "lo": _finite([r["ci_low"]])[0], "hi": _finite([r["ci_high"]])[0],
                "clear": bool(r["clear"]), "bonferroni": bool(r.get("clear (Bonferroni)", r["clear"])),
            })
    return out


def build_payload(experiments: list[Experiment]) -> dict:
    """The JSON the page draws: one entry per experiment."""
    if not experiments:
        raise ValueError("no experiment to show")
    return {"experiments": [_experiment_payload(e) for e in experiments]}


def render(payload: dict, cdn: bool = False) -> str:
    """The page: the HTML template with the payload and plotly.js (inline, or from a CDN with ``cdn``)."""
    template = resources.files("marketsim.viz").joinpath("compare_template.html").read_text(encoding="utf-8")
    if cdn:
        plotly_tag = '<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@3.1.0/plotly.min.js"></script>'
    else:
        from plotly.offline import get_plotlyjs  # optional dependency: pip install -e ".[viz]"

        plotly_tag = f"<script>{get_plotlyjs()}</script>"
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    title = html.escape(" · ".join(e["title"].split(" — ")[0] for e in payload["experiments"]))
    values = {"<!--PLOTLY-->": plotly_tag, "/*PAYLOAD*/null": data, "{{TITLE}}": f"Experiments {title}"}
    return re.sub("|".join(map(re.escape, values)), lambda m: values[m.group(0)], template)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Comparison of experiments as one self-contained HTML page.")
    parser.add_argument("--e5", default="runs/f6/v1/e5", help="E5 directory with summary.csv and p5_*.csv")
    parser.add_argument("--e7", default="runs/f7/e7", help="E7 directory with network*/ and p7_*.csv")
    parser.add_argument("-o", "--out", default="runs/replay/experiments.html", help="output HTML file")
    parser.add_argument("--cdn", action="store_true", help="load plotly.js from a CDN instead of inlining it")
    args = parser.parse_args(argv)
    experiments = []
    if (Path(args.e5) / "p5_metrics.csv").exists():
        experiments.append(load_e5(args.e5))
    if (Path(args.e7) / "p7_metrics.csv").exists():
        experiments.append(load_e7(args.e7, args.e5))
    if not experiments:
        parser.error("no p5_metrics.csv or p7_metrics.csv: run notebooks/07_e5_propagation.ipynb and "
                     "notebooks/08_e7_network_share.ipynb first")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build_payload(experiments), cdn=args.cdn), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
