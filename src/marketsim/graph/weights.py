"""Graph weights from stock prices (owner: D2).

For every company i, regress its daily log returns on the returns of its parents and of the market index:

    r_i,t = alpha_i + sum_j w_ij * r_j,t + beta_mkt_i * r_mkt,t + u_i,t

over the estimation period (2021-01-01 .. 2023-12-31). Two weight sets:
- "direct": parents are external nodes only (commodities + index)  -> used by myopic valuers;
- "full":   parents are external nodes and parent companies        -> used by network valuers.

Reference numbers to reproduce (752 trading days 2021-2023): docs/plans/phases/F2_dependency_graph.md.

Stability rule: keep the function signature and output columns; extend the implementation freely.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

WEIGHT_COLUMNS = ["parent", "child", "w_direct", "w_full", "t_stat_direct", "t_stat_full", "source"]


def ols(y, X):
    """y: Series; X: DataFrame of parents. Returns (coefficients, t-statistics, R^2) without the intercept."""
    Xm = np.column_stack([np.ones(len(X)), X.to_numpy()])
    b, *_ = np.linalg.lstsq(Xm, y.to_numpy(), rcond=None)
    resid = y.to_numpy() - Xm @ b
    s2 = resid @ resid / (len(y) - Xm.shape[1])
    se = np.sqrt(np.diag(s2 * np.linalg.inv(Xm.T @ Xm)))
    r2 = 1 - resid.var() / y.to_numpy().var()
    return dict(zip(X.columns, b[1:])), dict(zip(X.columns, b[1:] / se[1:])), r2



def estimate_weights(
    returns: pd.DataFrame,
    edges: pd.DataFrame,
    market: str = "sp500",
    start: str = "2021-01-01",
    end: str = "2023-12-31",
) -> pd.DataFrame:
    """
    Returns columns: parent, child, w_direct, w_full, t_stat_direct, t_stat_full, source.
    The market index edge (``market`` -> child) is added for every company.

    Why: the weights are the agents' knowledge of "how much this company moves when its parent moves".
    Used by: save_graph -> data/graph/<name>/ -> valuers in the simulation (S); reference_path (D2).
    """
    returns = returns.loc[(returns.index >= start) & (returns.index <= end)]
    
    results = []
    
    children = edges["child"].unique()
    
    for child in children:
        if child not in returns.columns:
            continue
            
        external_parents = edges[(edges["child"] == child) & (edges["source"].isin(["commodity", "index"]))]["parent"].tolist()
        if market not in external_parents and market in returns.columns:
            external_parents.append(market)
            
        X_dir_cols = [p for p in external_parents if p in returns.columns]
        if X_dir_cols:
            y = returns[child]
            data_dir = pd.concat([y, returns[X_dir_cols]], axis=1).dropna()
            if len(data_dir) > 10:
                params_dir, t_stats_dir, _ = ols(data_dir[child], data_dir[X_dir_cols])
            else:
                params_dir, t_stats_dir = {}, {}
        else:
            params_dir, t_stats_dir = {}, {}

        full_parents = edges[edges["child"] == child]["parent"].tolist()
        if market not in full_parents and market in returns.columns:
            full_parents.append(market)
            
        X_full_cols = [p for p in full_parents if p in returns.columns]
        if X_full_cols:
            data_full = pd.concat([returns[child], returns[X_full_cols]], axis=1).dropna()
            if len(data_full) > 10:
                params_full, t_stats_full, _ = ols(data_full[child], data_full[X_full_cols])
            else:
                params_full, t_stats_full = {}, {}
        else:
            params_full, t_stats_full = {}, {}

        # dict.fromkeys keeps first-seen order, so edges.csv is identical between runs (a set is not)
        all_parents = list(dict.fromkeys(external_parents + full_parents))
        for parent in all_parents:
            if parent == market:
                source = "index"
            else:
                matching_edge = edges[(edges["child"] == child) & (edges["parent"] == parent)]
                if matching_edge.empty:
                    raise ValueError(f"no source for edge {parent} -> {child}")
                source = matching_edge["source"].iloc[0]

            w_dir, t_dir = float("nan"), float("nan")
            if parent in params_dir:
                w_dir = float(params_dir[parent])
                t_dir = float(t_stats_dir[parent])

            w_full, t_full = float("nan"), float("nan")
            if parent in params_full:
                w_full = float(params_full[parent])
                t_full = float(t_stats_full[parent])

            results.append({
                "parent": parent,
                "child": child,
                "w_direct": w_dir,
                "w_full": w_full,
                "t_stat_direct": t_dir,
                "t_stat_full": t_full,
                "source": source,
            })

    return pd.DataFrame(results, columns=WEIGHT_COLUMNS)
