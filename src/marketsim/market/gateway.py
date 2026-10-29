"""Gateway with portfolios (owner: O). Rules are in the ``MarketGateway`` docstring (market/api.py).

Bookkeeping per agent: available and reserved cash (cents), available and reserved shares per asset.
Every resting order keeps its own reservation: ``remaining * limit`` cents for a buy, ``remaining`` shares
for a sell. Trades settle both sides; the unused part of a buy reservation is released at once.

Orders rejected by the gateway itself (REJECTED_FUNDS) never reach the exchange and get ``order_id = 0``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from marketsim.market.api import (
    Exchange,
    MarketDataView,
    PortfolioView,
    Report,
    Side,
    Status,
    TimeInForce,
    empty_fills,
)

_BUY, _SELL = int(Side.BUY), int(Side.SELL)
_VALID_TIF = frozenset(int(t) for t in TimeInForce)


@dataclass
class _OpenOrder:
    agent: int
    asset: int
    side: int
    limit: int
    remaining: int


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
        self._cash_total = int(cash.sum())
        self._shares_total = shares.sum(axis=0)
        self._cash_avail = cash.copy()
        self._cash_resv = np.zeros_like(cash)
        self._shares_avail = shares.copy()
        self._shares_resv = np.zeros_like(shares)
        self._open: dict[int, _OpenOrder] = {}
        self._pending_fills: list[np.ndarray] = []

    @property
    def exchange(self) -> Exchange:
        return self._exchange

    @property
    def n_agents(self) -> int:
        return int(self._cash_avail.shape[0])

    def _check_agent(self, agent_id: int) -> None:
        if not 0 <= agent_id < self.n_agents:
            raise ValueError(f"agent {agent_id} out of range")

    def place(self, agent_id: int, asset: int, side: int, qty: int, limit: int, tif: int, now: float) -> Report:
        agent_id, asset, side, qty, limit, tif = (int(v) for v in (agent_id, asset, side, qty, limit, tif))
        self._check_agent(agent_id)
        well_formed = (0 <= asset < self._exchange.n_assets and side in (_BUY, _SELL)
                       and qty > 0 and limit > 0 and tif in _VALID_TIF)

        if well_formed:  # reserve first; malformed orders go straight to the exchange, which rejects them
            if side == _BUY:
                cost = qty * limit
                if cost > self._cash_avail[agent_id]:
                    return Report(0, Status.REJECTED_FUNDS, 0, 0, math.nan)
                self._cash_avail[agent_id] -= cost
                self._cash_resv[agent_id] += cost
            else:
                if qty > self._shares_avail[agent_id, asset]:
                    return Report(0, Status.REJECTED_FUNDS, 0, 0, math.nan)
                self._shares_avail[agent_id, asset] -= qty
                self._shares_resv[agent_id, asset] += qty

        report, fills = self._exchange.submit(asset, agent_id, side, qty, limit, tif)
        for f in fills:
            self._settle(agent_id, asset, side, limit, f)
        if len(fills):
            self._pending_fills.append(fills)

        if not well_formed:
            return report
        if report.resting_qty > 0:
            self._open[report.order_id] = _OpenOrder(agent_id, asset, side, limit, report.resting_qty)
        else:
            self._release(agent_id, asset, side, limit, qty - report.filled_qty)
        return report

    def _settle(self, taker: int, asset: int, taker_side: int, taker_limit: int, fill) -> None:
        q, price = int(fill["qty"]), int(fill["price"])
        maker_order = self._open[int(fill["maker_order"])]
        maker = maker_order.agent
        if taker_side == _BUY:
            self._cash_resv[taker] -= taker_limit * q
            self._cash_avail[taker] += (taker_limit - price) * q
            self._shares_avail[taker, asset] += q
            self._shares_resv[maker, asset] -= q
            self._cash_avail[maker] += price * q
        else:
            self._shares_resv[taker, asset] -= q
            self._cash_avail[taker] += price * q
            self._cash_resv[maker] -= maker_order.limit * q
            self._cash_avail[maker] += (maker_order.limit - price) * q
            self._shares_avail[maker, asset] += q
        maker_order.remaining -= q
        if maker_order.remaining == 0:
            del self._open[int(fill["maker_order"])]

    def _release(self, agent: int, asset: int, side: int, limit: int, qty: int) -> None:
        if qty <= 0:
            return
        if side == _BUY:
            self._cash_resv[agent] -= qty * limit
            self._cash_avail[agent] += qty * limit
        else:
            self._shares_resv[agent, asset] -= qty
            self._shares_avail[agent, asset] += qty

    def _withdraw(self, order_id: int) -> int:
        order = self._open.pop(order_id)
        remaining = self._exchange.cancel(order_id)
        if remaining != order.remaining:
            raise AssertionError(f"order {order_id}: exchange has {remaining} left, gateway expected {order.remaining}")
        self._release(order.agent, order.asset, order.side, order.limit, remaining)
        return remaining

    def cancel(self, agent_id: int, order_id: int, now: float) -> int:
        agent_id, order_id = int(agent_id), int(order_id)
        self._check_agent(agent_id)
        order = self._open.get(order_id)
        if order is None:
            return 0
        if order.agent != agent_id:
            raise ValueError(f"agent {agent_id} cannot cancel order {order_id} of agent {order.agent}")
        return self._withdraw(order_id)

    def expire(self, order_id: int, now: float) -> int:
        order_id = int(order_id)
        if order_id not in self._open:
            return 0
        return self._withdraw(order_id)

    def portfolio(self, agent_id: int) -> PortfolioView:
        agent_id = int(agent_id)
        self._check_agent(agent_id)
        return PortfolioView(
            int(self._cash_avail[agent_id]), int(self._cash_resv[agent_id]),
            self._shares_avail[agent_id].copy(), self._shares_resv[agent_id].copy(),
        )

    def market(self) -> MarketDataView:
        return self._exchange

    def drain_fills(self) -> np.ndarray:
        fills = np.concatenate(self._pending_fills) if self._pending_fills else empty_fills()
        self._pending_fills = []
        return fills

    def check_invariants(self) -> None:
        if int(self._cash_avail.sum() + self._cash_resv.sum()) != self._cash_total:
            raise AssertionError("total cash changed")
        if not (self._shares_avail.sum(axis=0) + self._shares_resv.sum(axis=0) == self._shares_total).all():
            raise AssertionError("total shares changed")
        for name, arr in (("cash_available", self._cash_avail), ("cash_reserved", self._cash_resv),
                          ("shares_available", self._shares_avail), ("shares_reserved", self._shares_resv)):
            if (arr < 0).any():
                raise AssertionError(f"negative {name}")
        cash_resv = np.zeros_like(self._cash_resv)
        shares_resv = np.zeros_like(self._shares_resv)
        for o in self._open.values():
            if o.side == _BUY:
                cash_resv[o.agent] += o.remaining * o.limit
            else:
                shares_resv[o.agent, o.asset] += o.remaining
        if not (cash_resv == self._cash_resv).all() or not (shares_resv == self._shares_resv).all():
            raise AssertionError("reservations do not match open orders")
