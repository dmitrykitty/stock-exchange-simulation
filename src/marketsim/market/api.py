"""Contract K1: the exchange (order books + matching) and the gateway with portfolios.

This file is the **frozen interface** between the engine (owner O) and the simulation (owner S).
Change it only following docs/model/contracts.md (PR approved by both O and S).
Constant values mirror cpp/include/lob_core/types.hpp.

Units:
- price: integer number of ticks (1 tick = 0.01 USD), int64;
- quantity: integer number of shares, int64;
- money inside the gateway: integer cents, int64 (1 tick x 1 share = 1 cent).

Semantics (short version; full version in docs/model/contracts.md, section K1):
- price-time priority: best price first, earlier order first at the same price;
- trade price is the price of the resting (maker) order;
- a market order is an IOC order whose ``limit`` is the protection price;
- self-trade prevention: the incoming order is rejected **entirely** (REJECTED_SELF_TRADE) if the same
  owner has an order on the opposite side at a price the incoming order could match; the check happens
  before any state change;
- ``order_id`` is assigned by the exchange: 1, 2, 3, ... to every submitted order, rejected ones included;
- trade ``seq``: 1, 2, 3, ... shared by all assets;
- the exchange has no clock: GTD behaves like GTC in the book, expiry is done by the gateway (``expire``);
- ``submit_batch`` gives exactly the same result as calling ``submit`` for each order in array order.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol, runtime_checkable

import numpy as np

CONTRACT_VERSION = 1

NO_PRICE: int = int(np.iinfo(np.int64).min)
"""Empty side of the book / no trade yet."""


class Side(IntEnum):
    BUY = 1
    SELL = -1


class TimeInForce(IntEnum):
    GTC = 0
    IOC = 1
    GTD = 2


class Status(IntEnum):
    RESTING = 0
    FILLED = 1
    PARTIAL_RESTING = 2
    PARTIAL_CANCELLED = 3
    CANCELLED_IOC = 4
    REJECTED_SELF_TRADE = 10
    REJECTED_FUNDS = 11
    REJECTED_INVALID = 12


ORDER_DTYPE = np.dtype([
    ("asset", "u2"), ("owner", "u4"), ("side", "i1"), ("qty", "i8"), ("limit", "i8"), ("tif", "u1"),
])
"""One order for batch submission (``submit_batch``)."""

FILL_DTYPE = np.dtype([
    ("seq", "u8"), ("asset", "u2"), ("price", "i8"), ("qty", "i8"),
    ("taker_order", "u8"), ("maker_order", "u8"),
    ("taker_owner", "u4"), ("maker_owner", "u4"), ("taker_side", "i1"),
])
"""One trade."""

REPORT_DTYPE = np.dtype([
    ("order_id", "u8"), ("status", "u1"), ("filled_qty", "i8"), ("resting_qty", "i8"), ("avg_price", "f8"),
])
"""Order report in batch mode."""

DEPTH_DTYPE = np.dtype([("bid", "i8"), ("bid_qty", "i8"), ("ask", "i8"), ("ask_qty", "i8")])
"""One depth level. Missing level: NO_PRICE and 0."""


@dataclass(frozen=True)
class Report:
    """Result of one submitted order."""

    order_id: int
    status: Status
    filled_qty: int
    resting_qty: int
    avg_price: float  # average execution price in ticks; nan if nothing was filled


@dataclass(frozen=True)
class Top:
    bid: int
    bid_qty: int
    ask: int
    ask_qty: int


@dataclass(frozen=True)
class PortfolioView:
    """Resources of one agent. Cash in cents, shares per asset."""

    cash_available: int
    cash_reserved: int
    shares_available: np.ndarray  # shape (n_assets,), int64
    shares_reserved: np.ndarray   # shape (n_assets,), int64


@runtime_checkable
class MarketDataView(Protocol):
    """Public market data: what an agent may see (no other agents' portfolios)."""

    @property
    def n_assets(self) -> int: ...

    def top(self, asset: int) -> Top: ...

    def depth(self, asset: int, levels: int) -> np.ndarray:
        """DEPTH_DTYPE array of exactly ``levels`` elements, best prices first."""
        ...

    def last_trade(self, asset: int) -> tuple[int, int]:
        """(seq, price) of the last trade in the asset; (0, NO_PRICE) if there was none."""
        ...


@runtime_checkable
class Exchange(MarketDataView, Protocol):
    """Order books of all assets and matching. Implementations: PyExchange (reference) and CppExchange."""

    def submit(self, asset: int, owner: int, side: int, qty: int, limit: int, tif: int) -> tuple[Report, np.ndarray]:
        """Submit one order. Returns (report, FILL_DTYPE array with the trades of this order)."""
        ...

    def submit_batch(self, orders: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Submit an ORDER_DTYPE array. Returns (REPORT_DTYPE array, FILL_DTYPE array)."""
        ...

    def cancel(self, order_id: int) -> int:
        """Cancel an order. Returns the cancelled remaining quantity; 0 if the order is gone."""
        ...


@runtime_checkable
class MarketGateway(Protocol):
    """Gateway between agents and the exchange: checks and reserves resources, settles trades.

    Agents (owner S) talk to the gateway only. Rules:
    - a buy reserves ``qty * limit`` cents, a sell reserves ``qty`` shares; if resources are missing the
      order gets REJECTED_FUNDS and the book is not touched;
    - when an order executes below its limit, the unused part of the reservation is released immediately;
    - every trade updates the portfolios of **both** sides (taker and maker);
    - total cash and total shares of every asset across all agents are constant (``check_invariants``).
    """

    @property
    def exchange(self) -> Exchange: ...

    def place(self, agent_id: int, asset: int, side: int, qty: int, limit: int, tif: int, now: float) -> Report: ...

    def cancel(self, agent_id: int, order_id: int, now: float) -> int:
        """Cancel one's own order (ValueError for someone else's). Returns the cancelled remaining quantity."""
        ...

    def expire(self, order_id: int, now: float) -> int:
        """Called by the scheduler when a GTD order expires. If the order is gone: returns 0, changes nothing."""
        ...

    def portfolio(self, agent_id: int) -> PortfolioView: ...

    def market(self) -> MarketDataView: ...

    def drain_fills(self) -> np.ndarray:
        """All trades (FILL_DTYPE) since the previous call. Used for the run log (K3)."""
        ...

    def check_invariants(self) -> None:
        """AssertionError if cash/share conservation is violated or any resource is negative."""
        ...


def empty_fills() -> np.ndarray:
    return np.zeros(0, dtype=FILL_DTYPE)
