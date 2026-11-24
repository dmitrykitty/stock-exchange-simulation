"""Validation against the real data 2024–2025 (F8, owner: D1).

- ``reality``: the real data aligned with the simulated sessions, simulated runs in the same shape, random
  control graphs;
- ``checks``: the checks (graph out of sample, event study, stylised facts, next-day predictability, the graph
  rebuilt from simulated prices, bands of many runs, correlation structure);
- ``run``: ``python -m marketsim.validation.run prepare|checks --root runs/main``.
"""
