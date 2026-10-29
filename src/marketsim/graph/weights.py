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

import pandas as pd
import statsmodels.api as sm


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
            
        external_parents = edges[(edges["child"] == child) & (edges["source"] == "external")]["parent"].tolist()
        if market not in external_parents and market in returns.columns:
            external_parents.append(market)
            
        X_dir_cols = [p for p in external_parents if p in returns.columns]
        if X_dir_cols:
            X_dir = sm.add_constant(returns[X_dir_cols])
            y = returns[child]
            data_dir = pd.concat([y, X_dir], axis=1).dropna()
            if len(data_dir) > 10:
                model_dir = sm.OLS(data_dir[child], data_dir[X_dir_cols + ["const"]]).fit()
            else:
                model_dir = None
        else:
            model_dir = None

        full_parents = edges[edges["child"] == child]["parent"].tolist()
        if market not in full_parents and market in returns.columns:
            full_parents.append(market)
            
        X_full_cols = [p for p in full_parents if p in returns.columns]
        if X_full_cols:
            X_full = sm.add_constant(returns[X_full_cols])
            data_full = pd.concat([returns[child], X_full], axis=1).dropna()
            if len(data_full) > 10:
                model_full = sm.OLS(data_full[child], data_full[X_full_cols + ["const"]]).fit()
            else:
                model_full = None
        else:
            model_full = None

        all_parents = set(external_parents + full_parents)
        for parent in all_parents:
            if parent == market:
                source = "market"
            else:
                matching_edge = edges[(edges["child"] == child) & (edges["parent"] == parent)]
                source = matching_edge["source"].iloc[0] if not matching_edge.empty else "unknown"

            w_dir, t_dir = 0.0, 0.0
            if model_dir and parent in model_dir.params:
                w_dir = float(model_dir.params[parent])
                t_dir = float(model_dir.tvalues[parent])

            w_full, t_full = 0.0, 0.0
            if model_full and parent in model_full.params:
                w_full = float(model_full.params[parent])
                t_full = float(model_full.tvalues[parent])

            results.append({
                "parent": parent,
                "child": child,
                "w_direct": w_dir,
                "w_full": w_full,
                "t_stat_direct": t_dir,
                "t_stat_full": t_full,
                "source": source,
            })

    return pd.DataFrame(results)