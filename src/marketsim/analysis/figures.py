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

from marketsim.analysis.stats import one_sample

BLUE, ORANGE, GREEN, GREY, RED = "#1f77b4", "#ff7f0e", "#2ca02c", "#7f7f7f", "#d62728"


def _decimal(value: float, _position: int | None = None) -> str:
    return f"{value:g}".replace(".", ",")


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
    shown = [("day1", "Reakcja w następnej sesji"), ("lag", "Opóźnienie ceny względem $R$ (sesje)"),
             ("vol_amp", "Wzmocnienie zmienności"), ("corr_R", "Korelacja stóp zwrotu z $R$"),
             ("gap_days", "Liczba odchyleń od $R$"), ("day1_d2", "Reakcja rafinerii w następnej sesji")]
    fig, axes = plt.subplots(2, 3, figsize=(15, 7.5))
    for ax, (metric, title) in zip(axes.flat, shown):
        part = c.loc[c.metric == metric].reset_index(drop=True)
        _forest(ax, part, [f"{a} ({b})".replace(".", ",") for a, b in zip(part.contrast, part["at"])],
                [BLUE if f == "mix" else ORANGE for f in part.factor])
        ax.set_title(title, fontsize=10)
        ax.xaxis.set_major_formatter(_decimal)
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
    cells = dict(tuple(runs.groupby("share")))
    rng = np.random.default_rng(2027)
    fig, ax = plt.subplots(figsize=(8.4, 3.5))
    positions = np.arange(len(cells))
    for metric, offset, label, color in [
        ("day0_d2", -0.18, "W tej samej sesji", BLUE),
        ("day1_d2", 0.18, "W następnej sesji", ORANGE),
    ]:
        intervals = pd.DataFrame([one_sample(cell, metric, rng) for cell in cells.values()])
        means = intervals["mean"]
        errors = [means - intervals.ci_low, intervals.ci_high - means]
        bars = ax.bar(positions + offset, means, width=0.34, color=color, label=label, yerr=errors, capsize=3)
        labels = [f"{value:.2f}".replace(".", ",") for value in means]
        ax.bar_label(bars, labels=labels, padding=3)
    ax.axhline(0, color=GREY, linewidth=0.8)
    ax.set_xticks(positions, [str(share * 5) for share in cells])
    ax.set_xlabel("Liczba wyceniających obserwujących kursy dostawców (spośród 300)")
    ax.set_ylabel("Zmiana kursu rafinerii (%)")
    ax.yaxis.set_major_formatter(_decimal)
    ax.margins(y=0.2)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.19), ncol=2, frameon=False)
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
        fig, axes = plt.subplots(1, 3, figsize=(10.4, 3.3), layout="constrained")
        for ax, (metric, title) in zip(axes, [
                ("resid_unrelated", "Wszystkie sesje"),
                ("resid_unrelated_stress", "Największe zmiany indeksu"),
                ("risk_triggers", "Uruchomienia limitu ryzyka")]):
            part = m.loc[m.metric == metric]
            for k, (h, color) in enumerate(zip(sorted(part.h.unique()), (BLUE, ORANGE))):
                p = part.loc[part.h == h].set_index("channels").reindex(order)
                x = np.arange(len(order)) + (k - 0.5) * 0.25
                ax.errorbar(x, p["mean"], yerr=[p["mean"] - p.ci_low, p.ci_high - p["mean"]], fmt="o", capsize=3,
                            color=color, label=f"$h$ = {_decimal(h)}")
            ax.set_xticks(range(len(order)), ["Obie\nreguły", "Bez\nlimitu", "Bez rebalan-\nsowania", "Bez obu\nreguł"])
            ax.set_title(title, fontsize=11)
            ax.yaxis.set_major_formatter(_decimal)
            ax.axhline(0, color="k", lw=0.6)
            if metric in real.index:
                ax.axhline(real[metric], color=GREEN, ls="--", lw=1.2, label="Rynek rzeczywisty")
        axes[0].set_ylabel("Średnia korelacja reszt")
        axes[2].set_ylabel("Średnia liczba na przebieg")
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="outside upper center", ncols=len(labels), frameon=False)
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
        fig, axes = plt.subplots(1, 3, figsize=(10.4, 3.3), layout="constrained")
        for ax, group, title in zip(axes, ("target", "linked", "unrelated"),
                                    ("NVDA", "GOOGL i META", "15 spółek niepowiązanych")):
            for h, color in zip(sorted(e.h.unique()), (BLUE, ORANGE)):
                p = e.loc[(e.h == h) & e.metric.isin([f"{group}_{k}" for k in horizons])].copy()
                p["k"] = p.metric.str.rsplit("_", n=1).str[1].astype(int)
                p = p.sort_values("k")
                ax.errorbar(p.k, 100 * p["mean"], yerr=[100 * (p["mean"] - p.ci_low), 100 * (p.ci_high - p["mean"])],
                            fmt="o-", capsize=3, color=color, label=f"$h$ = {_decimal(h)}")
            ax.axhline(0, color="k", lw=0.6)
            ax.set_xticks(horizons)
            ax.yaxis.set_major_formatter(_decimal)
            ax.set_title(title, fontsize=11)
        axes[0].set_ylabel("Różnica logarytmów cen × 100")
        axes[1].set_xlabel("Liczba sesji od wiadomości")
        axes[0].legend(frameon=False)
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
                    label="Rynek rzeczywisty (przedział 95%)")
        ax.plot(x, d["R (real_v1)"], "s", color=ORANGE, label="Trajektoria referencyjna $R$")
        labels = {"sim: mixed, h=0.2": "Symulacja na badanym grafie", "sim: random graph 1": "Graf losowy 1",
                  "sim: random graph 2": "Graf losowy 2", "sim: flat inputs": "Stałe sygnały"}
        for name, marker in zip(sim[:4], ("^", "x", "+", ".")):
            ax.plot(x, d[name], marker, label=labels.get(name, name.removeprefix("sim: ")), alpha=0.8)
        ax.axhline(0, color=GREY, lw=0.8)
        ax.set_xticks(x, d.ticker)
        ax.set_ylabel("Średnia reakcja ponad wpływ indeksu")
        ax.yaxis.set_major_formatter(_decimal)
        ax.legend(fontsize=8)
        fig.tight_layout()
        paths.append(_save(fig, out / "f8_event_study.png"))
    if (v / "graph_recovery_by_edge.csv").exists():
        e = pd.read_csv(v / "graph_recovery_by_edge.csv")
        main = next(c for c in e.columns if c.startswith("sim: "))
        fig, ax = plt.subplots(figsize=(14, 4))
        x = np.arange(len(e))
        ax.bar(x - 0.27, e["planted (2021-2023)"], 0.27, label="Wagi agentów (2021–2023)", color=GREY)
        ax.bar(x, e["real 2024-2025"], 0.27, label="Oszacowane z cen rzeczywistych", color=BLUE)
        ax.bar(x + 0.27, e[main], 0.27, label="Oszacowane z cen symulowanych",
               color=ORANGE)
        ax.set_xticks(x, e.edge, rotation=75, fontsize=7)
        ax.axhline(0, color="k", lw=0.6)
        ax.legend(fontsize=8)
        ax.set_ylabel("Waga powiązania")
        ax.yaxis.set_major_formatter(_decimal)
        fig.tight_layout()
        paths.append(_save(fig, out / "f8_graph_recovery.png"))
    return paths


def e5_cells(root: Path, out: Path) -> Path:
    """E5: cell means for large price gaps and next-session response, excluding collapsed runs."""
    plt = _plt()
    s = pd.read_csv(root / "e5" / "summary.csv")[["run_id", "agents.composition", "agents.herding.h"]]
    runs = s.merge(pd.read_csv(root / "e5" / "p5_metrics.csv"), on="run_id").rename(
        columns={"agents.composition": "mix", "agents.herding.h": "h"})
    runs["collapse"] = runs.collapse.astype(bool)
    mixes = [m for m in ("network", "mixed", "trend") if m in set(runs.mix)]
    hs = sorted(runs.h.unique())
    shown = [("gap_days", r"Duże odchylenia od $R$ ($N_{10}$)", 1),
             ("day1", r"Reakcja w następnej sesji ($\pi_1$)", 2)]
    collapsed = runs.groupby(["mix", "h"]).collapse.agg(["sum", "size"])
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 2.45), layout="constrained")
    for ax, (metric, title, digits) in zip(axes, shown):
        if metric not in runs:
            ax.set_visible(False)
            continue
        table = runs.loc[~runs.collapse].pivot_table(index="mix", columns="h", values=metric).reindex(mixes)[hs]
        im = ax.imshow(table.to_numpy(), cmap="Blues", aspect="auto")
        for i, mix in enumerate(mixes):
            for j, h in enumerate(hs):
                k, n = collapsed.loc[(mix, h)]
                value = table.iat[i, j]
                note = f"\n({k}/{n})" if k else ""
                label = f"{value:.{digits}f}".replace(".", ",")
                color = "white" if im.norm(value) > 0.65 else "#10233b"
                ax.text(j, i, label + note, ha="center", va="center", fontsize=11, color=color)
        ax.set_xticks(range(len(hs)), [f"$h$ = {_decimal(h)}" for h in hs], fontsize=10)
        ax.set_yticks(range(len(mixes)), mixes, fontstyle="italic", fontsize=10)
        ax.tick_params(length=0)
        ax.set_title(title, fontsize=11)
        fig.colorbar(im, ax=ax, fraction=0.05, format=_decimal)
    return _save(fig, out / "f7_e5_cells.png")


def validation_more(root: Path, out: Path) -> list[Path]:
    """F8: the graph out of sample, stylised facts, correlation matrices and price bands."""
    plt = _plt()
    v = root / "validation"
    paths = []
    if (v / "out_of_sample_by_company.csv").exists():
        per = pd.read_csv(v / "out_of_sample_by_company.csv").sort_values("R (real_v1)")
        summary = pd.read_csv(v / "out_of_sample.csv").set_index("model")["median corr"]
        fig, ax = plt.subplots(figsize=(10.0, 4.0), layout="constrained")
        x = np.arange(len(per))
        bars = [("R (real_v1)", "Pełny graf", ORANGE),
                ("R (direct exposures)", "Ekspozycje bezpośrednie", GREEN),
                ("beta only", "Beta rynkowa", GREY)]
        bars = [b for b in bars if b[0] in per]
        width = 0.8 / len(bars)
        for k, (column, label, color) in enumerate(bars):
            ax.bar(x + (k - (len(bars) - 1) / 2) * width, per[column], width, label=label, color=color)
        for key, style, label in (("R of random graphs: mean", ":", "Średnia grafów losowych"),
                                  ("R of random graphs: 95th percentile", "--", "95. percentyl grafów losowych")):
            if key in summary:
                ax.axhline(summary[key], color=BLUE, ls=style, lw=1, label=label)
        ax.set_xticks(x, per.ticker)
        ax.set_ylabel("Korelacja dziennych stóp zwrotu\nz danymi rzeczywistymi")
        ax.yaxis.set_major_formatter(_decimal)
        handles, labels = ax.get_legend_handles_labels()
        fig.legend(handles, labels, loc="outside upper center", ncols=3, frameon=False)
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
        for ax, col, title in zip(axes, ("real", "sim", "R"), ("Rynek rzeczywisty 2024–2025", "Symulacja bazowa",
                                                               "Trajektoria referencyjna $R$")):
            m = c.pivot(index="a", columns="b", values=col).loc[order, order]
            im = ax.imshow(m.to_numpy(), cmap="RdBu_r", vmin=-1, vmax=1)
            ax.set_xticks(range(len(order)), order, rotation=90, fontsize=7)
            ax.set_yticks(range(len(order)), order, fontsize=7)
            ax.set_title(title, fontsize=10)
        fig.colorbar(im, ax=axes, fraction=0.02, format=_decimal)
        paths.append(_save(fig, out / "f8_correlation.png"))
    if (v / "band_paths.csv").exists() and (v / "bands.csv").exists():
        b = pd.read_csv(v / "band_paths.csv")
        coverage = pd.read_csv(v / "bands.csv").sort_values(["inside band", "ticker"])
        example = "VLO" if "VLO" in set(b.ticker) else coverage.iloc[-1].ticker
        p = b.loc[b.ticker == example].sort_values("session")
        fig, (price, share) = plt.subplots(2, 1, figsize=(9, 4.5), height_ratios=(1.5, 1), layout="constrained")
        price.fill_between(p.session, p.sim_lo, p.sim_hi, color=BLUE, alpha=0.28, label="Pasmo 90% symulacji")
        price.plot(p.session, p.sim_median, color=BLUE, lw=1, label="Mediana symulacji")
        price.plot(p.session, p.real, color="k", lw=1, label="Rzeczywisty kurs zamknięcia")
        price.set_ylabel("Kurs (USD)")
        price.set_xlabel("Sesja")
        price.set_title(f"{example} w latach 2024–2025", loc="left", fontsize=10)
        price.yaxis.set_major_formatter(_decimal)
        price.legend(loc="best", frameon=False)

        share.bar(coverage.ticker, 100 * coverage["inside band"],
                  color=[BLUE if ticker == example else "#9da9b4" for ticker in coverage.ticker])
        share.set_ylabel("Odsetek sesji (%)")
        share.set_title("Rzeczywisty kurs w paśmie symulacji", loc="left", fontsize=10)
        share.set_ylim(bottom=0)
        share.yaxis.set_major_formatter(_decimal)
        share.tick_params(axis="x", labelsize=8)
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
