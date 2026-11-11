"""Test suite for graph structure generation, BEA candidate review, and real_v1 validation.

Verifies that candidate company edges are correctly extracted from the BEA IO matrix,
explicit commodity/index/manual edges and 54-edge BEA classifications are consistent,
and the saved real_v1 graph passes all DependencyGraph.validate() constraints.
"""

from __future__ import annotations

import pandas as pd
import pytest

from marketsim.data import api as data_api
from marketsim.graph.api import DependencyGraph, Edge, Node, load_graph
from marketsim.graph.structure import (
    COMMODITY_EDGES,
    EXTERNAL_NODES,
    build_real_v1_graph,
    candidate_company_edges,
    commodity_edges,
    index_edges,
    manual_company_edges,
    review_bea_candidate_edges,
)


def _load_or_skip(loader, *args, **kwargs):
    # CI has no processed data; these tests run wherever data/processed/ exists.
    try:
        return loader(*args, **kwargs)
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


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


def test_explicit_commodity_index_and_manual_edges():
    companies = data_api.load_companies()
    comm = commodity_edges()
    idx = index_edges(companies)
    man = manual_company_edges()

    assert len(comm) == len(COMMODITY_EDGES) == 15
    assert set(comm["source"]) == {"commodity"}
    assert set(comm["parent"]) == {"wti", "henry_hub", "copper", "gold"}
    assert comm["justification"].str.strip().ne("").all()

    assert len(idx) == 18
    assert set(idx["parent"]) == {"sp500"}
    assert idx["child"].tolist() == companies["ticker"].tolist()

    assert len(man) == 4
    assert set(man["parent"]) == {"NVDA", "AMD"}
    assert set(man["child"]) == {"GOOGL", "META"}


def test_review_54_bea_candidate_edges():
    io_matrix = _load_or_skip(data_api.load_io_matrix, 2023)
    companies = data_api.load_companies()
    review = review_bea_candidate_edges(io_matrix, companies, threshold=0.02)

    assert len(review) == 54
    assert set(review["decision"]) == {"keep", "drop", "questionable"}
    assert review["justification"].str.strip().ne("").all()

    # All intra-industry bilateral pairs (COP<->EOG, VLO<->MPC, NEE<->DUK, FCX<->NEM, NUE<->AA, NVDA<->AMD, GOOGL<->META) are dropped
    same_ind = review[review["parent_bea"] == review["child_bea"]]
    assert len(same_ind) == 14
    assert (same_ind["decision"] == "drop").all()
    assert (~same_ind["included"]).all()

    # 10 BEA edges are included in real_v1: 211->324 (4), 324->481 (4), 324->484 (2); 22->331 (4) is excluded
    assert int(review["included"].sum()) == 10


def test_real_v1_graph_loads_and_matches_builder():
    real_v1 = load_graph("real_v1")
    built = _load_or_skip(build_real_v1_graph, computed_on=real_v1.meta.get("computed_on"))

    assert real_v1.externals() == list(EXTERNAL_NODES)
    assert len(real_v1.companies()) == 18
    assert len(real_v1.edges) == 47
    assert real_v1.spectral_radius("full") < 1.0
    assert real_v1.spectral_radius("direct") < 1.0
    assert real_v1.nodes.keys() == built.nodes.keys()
    for name, node in real_v1.nodes.items():
        other = built.nodes[name]
        assert (node.kind, node.ticker, node.bea_code) == (other.kind, other.ticker, other.bea_code)
    # Re-downloaded prices differ in the last digits, so numbers are compared with a tolerance.
    assert [(e.parent, e.child, e.source) for e in real_v1.edges] == [(e.parent, e.child, e.source) for e in built.edges]
    for saved, rebuilt in zip(real_v1.edges, built.edges):
        for field in ("w_direct", "w_full"):
            assert getattr(saved, field) == pytest.approx(getattr(rebuilt, field), abs=1e-4)
        for field in ("t_stat_direct", "t_stat_full"):
            assert getattr(saved, field) == pytest.approx(getattr(rebuilt, field), abs=1e-2)

    raw_prices = _load_or_skip(data_api.load_stock_prices, adjusted=False).loc["2024-01-02"]
    for ticker in real_v1.companies():
        assert real_v1.nodes[ticker].start_price == pytest.approx(float(raw_prices[ticker]), abs=1e-3)
