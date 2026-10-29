"""Graph structure from economics (owner: D2): which company -> company edges make sense.

Rule: a supplier -> customer edge exists if the customer's industry spends at least ``threshold`` of its
output on the supplier industry's products (BEA input-output table). Details and code snippets:
docs/plans/phases/F2_dependency_graph.md.

Stability rule: keep the function signature and output columns; extend the implementation freely.
"""

from __future__ import annotations

import pandas as pd


def candidate_company_edges(io_matrix: pd.DataFrame, companies: pd.DataFrame, threshold: float = 0.02) -> pd.DataFrame:
    """
    io_matrix -- marketsim.data.api.load_io_matrix(year): A.loc[j, i] = share of industry i's output spent on j;
    companies -- marketsim.data.api.load_companies().

    Returns columns: parent, child (tickers), parent_bea, child_bea, cost_share.
    Pairs within the same company are excluded; pairs within the same industry are kept only if
    the industry buys from itself above the threshold.

    Why: limits company -> company edges to economically meaningful supplier -> customer links, so that the
    graph does not connect everything that merely correlates.
    Used by: estimate_weights (D2), then the graph files read by the simulation (S).
    """
    edges = []

    for _, child in companies.iterrows():
        child_ticker = child["ticker"]
        child_bea = str(child["bea_code"])

        for _, parent in companies.iterrows():
            parent_ticker = parent["ticker"]
            parent_bea = str(parent["bea_code"])

            if child_ticker == parent_ticker:
                continue

            if parent_bea in io_matrix.index and child_bea in io_matrix.columns:
                cost_share = float(io_matrix.loc[parent_bea, child_bea])

                if cost_share >= threshold:
                    edges.append({
                        "parent": parent_ticker,
                        "child": child_ticker,
                        "parent_bea": parent_bea,
                        "child_bea": child_bea,
                        "cost_share": cost_share,
                    })

    return pd.DataFrame(edges)
