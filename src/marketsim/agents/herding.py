"""Fixed directed acquaintance network with expiring, published trade directions."""

from __future__ import annotations

import numpy as np


class HerdingNetwork:
    def __init__(self, n_agents: int, neighbors: int, ttl: float, rng: np.random.Generator) -> None:
        self.ttl = ttl
        size = min(neighbors, max(0, n_agents - 1))
        self.neighbors = []
        for agent in range(n_agents):
            candidates = np.concatenate((np.arange(agent), np.arange(agent + 1, n_agents)))
            self.neighbors.append(rng.choice(candidates, size, replace=False))
        self._directions = np.zeros(n_agents, dtype=np.int8)
        self._published_at = np.full(n_agents, -np.inf)

    def publish(self, agent: int, direction: int, now: float) -> None:
        """Publish only a direction; a neighbor never reads another agent's portfolio."""
        self._directions[agent] = direction
        self._published_at[agent] = now

    def mix(self, agent: int, own: float, now: float, h: float) -> tuple[float, float]:
        """Return (mean neighbor direction, final signal); expired entries contribute zero."""
        peers = self.neighbors[agent]
        if len(peers):
            fresh = now - self._published_at[peers] < self.ttl
            herd = float(np.sum(self._directions[peers] * fresh) / len(peers))
        else:
            herd = 0.0
        return herd, (1 - h) * own + h * herd
