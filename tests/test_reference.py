"""Tests for reference-path equations beyond the pinned graph contract."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketsim.graph import DependencyGraph, Edge, Node, load_graph
from marketsim.graph.reference import reference_path


def test_direct_reference_path_uses_external_weights_only():
    graph = load_graph("graph_chain")
    external = pd.DataFrame(
        {"wti": [100.0, 120.0], "sp500": [100.0, 100.0]},
        index=pd.Index([0, 1], name="session"),
    )

    result = reference_path(graph, external, mode="direct")

    oil_change = np.log(1.2)
    expected_log_changes = np.array([0.516, 0.430, -0.043]) * oil_change
    assert result.columns.tolist() == ["COP", "VLO", "DAL"]
    assert np.allclose(np.log(result.iloc[-1].to_numpy() / 100.0), expected_log_changes)


def test_reference_path_solves_stable_company_feedback():
    graph = DependencyGraph(
        nodes={
            "wti": Node("wti", "external"),
            "A": Node("A", "company", "A", start_price=100.0),
            "B": Node("B", "company", "B", start_price=100.0),
        },
        edges=[
            Edge("wti", "A", 0.5, 0.5, None, None, "commodity", "Oil input"),
            Edge("wti", "B", 0.1, 0.1, None, None, "commodity", "Oil input"),
            Edge("A", "B", None, 0.2, None, None, "bea", "Supplier link"),
            Edge("B", "A", None, 0.1, None, None, "bea", "Supplier link"),
        ],
    )
    external = pd.DataFrame(
        {"wti": [100.0, 110.0]},
        index=pd.Index([0, 1], name="session"),
    )

    result = reference_path(graph, external)

    expected = np.linalg.solve(
        np.array([[1.0, -0.1], [-0.2, 1.0]]),
        np.array([0.5, 0.1]) * np.log(1.1),
    )
    assert np.allclose(np.log(result.iloc[-1].to_numpy() / 100.0), expected)


def test_reference_path_rejects_missing_external_nodes():
    graph = load_graph("graph_chain")
    external = pd.DataFrame(
        {"wti": [100.0]},
        index=pd.Index([0], name="session"),
    )

    with pytest.raises(ValueError, match="sp500"):
        reference_path(graph, external)
