# `marketsim.graph`: dependency graph (contract K2)

**Owner:** D2. What to do and how: `docs/plans/phases/F2_dependency_graph.md` (Russian).

The graph is the agents' knowledge of what depends on what. An edge `parent → child` with weight $w$ means: the parent moves +1% → the child's fair valuation moves $+w$%. Valuer agents update their valuations along it when they see the parents' prices move.

## The main rule

The graph format and the function signatures are a contract: the simulation (S) and the metrics (D1) read them. They do not change without the procedure in `docs/model/contracts.md`. Extend the implementations freely.

## Functions

| Function | Status | What it does | Why | Used by |
|---|---|---|---|---|
| `load_graph(name)` | ready | reads `data/graph/<name>/` (or `data/fixtures/<name>/`) and validates it | one way to get a graph for the simulation, metrics and tests | S, D1, tests |
| `save_graph(graph, dir)` | ready | validates and writes `nodes.csv`, `edges.csv`, `meta.json` | graphs are always saved in the right format | D2 |
| `DependencyGraph.validate()` | ready | required fields, `w_direct` only from external parents, justifications, spectral radius < 1 | graph errors show up immediately, not as a strange simulation | `load_graph`, `save_graph` |
| `DependencyGraph.parents(child, mode)` | ready | parents and weights: `"direct"` for myopic valuers, `"full"` for network valuers | agents take their weights from here | S |
| `DependencyGraph.company_weight_matrix(mode)`, `spectral_radius(mode)` | ready | company-to-company weight matrix | stability check | `validate`, D2 |
| `structure.candidate_company_edges(io_matrix, companies, threshold)` | **D2 implements** | supplier → customer candidates from BEA cost shares | the graph links only economically meaningful pairs, not everything that correlates | D2 → `estimate_weights` |
| `weights.estimate_weights(returns, edges, market, start, end)` | **D2 implements** | regressions: `direct` and `full` weights with t-statistics | the weights are the agents' "experience": how much a company moves with its parent | D2 → `save_graph` → S |
| `reference.reference_path(graph, external, mode)` | **D2 implements** | reference path: instant and exact reaction of everyone | the ruler for metrics: transmission, lag, overshoot, spurious links | D1 (metrics), S (run log), dashboard |

Tests: `tests/contracts/test_graph_contract.py` (the reference-path test is skipped until the function exists, then it checks the formula on the chain).
