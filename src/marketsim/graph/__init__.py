"""Dependency graph (contract K2, owner: D2)."""

from pathlib import Path
from marketsim.graph.api import DependencyGraph, Edge, GraphError, Node, load_graph, save_graph

__all__ = ["DependencyGraph", "Edge", "GraphError", "Node", "load_graph", "save_graph", "reference_path"]


def reference_path() -> Path:
    """Returns the path to the reference graph directory (owner: D2)."""
    path = Path("data/graph/reference")
    path.mkdir(parents=True, exist_ok=True)
    return path