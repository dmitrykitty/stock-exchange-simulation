"""Checks for the committed empirical dependency graph."""

from marketsim.graph import load_graph


def test_real_v1_has_required_start_prices_justifications_and_stability():
    graph = load_graph("real_v1")

    assert len(graph.companies()) == 18
    assert graph.meta["start_price_date"] == "2024-01-02"
    assert all(
        graph.nodes[name].start_price is not None and graph.nodes[name].start_price > 0
        for name in graph.companies()
    )
    assert all(edge.justification.strip() for edge in graph.edges)
    assert graph.spectral_radius("full") < 1
