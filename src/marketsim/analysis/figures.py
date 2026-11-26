"""Figures of the report from the tables of the main study (``study`` and ``marketsim.validation.run``).

    python -m marketsim.analysis.figures --root runs/main --out docs/figures

Every figure is drawn only from CSV tables, so it can be redrawn without the run logs. Needs matplotlib
(``pip install -e ".[notebooks]"``).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

BLUE, ORANGE, GREEN, GREY, RED = "#1f77b4", "#ff7f0e", "#2ca02c", "#7f7f7f", "#d62728"


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"figure.dpi": 110, "font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    return plt


def _forest(ax, part: pd.DataFrame, labels: list[str], colors: list[str]) -> None:
    y = np.arange(len(part))[::-1]
    ax.errorbar(part["mean"], y, xerr=[part["mean"] - part.ci_low, part.ci_high - part["mean"]], fmt="none",
                ecolor=GREY, capsize=2)
    ax.scatter(part["mean"], y, c=colors, zorder=3, s=18)
    ax.axvline(0, color="k", lw=0.8)
    ax.set_yticks(y, labels, fontsize=7)


def p5(root: Path, out: Path) -> Path:
    """Paired contrasts of E5: investor mix against mixed (blue), herding against h = 0 (orange)."""
    plt = _plt()
    c = pd.read_csv(root / "e5" / "p5_contrasts.csv")
    shown = [("day1", "next-day share of a move of R"), ("lag", "lag of P behind R, sessions"),
             ("vol_amp", "volatility amplification σ(P)/σ(R)"), ("corr_R", "correlation of daily returns with R"),
             ("gap_days", "company-days with |ln P/R| > 10%"), ("day1_d2", "next-day share, refiners (2 links)")]
    fig, axes = plt.subplots(2, 3, figsize=(15, 7.5))
    for ax, (metric, title) in zip(axes.flat, shown):
        part = c.loc[c.metric == metric].reset_index(drop=True)
        _forest(ax, part, [f"{a} ({b})" for a, b in zip(part.contrast, part["at"])],
                [BLUE if f == "mix" else ORANGE for f in part.factor])
        ax.set_title(title, fontsize=10)
    fig.suptitle("E5: paired contrasts on the same seeds, 95% bootstrap intervals; "
                 "blue: investor mix against mixed, orange: herding against h = 0")
    fig.tight_layout()
    return _save(fig, out / "f7_p5_contrasts.png")


def p7(root: Path, out: Path) -> Path:
    """E7: cell means with 95% intervals against the share of network valuers."""
    plt = _plt()
    runs = pd.read_csv(root / "e7" / "p7_metrics.csv")
    shares = []
    for sub in sorted((root / "e7").glob("network*")):
        if (sub / "summary.csv").exists():
            s = pd.read_csv(sub / "summary.csv")[["run_id"]].assign(share=int(sub.name.removeprefix("network")))
            shares.append(s)
    e5 = pd.read_csv(root / "e5" / "summary.csv")
    e5 = e5.loc[(e5["agents.composition"] == "mixed") & (e5["agents.herding.h"] == 0.2), ["run_id"]].assign(share=30)
    runs = pd.concat(shares + [e5]).merge(runs, on="run_id")
    runs = runs.loc[~runs.collapse.astype(bool)]
    shown = [("pass2_d2", "refiners: share of R passed in 2 days"), ("day1_d2", "refiners: next-day share"),
             ("corr_d2", "refiners: correlation with R"), ("corr_tech", "GOOGL, META: correlation with R"),
             ("vol_amp", "volatility amplification"), ("gap_days", "company-days with |ln P/R| > 10%")]
    fig, axes = plt.subplots(2, 3, figsize=(14, 7))
    for ax, (metric, title) in zip(axes.flat, shown):
        g = runs.groupby("share")[metric]
        m, se = g.mean(), g.sem()
        ax.errorbar(m.index, m, yerr=1.96 * se, fmt="o-", capsize=3, color=BLUE)
        ax.set_xticks(sorted(runs.share.unique()), [f"{s}%" + ("\n(E5)" if s == 30 else "")
                                                    for s in sorted(runs.share.unique())])
        ax.set_title(title, fontsize=10)
    fig.suptitle("E7: share of network valuers among the 500 investors (300 valuers, h = 0.2), cell means with 95% "
                 "intervals")
    fig.tight_layout()
    return _save(fig, out / "f7_p7_network_share.png")


def p6(root: Path, out: Path) -> list[Path]:
    """E6: residual comovement of unrelated companies by wallet channel and herding; response to company news."""
    plt = _plt()
    paths = []
    means_path = root / "e6" / "p6_means.csv"
    if means_path.exists():
        m = pd.read_csv(means_path)
        order = ["all", "no_stoploss", "no_rebalancing", "neither"]
        # the same statistic on the real market (validation check ``comovement``), when it was computed
        real_path = root / "validation" / "comovement.csv"
        real = (pd.read_csv(real_path).set_index("series").loc["real 2024-2025"] if real_path.exists()
                else pd.Series(dtype=float))
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
        for ax, (metric, title) in zip(axes, [
                ("resid_unrelated", "unrelated pairs: residual correlation"),
                ("resid_unrelated_stress", "the same on the 10% largest market days"),
                ("risk_triggers", "stop-loss sales per run")]):
            part = m.loc[m.metric == metric]
            for k, (h, color) in enumerate(zip(sorted(part.h.unique()), (BLUE, ORANGE))):
                p = part.loc[part.h == h].set_index("channels").reindex(order)
                x = np.arange(len(order)) + (k - 0.5) * 0.25
                ax.errorbar(x, p["mean"], yerr=[p["mean"] - p.ci_low, p.ci_high - p["mean"]], fmt="o", capsize=3,
                            color=color, label=f"h = {h:g}")
            ax.set_xticks(range(len(order)), ["all channels", "no stop-loss", "no rebalancing", "neither"])
            ax.set_title(title, fontsize=10)
            ax.axhline(0, color="k", lw=0.6)
            if metric in real.index:
                ax.axhline(real[metric], color=GREEN, ls="--", lw=1.2, label="real 2024–2025")
        axes[0].legend()
        fig.suptitle("E6, real signals 2024–2025: comovement of companies the graph does not relate, beyond their "
                     "own references (means over runs, 95% intervals)")
        fig.tight_layout()
        paths.append(_save(fig, out / "f7_p6_channels.png"))
    effects_path = root / "e6" / "p6_shock_effects.csv"
    if effects_path.exists():
        e = pd.read_csv(effects_path)
        extra_path = effects_path.with_name("p6_shock_extra.csv")
        if extra_path.exists():
            extra = pd.read_csv(extra_path)
            e = pd.concat([e, extra.loc[extra.metric.str.endswith("_0")]], ignore_index=True)
        change = e.change.min()  # the largest fall, when several shock sizes were run
        e = e.loc[e.change == change]
        horizons = sorted({int(x.rsplit("_", 1)[1]) for x in e.metric if x.rsplit("_", 1)[-1].isdigit()})
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
        for ax, group, title in zip(axes, ("target", "linked", "unrelated"),
                                    ("NVDA itself", "GOOGL, META (linked by the graph)",
                                     "the 15 companies the graph does not relate")):
            for h, color in zip(sorted(e.h.unique()), (BLUE, ORANGE)):
                p = e.loc[(e.h == h) & e.metric.isin([f"{group}_{k}" for k in horizons])].copy()
                p["k"] = p.metric.str.rsplit("_", n=1).str[1].astype(int)
                p = p.sort_values("k")
                ax.errorbar(p.k, 100 * p["mean"], yerr=[100 * (p["mean"] - p.ci_low), 100 * (p.ci_high - p["mean"])],
                            fmt="o-", capsize=3, color=color, label=f"h = {h:g}")
            ax.axhline(0, color="k", lw=0.6)
            ax.set_xticks(horizons)
            ax.set_xlabel("closing session relative to the news session")
            ax.set_ylabel("100 × difference in log prices")
            ax.set_title(title, fontsize=10)
        axes[0].legend()
        fig.suptitle(f"E6, frozen signals: company valuations {100 * change:+.0f}% at the news, against the same "
                     "seed without the news (means over the pairs, 95% intervals)")
        fig.tight_layout()
        paths.append(_save(fig, out / "f7_p6_shock.png"))
    return paths


def validation(root: Path, out: Path) -> list[Path]:
    """F8: event study by company, the graph rebuilt from simulated prices, the correlation structure."""
    plt = _plt()
    v = root / "validation"
    paths = []
    if (v / "events_wti_1d_by_company.csv").exists():
        d = pd.read_csv(v / "events_wti_1d_by_company.csv").sort_values("real")
        sim = [c for c in d.columns if c.startswith("sim: ")]
        fig, ax = plt.subplots(figsize=(14, 4.2))
        x = np.arange(len(d))
        ax.errorbar(x, d.real, yerr=[d.real - d["real lo"], d["real hi"] - d.real], fmt="ko", capsize=3,
                    label="real, 95% interval")
        ax.plot(x, d["R (real_v1)"], "s", color=ORANGE, label="reference R")
        for name, marker in zip(sim[:4], ("^", "x", "+", ".")):
            ax.plot(x, d[name], marker, label=name.removeprefix("sim: "), alpha=0.8)
        ax.axhline(0, color=GREY, lw=0.8)
        ax.set_xticks(x, d.ticker)
        ax.set_title("Days of the 5% largest oil moves: mean response above the market beta, signed by the move")
        ax.legend(fontsize=8)
        fig.tight_layout()
        paths.append(_save(fig, out / "f8_event_study.png"))
    if (v / "graph_recovery_by_edge.csv").exists():
        e = pd.read_csv(v / "graph_recovery_by_edge.csv")
        main = next(c for c in e.columns if c.startswith("sim: "))
        fig, ax = plt.subplots(figsize=(14, 4))
        x = np.arange(len(e))
        ax.bar(x - 0.27, e["planted (2021-2023)"], 0.27, label="weights the agents use (2021–2023)", color=GREY)
        ax.bar(x, e["real 2024-2025"], 0.27, label="re-estimated on real 2024–2025", color=BLUE)
        ax.bar(x + 0.27, e[main], 0.27, label=f"re-estimated on simulated prices ({main.removeprefix('sim: ')})",
               color=ORANGE)
        ax.set_xticks(x, e.edge, rotation=75, fontsize=7)
        ax.axhline(0, color="k", lw=0.6)
        ax.legend(fontsize=8)
        ax.set_title("The graph rebuilt from prices: weight of every edge")
        fig.tight_layout()
        paths.append(_save(fig, out / "f8_graph_recovery.png"))
    return paths


def e5_cells(root: Path, out: Path) -> Path:
    """E5: cell means (collapsed runs left out) of four metrics as heatmaps, with the collapses of every cell."""
    plt = _plt()
    s = pd.read_csv(root / "e5" / "summary.csv")[["run_id", "agents.composition", "agents.herding.h"]]
    runs = s.merge(pd.read_csv(root / "e5" / "p5_metrics.csv"), on="run_id").rename(
        columns={"agents.composition": "mix", "agents.herding.h": "h"})
    runs["collapse"] = runs.collapse.astype(bool)
    mixes = [m for m in ("network", "mixed", "trend") if m in set(runs.mix)]
    hs = sorted(runs.h.unique())
    shown = [("day1", "next-day share of a move of R"), ("vol_amp", "volatility amplification σ(P)/σ(R)"),
             ("gap_days", "company-days with |ln P/R| > 10%"),
             ("resid_unrelated", "residual correlation, unrelated pairs")]
    collapsed = runs.groupby(["mix", "h"]).collapse.agg(["sum", "size"])
    fig, axes = plt.subplots(1, len(shown), figsize=(4.2 * len(shown), 3.6))
    for ax, (metric, title) in zip(axes, shown):
        if metric not in runs:
            ax.set_visible(False)
            continue
        table = runs.loc[~runs.collapse].pivot_table(index="mix", columns="h", values=metric).reindex(mixes)[hs]
        im = ax.imshow(table.to_numpy(), cmap="viridis", aspect="auto")
        for i, mix in enumerate(mixes):
            for j, h in enumerate(hs):
                k, n = collapsed.loc[(mix, h)]
                note = f"\n{k}/{n} coll." if k else ""
                ax.text(j, i, f"{table.iat[i, j]:.3g}{note}", ha="center", va="center", fontsize=8, color="white")
        ax.set_xticks(range(len(hs)), [f"h = {h:g}" for h in hs])
        ax.set_yticks(range(len(mixes)), mixes)
        ax.set_title(title, fontsize=9)
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.suptitle("E5: cell means over the runs that did not collapse (collapses: max |ln P/R| > ln 2)")
    fig.tight_layout()
    return _save(fig, out / "f7_e5_cells.png")


def validation_more(root: Path, out: Path) -> list[Path]:
    """F8: the graph out of sample, stylised facts, correlation matrices and price bands."""
    plt = _plt()
    v = root / "validation"
    paths = []
    if (v / "out_of_sample_by_company.csv").exists():
        per = pd.read_csv(v / "out_of_sample_by_company.csv").sort_values("R (real_v1)")
        summary = pd.read_csv(v / "out_of_sample.csv").set_index("model")["median corr"]
        fig, ax = plt.subplots(figsize=(12, 4))
        x = np.arange(len(per))
        bars = [("R (real_v1)", "reference R of real_v1", ORANGE),
                ("R (direct exposures)", "R with direct exposures only (no company-to-company edges)", GREEN),
                ("beta only", "market beta only", GREY)]
        bars = [b for b in bars if b[0] in per]
        width = 0.8 / len(bars)
        for k, (column, label, color) in enumerate(bars):
            ax.bar(x + (k - (len(bars) - 1) / 2) * width, per[column], width, label=label, color=color)
        for key, style in (("R of random graphs: mean", ":"), ("R of random graphs: 95th percentile", "--")):
            if key in summary:
                ax.axhline(summary[key], color=BLUE, ls=style, lw=1, label=f"{key} (median over companies)")
        ax.set_xticks(x, per.ticker)
        ax.set_ylabel("corr with real daily returns 2024–2025")
        ax.set_title("The graph out of sample: weights from 2021–2023 against the real daily moves of 2024–2025 "
                     "(same-day signals)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        paths.append(_save(fig, out / "f8_out_of_sample.png"))
    if (v / "stylised_facts.csv").exists():
        f = pd.read_csv(v / "stylised_facts.csv").set_index("series")
        shown = [c for c in ("autocorr r, lag 1", "autocorr |r|, lag 1", "excess kurtosis", "corr(log volume, |r|)")
                 if c in f]
        fig, axes = plt.subplots(1, len(shown), figsize=(4 * len(shown), 3.8))
        colors = [GREEN if i.startswith("real") else ORANGE if i.startswith("R ") else BLUE for i in f.index]
        for ax, col in zip(axes, shown):
            ax.barh(range(len(f)), f[col], color=colors)
            ax.set_yticks(range(len(f)), [i.removeprefix("sim: ") for i in f.index], fontsize=7)
            ax.invert_yaxis()
            ax.axvline(0, color="k", lw=0.6)
            ax.set_title(col, fontsize=9)
        fig.suptitle("Stylised facts of daily returns: real stocks (green), reference R (orange), simulations (blue)")
        fig.tight_layout()
        paths.append(_save(fig, out / "f8_stylised_facts.png"))
    if (v / "correlation_pairs.csv").exists():
        c = pd.read_csv(v / "correlation_pairs.csv")
        order = list(dict.fromkeys(c.a))
        fig, axes = plt.subplots(1, 3, figsize=(16, 5))
        for ax, col, title in zip(axes, ("real", "sim", "R"), ("real 2024–2025", "simulation (main cell)",
                                                               "reference R")):
            m = c.pivot(index="a", columns="b", values=col).loc[order, order]
            im = ax.imshow(m.to_numpy(), cmap="RdBu_r", vmin=-1, vmax=1)
            ax.set_xticks(range(len(order)), order, rotation=90, fontsize=7)
            ax.set_yticks(range(len(order)), order, fontsize=7)
            ax.set_title(title, fontsize=10)
        fig.colorbar(im, ax=axes, fraction=0.02)
        fig.suptitle("Correlation of daily returns between the 18 companies")
        paths.append(_save(fig, out / "f8_correlation.png"))
    if (v / "band_paths.csv").exists():
        b = pd.read_csv(v / "band_paths.csv")
        chosen = [t for t in ("COP", "VLO", "UAL", "NVDA", "FCX", "JPM") if t in set(b.ticker)]
        fig, axes = plt.subplots(2, 3, figsize=(15, 7))
        for ax, t in zip(axes.flat, chosen):
            p = b.loc[b.ticker == t]
            ax.fill_between(p.session, p.sim_lo, p.sim_hi, color=BLUE, alpha=0.25, label="simulation, 90% band")
            ax.plot(p.session, p.sim_median, color=BLUE, lw=1, label="simulation, median")
            ax.plot(p.session, p.R, color=ORANGE, lw=1, ls="--", label="reference R")
            ax.plot(p.session, p.real, color="k", lw=1, label="real close")
            ax.set_title(t, fontsize=10)
            ax.set_xlabel("session (trading day of 2024–2025)")
        axes.flat[0].set_ylabel("USD")
        axes.flat[0].legend(fontsize=7)
        fig.suptitle("Prices 2024–2025: real close, reference R and the band of the main-cell runs")
        fig.tight_layout()
        paths.append(_save(fig, out / "f8_bands.png"))
    return paths


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Report figures from the main study tables.")
    parser.add_argument("--root", type=Path, default=Path("runs/main"))
    parser.add_argument("--out", type=Path, default=Path("docs/figures"))
    args = parser.parse_args(argv)
    made = []
    if (args.root / "e5" / "p5_contrasts.csv").exists():
        made.append(p5(args.root, args.out))
    if (args.root / "e7" / "p7_metrics.csv").exists():
        made.append(p7(args.root, args.out))
    if (args.root / "e5" / "p5_metrics.csv").exists():
        made.append(e5_cells(args.root, args.out))
    made += p6(args.root, args.out) + validation(args.root, args.out) + validation_more(args.root, args.out)
    for path in made:
        print(path)


if __name__ == "__main__":
    main()
