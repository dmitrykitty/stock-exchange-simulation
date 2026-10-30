"""Tests for OLS weight estimation and parameter recovery."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketsim.graph.api import DependencyGraph, Edge, Node
from marketsim.graph.weights import estimate_weights, ols


def test_ols_returns_coefficients_t_statistics_and_r_squared():
    rng = np.random.default_rng(7)
    X = pd.DataFrame({"parent": rng.normal(size=100)})
    y = 0.4 + 1.25 * X["parent"] + rng.normal(0, 0.1, len(X))

    coefficients, t_statistics, r_squared = ols(y, X)

    assert coefficients["parent"] == pytest.approx(1.25, abs=0.03)
    assert t_statistics["parent"] > 10
    assert r_squared > 0.98


def test_weights_recover_direct_and_full_coefficients_and_validate_graph():
    rng = np.random.default_rng(42)
    n_days = 400
    dates = pd.date_range("2021-01-01", periods=n_days, freq="B")

    r_mkt = rng.normal(0, 0.01, n_days)
    r_oil = rng.normal(0, 0.02, n_days)
    r_parent = rng.normal(0, 0.015, n_days)
    external_factors = np.column_stack((np.ones(n_days), r_mkt, r_oil))
    r_parent -= external_factors @ np.linalg.lstsq(external_factors, r_parent, rcond=None)[0]
    r_parent *= 0.015 / r_parent.std()

    true_w_oil = 0.5
    true_w_parent = 0.3
    true_w_mkt = 0.2
    r_child = (
        0.001
        + true_w_oil * r_oil
        + true_w_parent * r_parent
        + true_w_mkt * r_mkt
        + rng.normal(0, 0.001, n_days)
    )

    returns = pd.DataFrame(
        {
            "sp500": r_mkt,
            "wti": r_oil,
            "PARENT_COMP": r_parent,
            "CHILD_COMP": r_child,
        },
        index=dates,
    )
    edges = pd.DataFrame(
        [
            {"parent": "wti", "child": "CHILD_COMP", "source": "commodity"},
            {"parent": "PARENT_COMP", "child": "CHILD_COMP", "source": "bea"},
        ]
    )

    weights_df = estimate_weights(
        returns,
        edges,
        market="sp500",
        start="2021-01-01",
        end="2023-12-31",
    )

    def weight_row(parent: str) -> pd.Series:
        rows = weights_df[
            (weights_df["parent"] == parent) & (weights_df["child"] == "CHILD_COMP")
        ]
        assert len(rows) == 1
        return rows.iloc[0]

    wti_row = weight_row("wti")
    market_row = weight_row("sp500")
    parent_row = weight_row("PARENT_COMP")
    tolerance = 0.02

    assert abs(wti_row["w_direct"] - true_w_oil) < tolerance
    assert abs(wti_row["w_full"] - true_w_oil) < tolerance
    assert abs(market_row["w_direct"] - true_w_mkt) < tolerance
    assert abs(market_row["w_full"] - true_w_mkt) < tolerance
    assert pd.isna(parent_row["w_direct"])
    assert abs(parent_row["w_full"] - true_w_parent) < tolerance

    nodes = {
        "wti": Node(name="wti", kind="external"),
        "sp500": Node(name="sp500", kind="external"),
        "PARENT_COMP": Node(
            name="PARENT_COMP", kind="company", ticker="PARENT_COMP", start_price=100.0
        ),
        "CHILD_COMP": Node(
            name="CHILD_COMP", kind="company", ticker="CHILD_COMP", start_price=100.0
        ),
    }

    def optional_float(value: object) -> float | None:
        return None if pd.isna(value) else float(value)

    graph_edges = [
        Edge(
            parent=row.parent,
            child=row.child,
            w_direct=optional_float(row.w_direct),
            w_full=optional_float(row.w_full),
            t_stat_direct=optional_float(row.t_stat_direct),
            t_stat_full=optional_float(row.t_stat_full),
            source=row.source,
            justification="Synthetic return regression",
        )
        for row in weights_df.itertuples(index=False)
    ]
    graph = DependencyGraph(nodes=nodes, edges=graph_edges)
    assert graph.validate() is graph


def test_weights_without_edges_keep_output_columns():
    returns = pd.DataFrame({"sp500": [0.01, -0.02]}, index=pd.date_range("2021-01-04", periods=2, freq="B"))
    edges = pd.DataFrame(columns=["parent", "child", "source"])

    weights_df = estimate_weights(returns, edges)

    assert weights_df.empty
    assert list(weights_df.columns) == [
        "parent", "child", "w_direct", "w_full", "t_stat_direct", "t_stat_full", "source",
    ]
