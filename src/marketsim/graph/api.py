"""Contract K2: the dependency graph that agents use to update their valuations.

On disk a graph is a directory with three files:

    nodes.csv   node, kind, ticker, bea_code, start_price
    edges.csv   parent, child, w_direct, w_full, t_stat_direct, t_stat_full, source, justification
    meta.json   free-form metadata (estimation period, number of days, ...)

Meaning of an edge ``parent -> child`` with weight ``w``: if the parent's price changes by 1%, the fair
valuation of the child changes by ``w`` %.

- ``kind``: "external" (commodity or market index, real data, not traded) or "company" (traded).
- ``w_direct``: weight used by *myopic* valuers; only edges whose parent is external may have it.
- ``w_full``: weight used by *network* valuers (parents: externals and companies).
- ``source``: "bea" (supplier -> customer from BEA input-output tables), "commodity", "index", "manual".
- every edge needs a written ``justification``.

Change the format only following docs/model/contracts.md.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from marketsim import paths

NODE_KINDS = ("external", "company")
EDGE_SOURCES = ("bea", "commodity", "index", "manual")
NODE_COLUMNS = ("node", "kind", "ticker", "bea_code", "start_price")
EDGE_COLUMNS = ("parent", "child", "w_direct", "w_full", "t_stat_direct", "t_stat_full", "source", "justification")


class GraphError(ValueError):
    """The graph does not follow contract K2."""


def _opt_float(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v == "":
        return None
    return float(v)


def _opt_str(v) -> str | None:
    if v is None or (isinstance(v, float) and math.isnan(v)) or str(v) == "":
        return None
    return str(v)


@dataclass(frozen=True)
class Node:
    name: str
    kind: str
    ticker: str | None = None
    bea_code: str | None = None
    start_price: float | None = None


@dataclass(frozen=True)
class Edge:
    parent: str
    child: str
    w_direct: float | None
    w_full: float | None
    t_stat_direct: float | None
    t_stat_full: float | None
    source: str
    justification: str

    def weight(self, mode: str) -> float | None:
        if mode == "direct":
            return self.w_direct
        if mode == "full":
            return self.w_full
        raise ValueError(f"mode must be 'direct' or 'full', got {mode!r}")


@dataclass
class DependencyGraph:
    nodes: dict[str, Node]
    edges: list[Edge]
    meta: dict = field(default_factory=dict)

    def externals(self) -> list[str]:
        return [n.name for n in self.nodes.values() if n.kind == "external"]

    def companies(self) -> list[str]:
        """Company nodes in file order; this order is the asset order on the exchange."""
        return [n.name for n in self.nodes.values() if n.kind == "company"]

    def parents(self, child: str, mode: str = "full") -> dict[str, float]:
        """Parents of ``child`` with their weights for the given mode ("direct" or "full")."""
        out = {}
        for e in self.edges:
            if e.child == child:
                w = e.weight(mode)
                if w is not None:
                    out[e.parent] = w
        return out

    def company_weight_matrix(self, mode: str = "full") -> tuple[list[str], np.ndarray]:
        """(companies, W) where W[i, j] is the weight of edge companies[j] -> companies[i]."""
        names = self.companies()
        pos = {n: i for i, n in enumerate(names)}
        w = np.zeros((len(names), len(names)))
        for e in self.edges:
            if e.parent in pos and e.child in pos:
                v = e.weight(mode)
                if v is not None:
                    w[pos[e.child], pos[e.parent]] = v
        return names, w

    def spectral_radius(self, mode: str = "full") -> float:
        """Largest |eigenvalue| of the company-to-company weight matrix; must be < 1 (no runaway feedback)."""
        _, w = self.company_weight_matrix(mode)
        if w.size == 0:
            return 0.0
        return float(np.max(np.abs(np.linalg.eigvals(w))))

    def validate(self) -> DependencyGraph:
        problems: list[str] = []
        for n in self.nodes.values():
            if n.kind not in NODE_KINDS:
                problems.append(f"node {n.name!r}: kind must be one of {NODE_KINDS}")
            if n.kind == "company":
                if not n.ticker:
                    problems.append(f"company {n.name!r}: ticker is required")
                if n.start_price is None or n.start_price <= 0:
                    problems.append(f"company {n.name!r}: start_price must be > 0")
        seen = set()
        for e in self.edges:
            key = (e.parent, e.child)
            if key in seen:
                problems.append(f"duplicate edge {e.parent} -> {e.child}")
            seen.add(key)
            if e.parent not in self.nodes or e.child not in self.nodes:
                problems.append(f"edge {e.parent} -> {e.child}: unknown node")
                continue
            if e.parent == e.child:
                problems.append(f"self-loop on {e.parent}")
            if self.nodes[e.child].kind != "company":
                problems.append(f"edge {e.parent} -> {e.child}: external nodes cannot have parents")
            if e.w_direct is None and e.w_full is None:
                problems.append(f"edge {e.parent} -> {e.child}: needs w_direct or w_full")
            if e.w_direct is not None and self.nodes[e.parent].kind != "external":
                problems.append(f"edge {e.parent} -> {e.child}: w_direct is only allowed for external parents")
            if e.source not in EDGE_SOURCES:
                problems.append(f"edge {e.parent} -> {e.child}: source must be one of {EDGE_SOURCES}")
            if not e.justification.strip():
                problems.append(f"edge {e.parent} -> {e.child}: justification is required")
        if not problems:
            for mode in ("direct", "full"):
                rho = self.spectral_radius(mode)
                if rho >= 1:
                    problems.append(f"spectral radius ({mode}) is {rho:.3f}, must be < 1")
        if problems:
            raise GraphError("; ".join(problems))
        return self


def resolve_graph_dir(name_or_path: str | Path) -> Path:
    """A path to a graph directory, or a graph name looked up in data/graph/ and then data/fixtures/."""
    p = Path(name_or_path)
    if p.is_dir():
        return p
    for base in (paths.graph_dir(), paths.fixtures_dir()):
        if (base / str(name_or_path)).is_dir():
            return base / str(name_or_path)
    raise FileNotFoundError(f"graph {name_or_path!r} not found in {paths.graph_dir()} or {paths.fixtures_dir()}")


def load_graph(name_or_path: str | Path) -> DependencyGraph:
    d = resolve_graph_dir(name_or_path)
    nodes_df = pd.read_csv(d / "nodes.csv", dtype={"node": str, "kind": str, "ticker": str, "bea_code": str})
    edges_df = pd.read_csv(d / "edges.csv", dtype={"parent": str, "child": str, "source": str, "justification": str})
    for cols, df, fname in ((NODE_COLUMNS, nodes_df, "nodes.csv"), (EDGE_COLUMNS, edges_df, "edges.csv")):
        if tuple(df.columns) != cols:
            raise GraphError(f"{fname}: columns must be exactly {cols}, got {tuple(df.columns)}")
    nodes = {}
    for r in nodes_df.itertuples(index=False):
        if r.node in nodes:
            raise GraphError(f"duplicate node {r.node!r}")
        nodes[r.node] = Node(r.node, r.kind, _opt_str(r.ticker), _opt_str(r.bea_code), _opt_float(r.start_price))
    edges = [
        Edge(r.parent, r.child, _opt_float(r.w_direct), _opt_float(r.w_full), _opt_float(r.t_stat_direct),
             _opt_float(r.t_stat_full), r.source, _opt_str(r.justification) or "")
        for r in edges_df.itertuples(index=False)
    ]
    meta_path = d / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return DependencyGraph(nodes, edges, meta).validate()


def save_graph(graph: DependencyGraph, directory: str | Path) -> Path:
    graph.validate()
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [(n.name, n.kind, n.ticker, n.bea_code, n.start_price) for n in graph.nodes.values()], columns=NODE_COLUMNS
    ).to_csv(d / "nodes.csv", index=False)
    pd.DataFrame(
        [(e.parent, e.child, e.w_direct, e.w_full, e.t_stat_direct, e.t_stat_full, e.source, e.justification)
         for e in graph.edges],
        columns=EDGE_COLUMNS,
    ).to_csv(d / "edges.csv", index=False)
    meta = dict(graph.meta)
    meta["spectral_radius_full"] = graph.spectral_radius("full")
    (d / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return d
