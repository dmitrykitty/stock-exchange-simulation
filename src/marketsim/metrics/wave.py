"""Wave metrics (phase F5, experiment E4): how the market passed a shock along the chain.

Everything is measured against the reference path R (``valuations.reference``): the price each
company would have if all agents reacted instantly and exactly to the graph. Definitions and the
worked example: docs/model/explainer.md, section 8.2.

Setup shared by the per-asset metrics:

- ``t0`` is the shock time; by default the time of the largest total jump of ``ln R`` over all
  assets (``shock_time``). In E4 this is the session where oil jumps.
- The series is sampled on a grid: the last point strictly before ``t0`` ("start", before the
  shock) followed by every point in ``[t0, end]`` ("after"), where ``end`` is the window end
  (``n_sessions``). With ``step=None`` the grid is the book snapshots (``sample_dt``); with
  ``step=h`` it is ``t0 - h, t0, t0 + h, ...``. Times in results are in sessions.
- ``P`` is ``series.price_series`` (midpoint; last trade when a side is empty); ``R`` and ``P``
  are read as of each grid time.
"""

from __future__ import annotations

import math

import numpy as np

from marketsim.metrics.series import as_of, price_series, reference_series
from marketsim.metrics.stability import assets, window
from marketsim.sim.log_schema import RunLog

__all__ = ["WAVE_METRICS", "chain_amplification", "shock_time", "wave_metrics", "wave_summary"]

NAN = float("nan")

WAVE_METRICS = (
    "completeness", "overshoot", "lag_sessions", "error_half_life_sessions", "volatility_amplification",
    "price_log_change", "reference_log_change",
)


def shock_time(log: RunLog) -> float:
    """Time of the largest total jump ``sum_i |Δ ln R_i|`` over all assets; ``nan`` if R never moves."""
    best_t, best = NAN, 0.0
    totals: dict[float, float] = {}
    for asset in assets(log):
        ref = reference_series(log, asset)
        ref = ref.loc[ref > 0]
        jumps = np.abs(np.diff(np.log(ref.to_numpy())))
        for t, jump in zip(ref.index[1:], jumps, strict=True):
            totals[float(t)] = totals.get(float(t), 0.0) + float(jump)
    for t in sorted(totals):
        if totals[t] > best:
            best_t, best = t, totals[t]
    return best_t


# ---------------------------------------------------------------------------------------------
# Building blocks on aligned arrays (exposed for tests and for the notebook)
# ---------------------------------------------------------------------------------------------

def _grid(log: RunLog, asset: int, t0: float, step: float | None) -> np.ndarray:
    _, end = window(log)
    if step is not None:
        if not step > 0:
            raise ValueError("step must be positive")
        n_after = math.floor((end - t0) / step + 1e-9)
        return t0 + step * np.arange(-1, n_after + 1)
    times = price_series(log, asset).index.to_numpy(dtype=float)
    before, after = times[times < t0], times[(times >= t0) & (times <= end)]
    if not len(before) or not len(after):
        return np.zeros(0)
    return np.concatenate([before[-1:], after])


def _end_value(t: np.ndarray, log_values: np.ndarray, end_window: float) -> float:
    """``ln X_end``: the last value, or the mean over the last ``end_window`` sessions of the "after" part."""
    if end_window <= 0:
        return float(log_values[-1])
    mask = t >= t[-1] - end_window
    mask[0] = False  # never the pre-shock point
    return float(np.mean(log_values[mask])) if mask.any() else float(log_values[-1])


def _lag_steps(log_r: np.ndarray, log_p: np.ndarray, max_lag: int | None) -> float:
    """Shift k (steps) maximizing the cross-correlation of ``Δ ln R_t`` and ``Δ ln P_{t+k}``, k >= 0.

    Returns are not demeaned: right after a shock the mean price change *is* the response, so
    removing it would hide the wave. The normalization is fixed (full-series norms), so short
    overlaps at large k are not favoured.
    """
    dr, dp = np.diff(log_r), np.diff(log_p)
    norm = math.sqrt(float(dr @ dr) * float(dp @ dp))
    if norm == 0:
        return NAN
    n = len(dr)
    max_lag = n // 2 if max_lag is None else min(max_lag, n - 1)
    corr = [float(dr[: n - k] @ dp[k:]) / norm for k in range(max_lag + 1)]
    return float(np.argmax(corr))


def _half_life(t: np.ndarray, error: np.ndarray) -> float:
    """Time from ``t[0]`` until ``error`` first falls to half of ``error[0]``, linearly interpolated."""
    half = error[0] / 2
    if error[0] == 0:
        return 0.0
    below = np.nonzero(error <= half)[0]
    if not len(below):
        return NAN
    i = below[0]
    frac = (error[i - 1] - half) / (error[i - 1] - error[i])
    return float(t[i - 1] + frac * (t[i] - t[i - 1]) - t[0])


def _volatility_amplification(p: np.ndarray, r: np.ndarray) -> float:
    rp, rr = np.diff(np.log(p)), np.diff(np.log(r))
    if len(rr) < 2:
        return NAN
    sr = float(np.std(rr, ddof=1))
    return float(np.std(rp, ddof=1)) / sr if sr > 0 else NAN


# ---------------------------------------------------------------------------------------------
# Public metrics
# ---------------------------------------------------------------------------------------------

def wave_metrics(log: RunLog, asset: int, t0: float | None = None, step: float | None = None,
                 end_window: float = 0.0, max_lag: int | None = None, vol_step: float = 1.0) -> dict:
    """Wave metrics of one asset: a dict with the keys of ``WAVE_METRICS`` (one summary.csv row part).

    - ``completeness`` = ``(ln P_end - ln P_start) / (ln R_end - ln R_start)``: 1 the market passed
      the whole reference move, > 1 amplified, < 1 damped; ``nan`` if R did not move.
    - ``overshoot`` = ``(max_{t >= t0} P_t - R_end) / R_end`` for a rise; for a fall the mirror
      ``(R_end - min P_t) / R_end``, so positive always means "went past the reference".
    - ``lag_sessions``: the shift k >= 0 with the largest cross-correlation of ``Δ ln R_t`` and
      ``Δ ln P_{t+k}``, times the grid step (``max_lag`` in steps, default half the series).
    - ``error_half_life_sessions``: time from ``t0`` until ``|P - R| / R`` first falls to half of
      its value at ``t0`` (linear interpolation between grid points); ``nan`` if it never does.
    - ``volatility_amplification`` = ``σ(Δ ln P) / σ(Δ ln R)`` on a grid with step ``vol_step``
      sessions anchored at ``t0`` (at the window start when there is no shock).
    - ``price_log_change``, ``reference_log_change``: numerator and denominator of completeness.

    ``end_window > 0`` replaces the single end point by the mean of ``ln P`` (and ``ln R``) over the
    last ``end_window`` sessions, which is more robust when prices oscillate after the shock.
    """
    out = dict.fromkeys(WAVE_METRICS, NAN)
    t0 = shock_time(log) if t0 is None else float(t0)
    price, ref = price_series(log, asset), reference_series(log, asset)

    start, end = window(log)
    anchor = start if math.isnan(t0) else t0
    vol_grid = anchor + vol_step * np.arange(-1, math.floor((end - anchor) / vol_step + 1e-9) + 1)
    vp, vr = as_of(price, vol_grid), as_of(ref, vol_grid)
    ok = np.isfinite(vp) & np.isfinite(vr) & (vp > 0) & (vr > 0)
    out["volatility_amplification"] = _volatility_amplification(vp[ok], vr[ok])

    if math.isnan(t0):
        return out
    t = _grid(log, asset, t0, step)
    if len(t) < 2:
        return out
    p, r = as_of(price, t), as_of(ref, t)
    if not (np.all(np.isfinite(p)) and np.all(np.isfinite(r)) and np.all(p > 0) and np.all(r > 0)):
        # Drop grid points without a price (e.g. before the first quote), but keep the start point.
        keep = np.isfinite(p) & np.isfinite(r) & (p > 0) & (r > 0)
        if not keep[0] or keep.sum() < 2:
            return out
        t, p, r = t[keep], p[keep], r[keep]
    log_p, log_r = np.log(p), np.log(r)

    dp = _end_value(t, log_p, end_window) - log_p[0]
    dr = _end_value(t, log_r, end_window) - log_r[0]
    out["price_log_change"], out["reference_log_change"] = dp, dr
    if dr != 0:
        out["completeness"] = dp / dr
        r_end = math.exp(_end_value(t, log_r, end_window))
        after = p[1:]
        out["overshoot"] = ((after.max() - r_end) if dr > 0 else (r_end - after.min())) / r_end

    lag = _lag_steps(log_r, log_p, max_lag)
    out["lag_sessions"] = lag * float(np.median(np.diff(t))) if not math.isnan(lag) else NAN
    out["error_half_life_sessions"] = _half_life(t[1:], np.abs(p[1:] - r[1:]) / r[1:])
    return out


def chain_amplification(log: RunLog, first: int = 0, last: int | None = None, **kwargs) -> float:
    """Completeness on the last link of the chain divided by completeness on the first.

    In E4 the assets are in chain order (COP, VLO, DAL), so the defaults compare DAL with COP.
    > 1: the wave grows along the chain; < 1: it fades. ``kwargs`` go to ``wave_metrics``.
    """
    last = assets(log)[-1] if last is None else last
    c_first = wave_metrics(log, first, **kwargs)["completeness"]
    c_last = wave_metrics(log, last, **kwargs)["completeness"]
    if math.isnan(c_first) or math.isnan(c_last) or c_first == 0:
        return NAN
    return c_last / c_first


def wave_summary(log: RunLog, **kwargs) -> dict:
    """One flat summary.csv row for the run: ``<metric>__<ticker>`` for every asset plus chain values."""
    names = log.meta.get("assets") or [str(a) for a in assets(log)]
    t0 = kwargs.pop("t0", None)
    t0 = shock_time(log) if t0 is None else t0
    row = {"run_id": log.meta.get("run_id"), "seed": log.meta.get("seed"), "shock_time": t0}
    completeness = {}
    for asset in assets(log):
        metrics = wave_metrics(log, asset, t0=t0, **kwargs)
        completeness[asset] = metrics["completeness"]
        row.update({f"{name}__{names[asset]}": value for name, value in metrics.items()})
    ids = assets(log)
    first, last = completeness[ids[0]], completeness[ids[-1]]
    row["chain_amplification"] = last / first if first and not (math.isnan(first) or math.isnan(last)) else NAN
    return row
