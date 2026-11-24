"""The real data the simulation is compared with (F8), loaded once, and the simulated series in the same shape.

Real stock prices are never shown to the agents. They enter in three places only: 2021–2023 to estimate the graph
weights, 2 January 2024 as the start prices, and 2024–2025 here, for the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.analysis.runs import daily
from marketsim.data import api
from marketsim.graph.api import DependencyGraph, Edge, GraphError, load_graph
from marketsim.graph.reference import reference_path
from marketsim.graph.weights import estimate_weights
from marketsim.sim.log_schema import load_run

TRAIN = ("2021-01-01", "2023-12-31")


@dataclass
class Reality:
    graph: DependencyGraph
    tickers: list[str]
    levels: pd.DataFrame          # external levels of the scenario, one row per session
    real: pd.DataFrame            # real daily log returns 2024–2025: companies and the scenario's signals
    prices: pd.DataFrame          # real adjusted closes 2024–2025 (USD) rescaled to the start prices of the graph
    volume: pd.DataFrame          # real traded shares, one row per session
    train: pd.DataFrame           # daily log returns 2021–2023 (the weights' period)
    beta: pd.Series               # market beta of every company on 2021–2023
    ref: pd.DataFrame             # daily log returns of the reference R of ``graph`` on the real signals
    beta_only: pd.DataFrame       # beta x the real index return: the simplest benchmark

    def reference_returns(self, graph: DependencyGraph) -> pd.DataFrame:
        """Daily log returns of the reference path of another graph on the same real signals."""
        out = np.log(reference_path(graph, self.levels, mode="full")).diff().iloc[1:]
        out.index = self.real.index
        return out[self.tickers]


def load_reality(graph: str = "real_v1", scenario: str = "real_2024_2025") -> Reality:
    """Real 2024–2025 data aligned with the sessions of ``scenario``."""
    g = load_graph(graph)
    tickers = g.companies()
    returns = api.load_returns_daily()
    external = api.load_external(scenario)
    dates = pd.DatetimeIndex(external["date"])
    levels = external.drop(columns="date")
    real = returns.loc[dates[1:], tickers].copy()
    signals = np.log(levels).diff().iloc[1:]
    signals.index = real.index
    real[signals.columns] = signals           # the scenario's own signal series (copper from LME)
    prices = api.load_stock_prices().loc[dates, tickers]
    prices.index = pd.RangeIndex(len(dates), name="session")
    # Adjusted closes are scaled down by the dividends paid later; rescaled to start at the simulation's start
    # price (the close of the first session in the graph), they keep their returns and share its level.
    start = pd.Series({t: g.nodes[t].start_price for t in tickers})
    prices = prices * (start / prices.iloc[0])
    volume = api.load_stock_volume().loc[dates, tickers]
    volume.index = prices.index
    train = returns.loc[TRAIN[0]:TRAIN[1]]
    beta = pd.Series({t: np.polyfit(*train[["sp500", t]].dropna().to_numpy().T, 1)[0] for t in tickers})
    ref = np.log(reference_path(g, levels, mode="full")).diff().iloc[1:]
    ref.index = real.index
    beta_only = pd.DataFrame({t: beta[t] * real["sp500"] for t in tickers})
    return Reality(g, tickers, levels, real, prices, volume, train, beta, ref[tickers], beta_only)


@dataclass
class SimRun:
    run_id: str
    returns: pd.DataFrame         # daily log returns on the real dates, NaN during warm-up
    prices: pd.DataFrame          # close in USD per session
    volume: pd.DataFrame          # traded shares per session


def load_sim(run_dir: str | Path, reality: Reality) -> SimRun:
    """A simulated run in the shape of ``Reality``: end-of-session midpoint, returns on the real dates."""
    log = load_run(run_dir)
    warmup = log.meta["config"]["warmup_sessions"]
    P, _ = daily(log)
    P = P[reality.tickers]
    returns = np.log(P).diff().iloc[1:]
    returns.index = reality.real.index[:len(returns)]
    returns.iloc[:warmup] = np.nan
    trades = log["trades"]
    names = log.meta["assets"]
    volume = (trades.assign(session=np.floor(trades.t).astype(int), ticker=lambda d: [names[a] for a in d.asset])
              .groupby(["session", "ticker"]).qty.sum().unstack().reindex(range(len(P)), fill_value=0))
    return SimRun(Path(run_dir).name, returns, P / 100.0, volume.reindex(columns=reality.tickers, fill_value=0))


def _edge_table(graph: DependencyGraph, pairs: list[tuple[str, str]], kinds: dict[str, str]) -> pd.DataFrame:
    rows = [(p, c, kinds[p]) for p, c in pairs] + [("sp500", c, "index") for c in graph.companies()]
    return pd.DataFrame(rows, columns=["parent", "child", "source"])


def real_pairs(graph: DependencyGraph) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """The non-index edges of ``graph`` and the source kind of every parent."""
    pairs = [(e.parent, e.child) for e in graph.edges if e.parent != "sp500"]
    kinds = {p: "commodity" if graph.nodes[p].kind == "external" else "bea" for p, _ in pairs}
    return pairs, kinds


def graph_from_structure(reality: Reality, pairs: list[tuple[str, str]], kinds: dict[str, str],
                         returns: pd.DataFrame | None = None, period: tuple[str, str] = TRAIN) -> DependencyGraph:
    """A graph with the given edges and weights estimated by ``estimate_weights`` on ``returns`` (default: the
    real returns of 2021–2023)."""
    returns = api.load_returns_daily() if returns is None else returns
    weights = estimate_weights(returns, _edge_table(reality.graph, pairs, kinds), start=period[0], end=period[1])

    def opt(value: float) -> float | None:
        return None if np.isnan(value) else float(value)

    edges = [Edge(r.parent, r.child, opt(r.w_direct), opt(r.w_full), opt(r.t_stat_direct), opt(r.t_stat_full),
                  r.source, "re-estimated") for r in weights.itertuples()]
    return DependencyGraph(dict(reality.graph.nodes), edges, {"name": "re-estimated"}).validate()


def random_graphs(reality: Reality, n: int = 200, seed: int = 7, swaps: int = 400
                  ) -> list[tuple[DependencyGraph, list[tuple[str, str]]]]:
    """Control graphs: the real edges with parents and children swapped at random (every company keeps its
    number of parents and every parent its number of children), weights re-estimated on 2021–2023. Rewirings
    whose feedback is unstable are skipped. The same ``seed`` gives the same graphs."""
    pairs0, kinds = real_pairs(reality.graph)
    rng = np.random.default_rng(seed)
    returns = api.load_returns_daily()
    out = []
    while len(out) < n:
        pairs = list(pairs0)
        for _ in range(swaps):
            i, j = rng.choice(len(pairs), 2, replace=False)
            (p1, c1), (p2, c2) = pairs[i], pairs[j]
            if p1 == c2 or p2 == c1 or (p1, c2) in pairs or (p2, c1) in pairs:
                continue
            pairs[i], pairs[j] = (p1, c2), (p2, c1)
        try:
            out.append((graph_from_structure(reality, pairs, kinds, returns), pairs))
        except GraphError:
            continue
    return out
