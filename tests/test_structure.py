"""Test suite for graph structure generation and validation.

Verifies that candidate company edges are correctly extracted from the BEA IO matrix
and that the resulting graph passes all DependencyGraph.validate() constraints.
"""

from __future__ import annotations

import pandas as pd
from marketsim.graph.structure import candidate_company_edges
from marketsim.graph.api import DependencyGraph, Node, Edge


def test_structure():
    io_matrix = pd.DataFrame(
        {
            "111": [0.05, 0.01],
            "211": [0.03, 0.04],
        },
        index=["111", "211"],
    )

    companies = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "bea_code": ["111", "211"],
            "name": ["Apple", "Microsoft"],
            "sector": ["Tech", "Tech"],
        }
    )

    edges_df = candidate_company_edges(io_matrix, companies, threshold=0.02)
    
    expected_columns = {"parent", "child", "parent_bea", "child_bea", "cost_share"}
    assert expected_columns.issubset(edges_df.columns)
    assert len(edges_df) == 1
    assert edges_df[["parent", "child"]].iloc[0].tolist() == ["AAPL", "MSFT"]
    assert edges_df["cost_share"].iloc[0] == 0.03

    edges_list = []
    for r in edges_df.itertuples(index=False):
        edges_list.append(
            Edge(
                parent=r.parent,
                child=r.child,
                w_direct=None,
                w_full=float(r.cost_share),
                t_stat_direct=None,
                t_stat_full=2.5,
                source="bea",
                justification="BEA input-output link",
            )
        )

    edges_list.append(
        Edge(
            parent="wti",
            child="AAPL",
            w_direct=0.5,
            w_full=0.5,
            t_stat_direct=3.0,
            t_stat_full=3.0,
            source="commodity",
            justification="Test commodity link",
        )
    )
    edges_list.append(
        Edge(
            parent="sp500",
            child="AAPL",
            w_direct=0.4,
            w_full=0.4,
            t_stat_direct=4.0,
            t_stat_full=4.0,
            source="index",
            justification="Test market index link",
        )
    )

    nodes_dict = {
        "AAPL": Node(name="AAPL", kind="company", ticker="AAPL", bea_code="111", start_price=100.0),
        "MSFT": Node(name="MSFT", kind="company", ticker="MSFT", bea_code="211", start_price=150.0),
        "wti": Node(name="wti", kind="external"),
        "sp500": Node(name="sp500", kind="external"),
    }

    graph = DependencyGraph(nodes=nodes_dict, edges=edges_list)
    assert graph.validate() is graph