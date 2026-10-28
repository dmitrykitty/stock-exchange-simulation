"""Gateway with portfolios (owner: O). Rules are in the ``MarketGateway`` docstring (market/api.py).

Skeleton: the constructor validates its input, other methods raise NotImplementedError.
Implementation plan: docs/plans/phases/F3_exchange_engine.md.
"""

from __future__ import annotations

import numpy as np

from marketsim.market.api import Exchange, MarketDataView, PortfolioView, Report


class Gateway:
    def __init__(self, exchange: Exchange, cash: np.ndarray, shares: np.ndarray) -> None:
        """
        cash   -- shape (n_agents,), int64: initial cash of every agent, in cents;
        shares -- shape (n_agents, n_assets), int64: initial shares.
        """
        cash = np.asarray(cash, dtype=np.int64)
        shares = np.asarray(shares, dtype=np.int64)
        if cash.ndim != 1 or shares.ndim != 2 or shares.shape[0] != cash.shape[0]:
            raise ValueError("expected cash of shape (n_agents,) and shares of shape (n_agents, n_assets)")
        if shares.shape[1] != exchange.n_assets:
            raise ValueError("shares.shape[1] must equal exchange.n_assets")
        if (cash < 0).any() or (shares < 0).any():
            raise ValueError("initial resources must be non-negative")
        self._exchange = exchange
        self._cash0 = cash.copy()
        self._shares0 = shares.copy()

    @property
    def exchange(self) -> Exchange:
        return self._exchange

    @property
    def n_agents(self) -> int:
        return int(self._cash0.shape[0])

    def place(self, agent_id: int, asset: int, side: int, qty: int, limit: int, tif: int, now: float) -> Report:
        raise NotImplementedError("Gateway.place (owner: O, see docs/plans/phases/F3_exchange_engine.md)")

    def cancel(self, agent_id: int, order_id: int, now: float) -> int:
        raise NotImplementedError("Gateway.cancel (owner: O)")

    def expire(self, order_id: int, now: float) -> int:
        raise NotImplementedError("Gateway.expire (owner: O)")

    def portfolio(self, agent_id: int) -> PortfolioView:
        raise NotImplementedError("Gateway.portfolio (owner: O)")

    def market(self) -> MarketDataView:
        return self._exchange

    def drain_fills(self) -> np.ndarray:
        raise NotImplementedError("Gateway.drain_fills (owner: O)")

    def check_invariants(self) -> None:
        raise NotImplementedError("Gateway.check_invariants (owner: O)")
