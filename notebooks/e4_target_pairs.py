"""E4 under flow and target sizing (docs/guides/f5_graph_valuers.md): shared survivor pairs and seed-paired intervals.

    python notebooks/e4_target_pairs.py [RUNS_DIR] [OUT_DIR]    # defaults: runs/f5/v1, runs/f5/v1/e4_pairs

RUNS_DIR holds the experiments e4 (configs/f5_e4.yaml) and e4_target (configs/f5_e4_target.yaml). Definitions as in
notebooks/05_chain_wave.ipynb: session paths are mean two-sided midpoints, a crash is a drawdown above 25% of any
asset after the shock. A pair is the two runs with the same cell, seed and shock direction.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.market.api import NO_PRICE
from marketsim.sim.log_schema import load_run

RUNS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("runs/f5/v1")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else RUNS / "e4_pairs"
SHOCK = 5
TICKERS = ["COP", "VLO", "DAL"]


def session_paths(log):
    book = log["book"]
    book = book.loc[(book.best_bid != NO_PRICE) & (book.best_ask != NO_PRICE)]
    book = book.assign(mid=(book.best_bid + book.best_ask) / 200, session=np.floor(book.t).astype(int))
    val = log["valuations"].assign(session=lambda d: np.floor(d.t).astype(int))
    mids = book.groupby(["asset", "session"]).mid.mean().unstack(0)
    ref = (val.groupby(["asset", "session"]).reference.mean() / 100).unstack(0)
    return mids, ref


def run_row(directory):
    log = load_run(directory)
    mids, ref = session_paths(log)
    row = {"run_id": log.meta["run_id"], "seed": log.meta["seed"]}
    for a, t in enumerate(TICKERS):
        m, r = mids[a].reindex(range(30)), ref[a].reindex(range(30))
        move = np.log(r.iloc[-1] / r.iloc[SHOCK - 1])
        for s in range(30):
            row[f"{t}_{s}"] = np.log(m.iloc[s] / m.iloc[SHOCK - 1]) / move
        after = m.iloc[SHOCK:]
        row[f"dd_{t}"] = float(np.max(1 - after / after.cummax()))
        row[f"err_{t}"] = float(np.mean(np.abs(np.log(after / r.iloc[SHOCK:]))))
        gap = np.log(m / r).iloc[10:30]
        row[f"osc_{t}"] = float(gap.std())
        returns = np.log(m).diff().iloc[SHOCK + 1:30]
        row[f"ac_{t}"] = float(returns.autocorr(1))
    return row


def table(name):
    cache = OUT / f"{name}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    summary = pd.read_csv(RUNS / name / "summary.csv")
    rows = pd.DataFrame([run_row(RUNS / name / r) for r in summary.run_id])
    keep = [c for c in summary.columns if c.startswith("agents.") or c in ("run_id", "external")]
    out = summary[keep].merge(rows, on="run_id")
    out["crash"] = out[[f"dd_{t}" for t in TICKERS]].max(axis=1) > 0.25
    out.to_parquet(cache)
    return out


OUT.mkdir(parents=True, exist_ok=True)
flow, target = table("e4"), table("e4_target")
key = ["agents.composition", "agents.herding.h", "external", "seed"]
pairs = flow.merge(target, on=key, suffixes=("_flow", "_target"))
print("pairs", len(pairs), "crash share", pairs.crash_flow.mean(), pairs.crash_target.mean())

windows = {"5-6": range(5, 7), "7-9": range(7, 10), "10-14": range(10, 15)}
rows = []
for (comp, h), g in pairs.groupby(["agents.composition", "agents.herding.h"]):
    both = g.loc[~g.crash_flow & ~g.crash_target]
    row = {"composition": comp, "h": h, "n": len(g), "flow survivors": int((~g.crash_flow).sum()),
           "target survivors": int((~g.crash_target).sum()), "shared survivors": len(both)}
    for t in ("COP", "VLO"):
        for w, ss in windows.items():
            for rule in ("flow", "target"):
                cols = [f"{t}_{s}_{rule}" for s in ss]
                own = g.loc[~g[f"crash_{rule}"], cols].mean(axis=1).median()
                shared = both[cols].mean(axis=1).median()
                row[f"{t} {w} {rule} own"] = own
                row[f"{t} {w} {rule} shared"] = shared
    rows.append(row)
catch = pd.DataFrame(rows)
catch.to_csv(OUT / "catch_up.csv", index=False)
pd.set_option("display.width", 250)
print(catch[["composition", "h", "n", "flow survivors", "target survivors", "shared survivors",
             "COP 7-9 flow own", "COP 7-9 flow shared", "COP 7-9 target own", "COP 7-9 target shared",
             "VLO 7-9 flow own", "VLO 7-9 flow shared", "VLO 7-9 target own", "VLO 7-9 target shared"]].round(2))

# Seed-paired intervals: difference target - flow per (seed, shock), mean of the two shocks per seed, bootstrap over
# the 30 seeds (all runs, crashed or not: both rules ran on the same seeds and shocks).
measures = {"err_COP": "deviation COP", "osc_COP": "oscillation COP", "err_VLO": "deviation VLO",
            "osc_VLO": "oscillation VLO", "ac_VLO": "autocorrelation VLO"}
rng = np.random.default_rng(2026)
ci = []
for (comp, h), g in pairs.groupby(["agents.composition", "agents.herding.h"]):
    for m, label in measures.items():
        diff = (g[f"{m}_target"] - g[f"{m}_flow"]).groupby(g["seed"]).mean().dropna().to_numpy()
        boot = diff[rng.integers(0, len(diff), (5000, len(diff)))].mean(axis=1)
        ci.append({"composition": comp, "h": h, "measure": label, "seeds": len(diff), "mean diff": diff.mean(),
                   "lo": np.quantile(boot, 0.025), "hi": np.quantile(boot, 0.975)})
ci = pd.DataFrame(ci)
ci.to_csv(OUT / "paired_ci.csv", index=False)
print(ci.round(4).to_string())
print("entirely below zero:", int((ci.hi < 0).sum()), "of", len(ci))
