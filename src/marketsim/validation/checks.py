"""Checks of F8: what the simulated market reproduces of the real one in 2024–2025, and what it does not.

Every check compares the same statistic for the real data, the reference R and groups of simulated runs
(``sims``: name -> list of ``SimRun``). Controls make a check meaningful: the R of random graphs (the graph's own
content) and simulations on a random graph or on flat inputs with the same seeds (the agents' part). A statistic
that a control reproduces as well as the model says nothing about the model.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from marketsim.analysis.runs import STRESS_SHARE, pair_kinds, residuals
from marketsim.validation.reality import Reality, SimRun, graph_from_structure, real_pairs

Sims = dict[str, list[SimRun]]
# R without the company-to-company edges: is the gain of the graph in them or in the direct exposures?
DIRECT = "R (direct exposures)"


# ----- 1. the graph out of sample ----------------------------------------------------------------------------------
def _fit(reality: Reality, pred: pd.DataFrame) -> pd.Series:
    """Correlation of a prediction with the real daily return, per company."""
    return pd.Series({t: pd.concat([reality.real[t], pred[t]], axis=1).dropna().corr().iloc[0, 1]
                      for t in reality.tickers})


def out_of_sample(reality: Reality, random_refs: list[pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Does the graph, estimated on 2021–2023, explain the real daily moves of 2024–2025 (R uses the same-day
    signals)? Per company: the correlation of R, of R with the direct exposures only and of beta-only with the real
    return; summary: medians and the share of random graphs whose R does at least as well as real_v1."""
    direct = reality.reference_returns(reality.graph, mode="direct")
    per = pd.DataFrame({"R (real_v1)": _fit(reality, reality.ref), "beta only": _fit(reality, reality.beta_only),
                        DIRECT: _fit(reality, direct)})
    random_median = np.array([_fit(reality, r).median() for r in random_refs])
    summary = pd.DataFrame([
        {"model": "R (real_v1)", "median corr": per["R (real_v1)"].median()},
        {"model": "beta only", "median corr": per["beta only"].median()},
        {"model": DIRECT, "median corr": per[DIRECT].median()},
        {"model": "R of random graphs: mean", "median corr": random_median.mean()},
        {"model": "R of random graphs: 95th percentile", "median corr": np.quantile(random_median, 0.95)},
        {"model": "share of random graphs >= real_v1",
         "median corr": float(np.mean(random_median >= per["R (real_v1)"].median()))},
    ])
    return per, summary


# ----- 2. reaction to large commodity moves (event study) ----------------------------------------------------------
def events_of(reality: Reality, column: str, warmup: int, share: float = 0.05, days: int = 1) -> pd.Series:
    """The ``share`` of days with the largest |moves| of ``column`` (sign of the move), windows not overlapping."""
    move = reality.real[column].iloc[warmup:-1]
    position = dict(zip(reality.real.index, range(len(reality.real))))
    chosen, used = [], set()
    for day in move.abs().sort_values(ascending=False).index:
        window = set(range(position[day], position[day] + days))
        if window & used:
            continue
        chosen.append(day)
        used |= window
        if len(chosen) == round(share * len(move)):
            break
    return pd.Series(np.sign(move.loc[chosen]), index=chosen).sort_index()


def _responses(reality: Reality, frame: pd.DataFrame, events: pd.Series, days: int, abnormal: bool) -> np.ndarray:
    data = frame[reality.tickers]
    if abnormal:
        data = data - np.outer(reality.real["sp500"], reality.beta)
    position = reality.real.index.get_indexer(events.index)
    total = sum(data.to_numpy()[position + d] for d in range(days))
    return total * events.to_numpy()[:, None]


def _compare(model: np.ndarray, truth: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> dict:
    return {"corr across companies": float(np.corrcoef(model, truth)[0, 1]),
            "inside real 95% CI": float(np.mean((model >= lo) & (model <= hi))),
            "same sign": float(np.mean(np.sign(model) == np.sign(truth))),
            "size ratio": float(model @ truth / (truth @ truth))}


def event_study(reality: Reality, sims: Sims, random_refs: list[pd.DataFrame], column: str = "wti",
                days: int = 1, abnormal: bool = True, warmup: int = 10, seed: int = 3) -> tuple[pd.DataFrame,
                                                                                                pd.DataFrame]:
    """Mean response of every company on the days of the largest moves of ``column`` (signed by the move, above
    the market beta when ``abnormal``) for the real data, R and every group of runs; compared across the 18
    companies. The real 95% interval comes from a bootstrap over blocks of 10 trading days."""
    events = events_of(reality, column, warmup, days=days)
    truth_events = _responses(reality, reality.real, events, days, abnormal)
    truth = truth_events.mean(axis=0)
    rng = np.random.default_rng(seed)
    blocks = reality.real.index.get_indexer(events.index) // 10
    groups = [np.flatnonzero(blocks == b) for b in np.unique(blocks)]
    boot = np.array([truth_events[np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])]
                     .mean(axis=0) for _ in range(2000)])
    lo, hi = np.quantile(boot, 0.025, axis=0), np.quantile(boot, 0.975, axis=0)
    means = {"R (real_v1)": _responses(reality, reality.ref, events, days, abnormal).mean(axis=0),
             "beta only": _responses(reality, reality.beta_only, events, days, abnormal).mean(axis=0),
             DIRECT: _responses(reality, reality.reference_returns(reality.graph, mode="direct"), events, days,
                                abnormal).mean(axis=0)}
    for name, runs in sims.items():
        means["sim: " + name] = np.mean([_responses(reality, r.returns, events, days, abnormal).mean(axis=0)
                                         for r in runs], axis=0)
    table = pd.DataFrame({name: _compare(m, truth, lo, hi) for name, m in means.items()}).T
    if random_refs:
        control = pd.DataFrame([_compare(_responses(reality, r, events, days, abnormal).mean(axis=0), truth, lo, hi)
                                for r in random_refs])
        table.loc["R of random graphs: mean"] = control.mean()
        table.loc["R of random graphs: 95th percentile"] = control.quantile(0.95)
        table.loc["share of random R >= R (real_v1)"] = (control >= table.loc["R (real_v1)"]).mean()
    detail = pd.DataFrame({"real": truth, "real lo": lo, "real hi": hi, **means}, index=reality.tickers)
    return table.rename_axis("model").reset_index(), detail.rename_axis("ticker").reset_index()


# ----- 3. stylised facts ------------------------------------------------------------------------------------------
def _facts(reality: Reality, returns: pd.DataFrame, volume: pd.DataFrame | None, warmup: int) -> dict:
    x = returns[reality.tickers].iloc[warmup:]
    out = {"autocorr r, lag 1": x.apply(lambda c: c.autocorr(1)).median(),
           "autocorr |r|, lag 1": x.abs().apply(lambda c: c.autocorr(1)).median(),
           "excess kurtosis": x.kurt().median(),
           "daily std, %": 100 * x.std().median(),
           "corr with real returns": x.corrwith(reality.real[reality.tickers].iloc[warmup:]).median()}
    if volume is not None:
        v = np.log(volume[reality.tickers].iloc[1:].replace(0, np.nan))
        v.index = returns.index
        v = v.iloc[warmup:]
        out["corr(log volume, |r|)"] = pd.Series({t: v[t].corr(x[t].abs()) for t in reality.tickers}).median()
    return out


def stylised_facts(reality: Reality, sims: Sims, warmup: int = 10) -> pd.DataFrame:
    """Return autocorrelation, volatility clustering, fat tails, size, fit to the real returns and the
    volume–volatility relation; for the real data, R and the mean over the runs of every group."""
    rows = {"real 2024-2025": _facts(reality, reality.real, reality.volume, warmup),
            "R (real_v1)": _facts(reality, reality.ref, None, warmup)}
    for name, runs in sims.items():
        rows["sim: " + name] = pd.DataFrame([_facts(reality, r.returns, r.volume, warmup) for r in runs]).mean()
    return pd.DataFrame(rows).T.rename_axis("series").reset_index()


# ----- 4. no next-day predictability -------------------------------------------------------------------------------
def _ols_t(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    mask = ~(np.isnan(y) | np.isnan(x))
    y, x = y[mask], np.column_stack([np.ones(mask.sum()), x[mask]])
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    resid = y - x @ beta
    cov = resid @ resid / (len(y) - 2) * np.linalg.inv(x.T @ x)
    return float(beta[1]), float(beta[1] / math.sqrt(cov[1, 1]))


def _lag_t(reality: Reality, frame: pd.DataFrame, pairs: list[tuple[str, str]]) -> pd.Series:
    t = {}
    for parent, child in pairs:
        source = frame[parent] if parent in reality.tickers else reality.real[parent]
        t[f"{parent}->{child}"] = _ols_t(frame[child].to_numpy(), source.shift(1).to_numpy())[1]
    return pd.Series(t)


def lag_test(reality: Reality, sims: Sims, warmup: int = 10) -> pd.DataFrame:
    """Does yesterday's return of a parent predict today's return of its child? In the real data it does not
    (|t| < 2 for almost every edge); a market slower than the real one would show it. Per group: median t over
    edges and runs and the share of |t| > 2."""
    pairs, _ = real_pairs(reality.graph)
    real = reality.real.copy()
    real.iloc[:warmup] = np.nan
    rows = []
    for name, frames in [("real", [real])] + [("sim: " + n, [r.returns for r in runs]) for n, runs in sims.items()]:
        values = np.concatenate([_lag_t(reality, f, pairs).to_numpy() for f in frames])
        rows.append({"series": name, "median t": float(np.nanmedian(values)),
                     "share |t| > 2": float(np.mean(np.abs(values) > 2)), "share t > 2": float(np.mean(values > 2))})
    return pd.DataFrame(rows)


# ----- 5. the graph rebuilt from simulated prices ------------------------------------------------------------------
def graph_recovery(reality: Reality, sims: Sims) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Estimate the weights of the real_v1 structure from simulated daily returns (and, for comparison, from the
    real returns of 2024–2025) and compare them with the weights the agents use (2021–2023). Per group: the
    correlation of the weights across edges, the mean absolute error, the share of edges recovered (same sign and
    |t| > 2) and of edges with the opposite sign and |t| > 2; per edge: the mean weight."""
    pairs, kinds = real_pairs(reality.graph)
    planted = {(e.parent, e.child): e.w_full for e in reality.graph.edges if e.parent != "sp500"}
    period = (str(reality.real.index[0].date()), str(reality.real.index[-1].date()))

    def summary(frame: pd.DataFrame) -> tuple[dict, dict]:
        data = frame[reality.tickers].join(reality.real.drop(columns=reality.tickers))
        g = graph_from_structure(reality, pairs, kinds, data, period)
        got = {(e.parent, e.child): (e.w_full, e.t_stat_full) for e in g.edges if e.parent != "sp500"}
        w0 = np.array([planted[p] for p in pairs])
        w1 = np.array([got[p][0] for p in pairs])
        t1 = np.array([got[p][1] for p in pairs])
        return ({"weight corr": float(np.corrcoef(w0, w1)[0, 1]), "mean |error|": float(np.mean(np.abs(w1 - w0))),
                 "recovered": float(np.mean((np.sign(w1) == np.sign(w0)) & (np.abs(t1) > 2))),
                 "wrong sign": float(np.mean((np.sign(w1) != np.sign(w0)) & (np.abs(t1) > 2)))},
                {f"{p}->{c}": got[(p, c)][0] for p, c in pairs})

    rows, edges = [], {"planted (2021-2023)": {f"{p}->{c}": planted[(p, c)] for p, c in pairs}}
    s, e = summary(reality.real)
    rows.append({"series": "real 2024-2025", **s})
    edges["real 2024-2025"] = e
    for name, runs in sims.items():
        results = [summary(r.returns) for r in runs]
        rows.append({"series": "sim: " + name, **pd.DataFrame([x[0] for x in results]).mean()})
        edges["sim: " + name] = pd.DataFrame([x[1] for x in results]).mean()
    return pd.DataFrame(rows), pd.DataFrame(edges).rename_axis("edge").reset_index()


# ----- 6. bands of many runs ---------------------------------------------------------------------------------------
def bands(reality: Reality, runs: list[SimRun], level: float = 0.9, warmup: int = 10) -> pd.DataFrame:
    """Per company: the share of sessions after warm-up on which the real close lies inside the central ``level``
    band of the simulated closes of ``runs``, the band of R being a single line. A model with the right
    variability but no company news (60% of a real stock's moves) is not expected to cover the real path."""
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    stack = np.stack([r.prices[reality.tickers].to_numpy() for r in runs])
    lo, hi = np.nanquantile(stack, lo_q, axis=0), np.nanquantile(stack, hi_q, axis=0)
    real = reality.prices[reality.tickers].to_numpy()
    inside = ((real >= lo) & (real <= hi))[warmup:]
    width = np.median((hi / lo)[warmup:], axis=0)
    return pd.DataFrame({"ticker": reality.tickers, "inside band": inside.mean(axis=0),
                         "inside at the end": inside[-1], "median band width (hi/lo)": width})


# ----- 7. correlation structure ------------------------------------------------------------------------------------
def correlation_structure(reality: Reality, sims: Sims, warmup: int = 10) -> pd.DataFrame:
    """The 18 x 18 correlation matrix of daily returns, simulated against real: the correlation of the 153
    off-diagonal entries, their mean absolute difference and the mean level of both."""
    upper = np.triu_indices(len(reality.tickers), 1)
    real = reality.real[reality.tickers].iloc[warmup:].corr().to_numpy()[upper]

    def compare(frame: pd.DataFrame) -> dict:
        c = frame[reality.tickers].iloc[warmup:].corr().to_numpy()[upper]
        return {"corr with real matrix": float(np.corrcoef(c, real)[0, 1]),
                "mean |difference|": float(np.mean(np.abs(c - real))), "mean level": float(c.mean()),
                "real mean level": float(real.mean())}

    rows = [{"series": "R (real_v1)", **compare(reality.ref)}]
    for name, runs in sims.items():
        rows.append({"series": "sim: " + name, **pd.DataFrame([compare(r.returns) for r in runs]).mean()})
    return pd.DataFrame(rows)


def band_paths(reality: Reality, runs: list[SimRun], level: float = 0.9) -> pd.DataFrame:
    """Per session and company: the real close, R (from the real start price) and the median and central ``level``
    band of the simulated closes of ``runs`` (USD), in long form for the figures."""
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    stack = np.stack([r.prices[reality.tickers].to_numpy() for r in runs])
    start = reality.prices[reality.tickers].iloc[0].to_numpy()
    ref = start * np.exp(np.vstack([np.zeros(len(reality.tickers)), reality.ref[reality.tickers].cumsum().to_numpy()]))
    frames = {"real": reality.prices[reality.tickers].to_numpy(), "R": ref,
              "sim_lo": np.nanquantile(stack, lo_q, axis=0), "sim_median": np.nanmedian(stack, axis=0),
              "sim_hi": np.nanquantile(stack, hi_q, axis=0)}
    n = stack.shape[1]
    out = pd.DataFrame({"session": np.repeat(np.arange(n), len(reality.tickers)),
                        "ticker": np.tile(reality.tickers, n)})
    for name, values in frames.items():
        out[name] = values[:n].ravel()
    return out


def correlation_pairs(reality: Reality, runs: list[SimRun], warmup: int = 10) -> pd.DataFrame:
    """Every pair of companies: the correlation of daily returns in the real data, in R and on average over
    ``runs`` (for the matrix figures)."""
    t = reality.tickers
    real = reality.real[t].iloc[warmup:].corr()
    ref = reality.ref[t].iloc[warmup:].corr()
    sim = sum(r.returns[t].iloc[warmup:].corr() for r in runs) / len(runs)
    rows = [{"a": a, "b": b, "real": real.at[a, b], "R": ref.at[a, b], "sim": sim.at[a, b]} for a in t for b in t]
    return pd.DataFrame(rows)


# ----- 8. comovement beyond the graph ------------------------------------------------------------------------------
def comovement(reality: Reality, sims: Sims, warmup: int = 10, stress_share: float = STRESS_SHARE) -> pd.DataFrame:
    """The P6 statistic on the real data: the mean correlation of residual returns (a company's daily return less
    what its own R of ``reality.graph`` explains today and yesterday) over the pairs the graph does not relate, on
    all days and on the ``stress_share`` days with the largest real index moves, and the same for the simulations.
    With the real numbers this tells whether the simulated comovement beyond R is in the real market too."""
    t = reality.tickers
    kinds = pair_kinds(reality.graph, t)
    unrelated = [pair for pair, flag in kinds.items() if not flag]
    linked = [pair for pair, flag in kinds.items() if flag]
    q = reality.ref[t].iloc[warmup:]
    market = reality.real["sp500"].iloc[warmup:].abs()
    stress = market >= market.quantile(1 - stress_share)

    def stats(frame: pd.DataFrame) -> dict:
        u = residuals(frame[t].iloc[warmup:], q)
        cu, cs = u.corr(), u.loc[stress].corr()
        values = np.array([cu.loc[p] for p in unrelated])
        return {"resid_unrelated": float(np.nanmean(values)),
                "resid_unrelated_stress": float(np.nanmean([cs.loc[p] for p in unrelated])),
                "resid_related": float(np.nanmean([cu.loc[p] for p in linked])),
                "false_links": float(np.mean(values[np.isfinite(values)] > 0.1))}

    rows = [{"series": "real 2024-2025", **stats(reality.real)}]
    for name, runs in sims.items():
        rows.append({"series": "sim: " + name, **pd.DataFrame([stats(r.returns) for r in runs]).mean()})
    return pd.DataFrame(rows)
