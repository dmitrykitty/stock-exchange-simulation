"""Statistics of the F7 experiments. The unit is a run; two cells are compared on the same seeds (paired).

A paired contrast is the mean over seeds of ``metric(cell a) - metric(cell b)`` with a percentile bootstrap
interval over the seeds (95%, and the Bonferroni level for ``family`` contrasts). Collapsed runs (``collapse``) are
left out pairwise: a seed counts only when neither of its two runs collapsed.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

DRAWS = 5000


def _interval(d: np.ndarray, rng: np.random.Generator, family: int, draws: int) -> dict:
    if len(d) == 0:
        return {"pairs": 0, "mean": math.nan, "ci_low": math.nan, "ci_high": math.nan, "clear": False,
                "clear (Bonferroni)": False}
    boot = rng.choice(d, (draws, len(d))).mean(axis=1)
    lo, hi = np.quantile(boot, [0.025, 0.975])
    alpha = 0.05 / max(family, 1)
    blo, bhi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    return {"pairs": len(d), "mean": float(d.mean()), "ci_low": float(lo), "ci_high": float(hi),
            "clear": bool(lo > 0 or hi < 0), "clear (Bonferroni)": bool(blo > 0 or bhi < 0)}


def _by_seed(cell: pd.DataFrame, metric: str) -> pd.DataFrame:
    if not cell["seed"].is_unique:
        raise ValueError("a cell must have one run per seed")
    columns = [metric] + (["collapse"] if "collapse" in cell else [])
    out = cell.set_index("seed")[columns].copy()
    if "collapse" not in out:
        out["collapse"] = False
    out["collapse"] = out["collapse"].astype(bool)
    return out


def paired(a: pd.DataFrame, b: pd.DataFrame, metric: str, rng: np.random.Generator, family: int = 1,
           draws: int = DRAWS) -> dict:
    """Paired contrast ``a - b`` of ``metric`` over the seeds both cells have and neither lost to a collapse."""
    x = _by_seed(a, metric).add_suffix("_a").join(_by_seed(b, metric).add_suffix("_b"), how="inner")
    x = x.loc[~x.collapse_a & ~x.collapse_b]
    d = (x[metric + "_a"] - x[metric + "_b"]).to_numpy(dtype=float)
    return _interval(d[np.isfinite(d)], rng, family, draws)


def one_sample(cell: pd.DataFrame, metric: str, rng: np.random.Generator, family: int = 1,
               draws: int = DRAWS) -> dict:
    """Mean of ``metric`` over the runs of one cell with its bootstrap interval (a metric that is itself a paired
    difference, such as the response to a shock against the same seed without it)."""
    d = cell[metric].to_numpy(dtype=float)
    return _interval(d[np.isfinite(d)], rng, family, draws)


def did(a1: pd.DataFrame, a0: pd.DataFrame, b1: pd.DataFrame, b0: pd.DataFrame, metric: str,
        rng: np.random.Generator, draws: int = DRAWS) -> dict:
    """Difference in differences ``(a1 - a0) - (b1 - b0)`` per seed, e.g. how much more herding changes the trend
    mix than the mixed one; seeds with a collapse in any of the four cells are left out."""
    parts = [_by_seed(c, metric) for c in (a1, a0, b1, b0)]
    seeds = set(parts[0].index).intersection(*(p.index for p in parts[1:]))
    keep = sorted(s for s in seeds if not any(p.at[s, "collapse"] for p in parts))
    v = [p.loc[keep, metric].to_numpy(dtype=float) for p in parts]
    d = (v[0] - v[1]) - (v[2] - v[3])
    return _interval(d[np.isfinite(d)], rng, 1, draws)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval of a proportion ``k / n``."""
    if n == 0:
        return math.nan, math.nan
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def fisher_greater(a: int, n1: int, b: int, n2: int) -> float:
    """One-sided Fisher exact test: p-value that group 1 (``a`` of ``n1``) has a higher rate than group 2."""
    total, k = n1 + n2, a + b

    def p(x: int) -> float:
        return math.comb(n1, x) * math.comb(n2, k - x) / math.comb(total, k)

    return float(sum(p(x) for x in range(a, min(n1, k) + 1)))
