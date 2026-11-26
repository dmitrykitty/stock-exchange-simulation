"""Reproduce the supplementary E7 oil regression from the main study's saved logs."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.metrics import as_of, price_series
from marketsim.sim.log_schema import RunLog
from marketsim.validation.reality import load_reality


def main() -> None:
    reality = load_reality()
    factors = np.log(reality.levels[reality.graph.externals()]).diff()
    regressors = pd.concat([factors, factors.shift().add_suffix("_lag")], axis=1)
    rows = []
    for meta_path in sorted(Path("runs/main/e7").glob("network*/*/meta.json")):
        meta = json.loads(meta_path.read_text())
        directory = meta_path.parent
        assets = [meta["assets"].index(ticker) for ticker in ("VLO", "MPC")]
        tables = {
            name: pd.read_parquet(directory / f"{name}.parquet", filters=[("asset", "in", assets)])
            for name in ("book", "trades")
        }
        log = RunLog(directory, meta, tables)
        ends = np.arange(meta["config"]["n_sessions"]) + 1 - 1e-9
        prices = pd.DataFrame({a: as_of(price_series(log, a), ends) for a in assets})
        first = meta["config"]["warmup_sessions"] + 2
        y = np.log(prices).diff().iloc[first:]
        x = regressors.iloc[first:].assign(intercept=1)
        coefficients = np.linalg.lstsq(x, y, rcond=None)[0].mean(axis=1)
        rows.append(dict(zip(x.columns, coefficients)) | {
            "seed": meta["seed"], "network": meta["resolved_agents"]["valuers_network"],
        })
    results = pd.DataFrame(rows)
    print(results.groupby("network")[["wti", "wti_lag"]].mean())
    pairs = results.pivot(index="seed", columns="network", values="wti_lag")
    differences = (pairs[250] - pairs[0]).to_numpy()
    rng = np.random.default_rng(42)
    draws = rng.choice(differences, (10000, len(differences))).mean(axis=1)
    print("250 minus 0, mean and 95% interval:", differences.mean(), np.quantile(draws, [0.025, 0.975]))


if __name__ == "__main__":
    main()
