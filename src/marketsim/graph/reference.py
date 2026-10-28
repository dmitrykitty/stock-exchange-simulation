"""Reference path R(t): what prices would be if every agent applied the full graph weights instantly and
exactly (owner: D2). Agents never see it; it is the ruler for the metrics.

    ln R_i(t) - ln R_i(0) = sum_{j in parents(i)} w_ij * (ln R_j(t) - ln R_j(0)),
    R_k(t) = X_k(t) for external nodes (commodities, market index).

Evaluate in topological order from external nodes to companies (or solve the linear system).
Formula and worked example: docs/model/model_description.md, sections 4.5-4.6.
"""

from __future__ import annotations

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
    raise NotImplementedError("reference_path (owner: D2, see docs/plans/phases/F2_dependency_graph.md)")
