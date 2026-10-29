"""Fixed investor groups and exact integer allocations, independent of concentration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from marketsim.sim.settings import AgentSettings


@dataclass
class Population:
    kinds: list[str]
    funds: np.ndarray
    cash: np.ndarray
    shares: np.ndarray


def _allocate(total: int, indices: np.ndarray, out: np.ndarray) -> None:
    amount, remainder = divmod(total, len(indices))
    out[indices] = amount
    out[indices[:remainder]] += 1


def make_population(settings: AgentSettings, rng: np.random.Generator) -> Population:
    """Allocate C of both investor resources to fixed funds; makers are separate."""
    s = settings
    kinds = ["valuer"] * s.valuers + ["trend"] * s.trend + ["noise"] * s.noise
    n = len(kinds)
    kinds += ["market_maker"] * s.market_makers_per_asset
    funds = np.zeros(len(kinds), dtype=bool)
    # Random group membership is fixed by the run seed, never by C.
    funds[rng.choice(n, s.n_funds, replace=False)] = True
    fund_indices = np.flatnonzero(funds)
    retail_indices = np.flatnonzero(~funds[:n])
    cash = np.zeros(len(kinds), dtype=np.int64)
    shares = np.zeros(len(kinds), dtype=np.int64)
    for total, out in ((s.total_cash_cents, cash), (s.total_shares, shares)):
        fund_total = int(Decimal(str(s.concentration)) * total)
        _allocate(fund_total, fund_indices, out)
        _allocate(total - fund_total, retail_indices, out)
    cash[n:] = s.market_maker_cash_cents
    shares[n:] = s.market_maker_shares
    return Population(kinds, funds, cash, shares.reshape(-1, 1))
