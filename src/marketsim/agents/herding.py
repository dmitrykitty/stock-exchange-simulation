"""Fixed directed acquaintance network with expiring, published trade directions."""

from __future__ import annotations

import numpy as np


class HerdingNetwork:
    def __init__(
        self, n_agents: int, neighbors: int, ttl: float, rng: np.random.Generator,
        n_assets: int = 1, follows: np.ndarray | None = None,
    ) -> None:
        """``follows[agent, asset]`` marks the assets an agent trades; only those neighbors count for an asset.

        With one asset and everyone following it this is the F4 network: the denominator is the full neighbor count.
        """
        self.ttl = ttl
        size = min(neighbors, max(0, n_agents - 1))
        self.neighbors = []
        for agent in range(n_agents):
            candidates = np.concatenate((np.arange(agent), np.arange(agent + 1, n_agents)))
            self.neighbors.append(rng.choice(candidates, size, replace=False))
        self._follows = np.ones((n_agents, n_assets), dtype=bool) if follows is None else np.asarray(follows, dtype=bool)
        self._directions = np.zeros((n_agents, n_assets), dtype=np.int8)
        self._published_at = np.full((n_agents, n_assets), -np.inf)

    def publish(self, agent: int, direction: int, now: float, asset: int = 0) -> None:
        """Publish only a direction; a neighbor never reads another agent's portfolio."""
        self._directions[agent, asset] = direction
        self._published_at[agent, asset] = now

    def mix(self, agent: int, own: float, now: float, h: float, asset: int = 0) -> tuple[float, float]:
        """Return (mean neighbor direction, final signal); expired entries contribute zero."""
        # Plain Python: numpy overhead dominated on a handful of neighbors; the integer sum is exact.
        follows, published, directions = self._follows, self._published_at, self._directions
        total = count = 0
        for p in self.neighbors[agent]:
            if follows[p, asset]:
                count += 1
                if now - published[p, asset] < self.ttl:
                    total += int(directions[p, asset])
        herd = total / count if count else 0.0
        return herd, (1 - h) * own + h * herd


def susceptibility(n_agents: int, h: float, followers: float, h_std: float, rng: np.random.Generator) -> np.ndarray:
    """Each investor's own herding strength: a share ``followers`` draws h from N(h, h_std) clipped to [0, 1], the
    rest gets 0. With followers = 1 and h_std = 0 every investor has exactly h and ``rng`` is not used."""
    if followers == 1 and h_std == 0:
        return np.full(n_agents, float(h))
    out = np.zeros(n_agents)
    n_followers = round(followers * n_agents)
    chosen = np.sort(rng.choice(n_agents, n_followers, replace=False))
    draws = rng.normal(h, h_std, n_followers) if h_std > 0 else np.full(n_followers, float(h))
    out[chosen] = np.clip(draws, 0.0, 1.0)
    return out
