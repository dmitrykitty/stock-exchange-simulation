"""Metrics of one run of the network market, read from its log (contract K3) and the graph (contract K2).

All metrics use end-of-session values: the price ``P`` (midpoint, last trade when a side is empty) and the reference
``R`` at the last book snapshot of every session, after ``warmup`` sessions.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim.graph.api import DependencyGraph, load_graph
from marketsim.metrics import as_of, price_series, reference_series, wave_metrics
from marketsim.sim.log_schema import RunLog, load_run

# Companies of real_v1 by their distance from the commodities, and the tech pair whose parents (NVDA, AMD) only
# network valuers see.
GROUPS = {"d1": ["COP", "EOG"], "d2": ["VLO", "MPC"], "d3": ["DAL", "UAL", "ODFL"], "tech": ["GOOGL", "META"]}
# A run collapses when a price ends a session more than a factor of 2 away from its reference after warm-up.
COLLAPSE_GAP = math.log(2)
GAP_DAYS_THRESHOLD = 0.10
MARKET_NODE = "sp500"
# Share of the sessions with the largest market moves used for the stress comovement.
STRESS_SHARE = 0.1


def daily(log: RunLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Price and reference (ticks) at the end of every session: two frames, sessions x tickers."""
    tickers = log.meta["assets"]
    ends = np.arange(log.meta["config"]["n_sessions"]) + 1 - 1e-9  # the last snapshot of every session
    p = np.column_stack([as_of(price_series(log, a), ends) for a in range(len(tickers))])
    r = np.column_stack([as_of(reference_series(log, a), ends) for a in range(len(tickers))])
    return pd.DataFrame(p, columns=tickers).ffill(), pd.DataFrame(r, columns=tickers)


def market_levels(log: RunLog) -> pd.Series:
    """Level of the market index (external node ``sp500``) in every session, from the session-start events."""
    events = log["events"]
    starts = events.loc[events.kind == "session_start", "payload"].map(json.loads)
    levels = {p["session"]: p.get(MARKET_NODE, np.nan) for p in starts}
    return pd.Series(levels, dtype=float).sort_index()


def ols_day0_day1(r: np.ndarray, q: np.ndarray) -> tuple[float, float]:
    """Pooled OLS (with intercept) of returns ``r`` on the same-day and previous-day change of R (``q``); both are
    arrays days x companies. Returns the two slopes: the same-day and the next-day share of a move of R."""
    y, x0, x1 = r[1:].ravel(), q[1:].ravel(), q[:-1].ravel()
    ok = np.isfinite(y) & np.isfinite(x0) & np.isfinite(x1)
    if ok.sum() < 3 or np.var(x0[ok]) == 0:
        return math.nan, math.nan
    beta = np.linalg.lstsq(np.column_stack([np.ones(ok.sum()), x0[ok], x1[ok]]), y[ok], rcond=None)[0]
    return float(beta[1]), float(beta[2])


def propagation_metrics(log: RunLog, warmup: int) -> dict:
    """P5 and P7: how moves of R reach the prices (pooled and per group), lag, amplification, gaps, collapse."""
    P, R = daily(log)
    gap = np.log(P / R).iloc[warmup:]
    r, q = np.log(P).diff().iloc[warmup + 1:], np.log(R).diff().iloc[warmup + 1:]
    out: dict = {}
    out["day0"], out["day1"] = ols_day0_day1(r.to_numpy(), q.to_numpy())
    corr = r.corrwith(q)
    for name, members in GROUPS.items():
        members = [m for m in members if m in r.columns]
        if not members:
            continue
        b0, b1 = ols_day0_day1(r[members].to_numpy(), q[members].to_numpy())
        out[f"day0_{name}"], out[f"day1_{name}"], out[f"pass2_{name}"] = b0, b1, b0 + b1
        out[f"corr_{name}"] = float(corr[members].mean())
    wave = [wave_metrics(log, a, t0=float(warmup), step=0.25, max_lag=12) for a in range(len(P.columns))]
    out["lag"] = float(np.nanmean([w["lag_sessions"] for w in wave]))
    out["vol_amp"] = float(np.nanmedian([w["volatility_amplification"] for w in wave]))
    out["corr_R"] = float(corr.median())
    out["gap_days"] = int((gap.abs() > GAP_DAYS_THRESHOLD).to_numpy().sum())
    out["acf1"] = float(np.mean([r[t].autocorr(1) for t in r.columns]))
    out["worst_gap"] = float(gap.abs().to_numpy().max())
    out["collapse"] = bool(out["worst_gap"] > COLLAPSE_GAP)
    return out


def _ancestors(graph: DependencyGraph, node: str, skip: frozenset[str]) -> set[str]:
    seen: set[str] = set()
    stack = [node]
    while stack:
        for parent in graph.parents(stack.pop(), "full"):
            if parent not in seen and parent not in skip:
                seen.add(parent)
                stack.append(parent)
    return seen


def related(graph: DependencyGraph, a: str, b: str, market: str = MARKET_NODE) -> bool:
    """True when the graph links ``a`` and ``b`` other than through the market index: one is an ancestor of the
    other or they share an ancestor (a commodity or a company)."""
    skip = frozenset({market})
    up_a, up_b = _ancestors(graph, a, skip), _ancestors(graph, b, skip)
    return a in up_b or b in up_a or bool(up_a & up_b)


@lru_cache(maxsize=8)
def _graph(name: str) -> DependencyGraph:
    return load_graph(name)


def pair_kinds(graph: DependencyGraph, tickers: list[str]) -> dict[tuple[str, str], bool]:
    """Every unordered pair of ``tickers`` -> whether the graph relates them (``related``)."""
    return {(a, b): related(graph, a, b) for i, a in enumerate(tickers) for b in tickers[i + 1:]}


def residuals(r: pd.DataFrame, q: pd.DataFrame) -> pd.DataFrame:
    """Daily price returns of every company less what its own reference explains: OLS of ``r_i`` on the same-day
    and previous-day change of ``R_i``. What is left is the part of a price move that the graph does not give."""
    out = pd.DataFrame(np.nan, index=r.index, columns=r.columns)
    for c in r.columns:
        y, x0, x1 = r[c].to_numpy(), q[c].to_numpy(), q[c].shift(1).to_numpy()
        ok = np.isfinite(y) & np.isfinite(x0) & np.isfinite(x1)
        if ok.sum() < 3:
            continue
        x = np.column_stack([np.ones(ok.sum()), x0[ok], x1[ok]])
        beta = np.linalg.lstsq(x, y[ok], rcond=None)[0]
        out.loc[ok, c] = y[ok] - x @ beta
    return out


def comovement_metrics(log: RunLog, warmup: int, graph: DependencyGraph | None = None) -> dict:
    """P6: do companies the graph does not relate move together beyond what their references say?

    - ``resid_unrelated`` / ``resid_related``: mean correlation of the residual returns (``residuals``) over the
      pairs the graph does not relate / relates; with independent order flows the unrelated pairs are near zero;
    - ``*_stress``: the same on the ``STRESS_SHARE`` sessions with the largest market-index moves;
    - ``false_links``: share of unrelated pairs with a residual correlation above 0.1;
    - ``excess_unrelated``: ``corr(r^P_i, r^P_j) - corr(r^R_i, r^R_j)`` over unrelated pairs (the proposal's
      definition; below zero when idiosyncratic noise dilutes the common factor of R);
    - ``risk_triggers``: stop-loss sales started in the run.
    """
    graph = graph or _graph(log.meta["config"]["graph"])
    P, R = daily(log)
    r, q = np.log(P).diff().iloc[warmup + 1:], np.log(R).diff().iloc[warmup + 1:]
    u = residuals(r, q)
    kinds = pair_kinds(graph, list(P.columns))
    unrelated = [pair for pair, flag in kinds.items() if not flag]
    linked = [pair for pair, flag in kinds.items() if flag]

    def mean(matrix: pd.DataFrame, pairs: list[tuple[str, str]]) -> float:
        values = [matrix.loc[pair] for pair in pairs]
        values = [v for v in values if np.isfinite(v)]
        return float(np.mean(values)) if values else math.nan

    market = np.log(market_levels(log)).diff().reindex(r.index).abs()
    stress = market >= market.quantile(1 - STRESS_SHARE) if market.notna().sum() > 10 and market.max() > 0 else None
    cu = u.corr()
    out = {"resid_unrelated": mean(cu, unrelated), "resid_related": mean(cu, linked)}
    if stress is not None:
        cs = u.loc[stress].corr()
        out["resid_unrelated_stress"], out["resid_related_stress"] = mean(cs, unrelated), mean(cs, linked)
    else:
        out["resid_unrelated_stress"] = out["resid_related_stress"] = math.nan
    values = [cu.loc[pair] for pair in unrelated if np.isfinite(cu.loc[pair])]
    out["false_links"] = float(np.mean([v > 0.1 for v in values])) if values else math.nan
    out["excess_unrelated"] = mean(r.corr() - q.corr(), unrelated)
    out["risk_triggers"] = int(log.meta.get("risk_limit_triggers", 0))
    return out


def run_metrics(run_dir: str | Path, warmup: int | None = None, comovement: bool = True) -> dict:
    """Every metric of one run, with its ``run_id``; ``warmup`` defaults to the run's ``warmup_sessions``."""
    log = load_run(run_dir)
    warmup = log.meta["config"]["warmup_sessions"] if warmup is None else warmup
    out = {"run_id": Path(run_dir).name, **propagation_metrics(log, warmup)}
    if comovement:
        out.update(comovement_metrics(log, warmup))
    return out


def shock_groups(graph: DependencyGraph, tickers: list[str], shocked: str) -> dict[str, list[str]]:
    """Companies by their relation to the shocked one: itself, its descendants in the graph (``linked``) and the
    companies the graph does not relate to it (``unrelated``)."""
    descendants = [t for t in tickers if t != shocked and shocked in _ancestors(graph, t, frozenset({MARKET_NODE}))]
    unrelated = [t for t in tickers if t != shocked and not related(graph, t, shocked)]
    return {"target": [shocked], "linked": descendants, "unrelated": unrelated}


def shock_response(shocked_dir: str | Path, base_dir: str | Path, horizons: tuple[int, ...] = (1, 5, 20)) -> dict:
    """Response to a company shock in a pair of runs with the same config and seed, without and with the shock:
    mean over each group of ``ln P_shocked - ln P_base`` ``h`` sessions after the shock, and the mean absolute value
    of the same for the unrelated companies (the size of the moves, whatever their direction). A horizon past the
    end of the run uses its last session."""
    shocked, base = load_run(shocked_dir), load_run(base_dir)
    shock = shocked.meta["resolved_agents"]["company_shock"]
    graph = _graph(shocked.meta["config"]["graph"])
    p1, _ = daily(shocked)
    p0, _ = daily(base)
    diff = np.log(p1) - np.log(p0)
    groups = shock_groups(graph, list(p1.columns), shock["asset"])
    start = math.floor(shock["time"])  # the session the shock happens in; its end is the first value after it
    out: dict = {"run_id": Path(shocked_dir).name, "base_run_id": Path(base_dir).name}
    for h in horizons:
        k = min(start + h, len(diff) - 1)
        for name, members in groups.items():
            out[f"{name}_{h}"] = float(diff[members].iloc[k].mean()) if members else math.nan
        out[f"unrelated_abs_{h}"] = float(diff[groups["unrelated"]].iloc[k].abs().mean())
    out["risk_triggers_extra"] = (int(shocked.meta.get("risk_limit_triggers", 0))
                                  - int(base.meta.get("risk_limit_triggers", 0)))
    return out
