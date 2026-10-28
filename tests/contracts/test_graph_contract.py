"""Contract K2: graph format, validation rules, and the reference path formula."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from marketsim.data import api
from marketsim.graph import Edge, GraphError, load_graph, save_graph
from marketsim.graph.reference import reference_path
from tests.conftest import skip_if_not_implemented


@pytest.fixture
def chain():
    return load_graph("graph_chain")


def test_fixture_graph_loads(chain):
    assert chain.externals() == ["wti", "sp500"]
    assert chain.companies() == ["COP", "VLO", "DAL"]
    assert chain.parents("DAL", "direct") == pytest.approx({"wti": -0.043, "sp500": 1.274})
    assert chain.parents("DAL", "full") == pytest.approx({"wti": -0.139, "sp500": 1.140, "VLO": 0.222})
    assert chain.spectral_radius("full") < 1


def test_save_load_roundtrip(chain, tmp_path):
    back = load_graph(save_graph(chain, tmp_path / "g"))
    assert back.nodes == chain.nodes and back.edges == chain.edges


def _with_edge(graph, edge):
    return dataclasses.replace(graph, edges=[*graph.edges, edge])


def test_direct_weight_from_a_company_is_rejected(chain):
    bad = _with_edge(chain, Edge("COP", "DAL", 0.1, 0.1, None, None, "manual", "test"))
    with pytest.raises(GraphError):
        bad.validate()


def test_external_nodes_cannot_have_parents(chain):
    bad = _with_edge(chain, Edge("COP", "wti", None, 0.1, None, None, "manual", "test"))
    with pytest.raises(GraphError):
        bad.validate()


def test_justification_is_required(chain):
    bad = _with_edge(chain, Edge("COP", "DAL", None, 0.1, None, None, "manual", "  "))
    with pytest.raises(GraphError):
        bad.validate()


def test_runaway_feedback_is_rejected(chain):
    bad = _with_edge(chain, Edge("DAL", "COP", None, 9.0, None, None, "manual", "cycle"))
    bad = _with_edge(bad, Edge("COP", "DAL", None, 9.0, None, None, "manual", "cycle"))
    with pytest.raises(GraphError):
        bad.validate()


@skip_if_not_implemented
def test_reference_path_follows_the_formula(chain):
    """Oil +20% at session 5 (sp500 flat): the worked example of docs/model/model_description.md, 4.5."""
    ext = api.load_external("oil_shock")
    ref = reference_path(chain, ext, mode="full")
    assert list(ref.columns) == ["COP", "VLO", "DAL"] and ref.index.name == "session"
    assert np.allclose(ref.iloc[0].to_numpy(), [100.0, 100.0, 100.0])
    x = math.log(1.2)
    cop = 0.516 * x
    vlo = 0.701 * cop + 0.069 * x
    dal = 0.222 * vlo - 0.139 * x
    assert np.allclose(np.log(ref.iloc[-1].to_numpy() / 100.0), [cop, vlo, dal])
    assert np.allclose(ref.iloc[4].to_numpy(), [100.0, 100.0, 100.0])  # before the shock nothing moves
