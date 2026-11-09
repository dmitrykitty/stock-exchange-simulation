"""Reference path R(t): what prices would be if every agent applied the full graph weights instantly and
exactly (owner: D2). Agents never see it; it is the ruler for the metrics.

    ln R_i(t) - ln R_i(0) = sum_{j in parents(i)} w_ij * (ln R_j(t) - ln R_j(0)),
    R_k(t) = X_k(t) for external nodes (commodities, market index).

Evaluate in topological order from external nodes to companies (or solve the linear system).
Formula and worked example: docs/model/model_description.md, sections 4.5-4.6.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from marketsim.graph.api import DependencyGraph


def reference_path(graph: DependencyGraph, external: pd.DataFrame, mode: str = "full") -> pd.DataFrame:
    """
    graph    -- dependency graph (contract K2);
    external -- table from marketsim.data.api.load_external(): index "session", one column per external node;
    mode     -- "full" (default) or "direct".

    Returns a DataFrame indexed by "session" with one column per company: reference price levels,
    starting at each company's ``start_price`` at session 0.

    Why: the ruler for "did the market get it right": transmission, lag, overshoot, excess comovement.
    Used by: metrics (D1), the valuations table of the run log (S), charts (dashed line).
    Stability rule: keep the signature and output format; tests/contracts/test_graph_contract.py pins it.
    """
    if mode not in ("full", "direct"):
        raise ValueError(f"mode must be 'direct' or 'full', got {mode!r}")
    graph.validate()
    if external.empty:
        raise ValueError("external signals must contain at least one session")
    if not external.columns.is_unique:
        raise ValueError("external signal columns must be unique")

    companies = graph.companies()
    externals = graph.externals()
    missing = [name for name in externals if name not in external.columns]
    if missing:
        raise ValueError(f"external signals are missing graph nodes: {missing}")

    try:
        external_values = external.loc[:, externals].to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("external signal levels must be numeric") from exc
    if not np.isfinite(external_values).all() or (external_values <= 0).any():
        raise ValueError("external signal levels must be finite and > 0")

    names, company_weights = graph.company_weight_matrix(mode)
    if names != companies:
        raise ValueError("company order changed while building the reference path")

    external_log_changes = np.log(external_values) - np.log(external_values[0])
    external_weights = np.zeros((len(companies), len(externals)), dtype=float)
    external_positions = {name: position for position, name in enumerate(externals)}
    for company_position, company in enumerate(companies):
        for parent, weight in graph.parents(company, mode).items():
            if parent in external_positions:
                external_weights[company_position, external_positions[parent]] = weight

    if companies:
        system = np.eye(len(companies)) - company_weights
        company_log_changes = np.linalg.solve(
            system,
            external_weights @ external_log_changes.T,
        ).T
        start_prices = np.array([graph.nodes[name].start_price for name in companies], dtype=float)
        price_levels = np.exp(np.log(start_prices) + company_log_changes)
    else:
        price_levels = np.empty((len(external), 0), dtype=float)

    index = external.index.rename("session")
    return pd.DataFrame(price_levels, index=index, columns=companies)
