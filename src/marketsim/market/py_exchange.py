"""Reference exchange implementation in pure Python (owner: O).

Simple and slow, but it must be correct. It exists so that
1) the simulation can be written without waiting for the C++ engine;
2) the C++ engine can be checked with differential tests (same orders -> same trades).

Layout (mirrors the C++ engine, cpp/src/exchange.cpp):
- one book per asset, two sides per book;
- a side keeps sorted price levels, each level is a FIFO queue of resting orders;
- ``order_id -> (asset, side, price, node)`` index for cancellation;
- per side, ``owner -> {price: number of resting orders}`` for the self-trade check.

Semantics: docs/plans/phases/F3_exchange_engine.md, section 2.
"""

from __future__ import annotations

import bisect
import math
from collections import deque

import numpy as np

from marketsim.market.api import (
    DEPTH_DTYPE,
    FILL_DTYPE,
    NO_PRICE,
    REPORT_DTYPE,
    Report,
    Side,
    Status,
    TimeInForce,
    Top,
    empty_fills,
)

_BUY, _SELL, _IOC = int(Side.BUY), int(Side.SELL), int(TimeInForce.IOC)
_VALID_TIF = frozenset(int(t) for t in TimeInForce)


class _Resting:
    __slots__ = ("order_id", "owner", "remaining")

    def __init__(self, order_id: int, owner: int, remaining: int) -> None:
        self.order_id = order_id
        self.owner = owner
        self.remaining = remaining


class _BookSide:
    """One side of one order book."""

    def __init__(self, is_bid: bool) -> None:
        self.is_bid = is_bid
        self.prices: list[int] = []                      # ascending
        self.levels: dict[int, deque[_Resting]] = {}
        self.own: dict[int, dict[int, int]] = {}         # owner -> price -> resting orders count

    def best(self) -> int | None:
        if not self.prices:
            return None
        return self.prices[-1] if self.is_bid else self.prices[0]

    def prices_best_first(self) -> list[int]:
        return self.prices[::-1] if self.is_bid else self.prices

    def level_qty(self, price: int) -> int:
        return sum(o.remaining for o in self.levels[price])

    def add(self, price: int, node: _Resting) -> None:
        if price not in self.levels:
            bisect.insort(self.prices, price)
            self.levels[price] = deque()
        self.levels[price].append(node)
        counts = self.own.setdefault(node.owner, {})
        counts[price] = counts.get(price, 0) + 1

    def pop_front(self, price: int) -> None:
        node = self.levels[price].popleft()
        self._forget(node.owner, price)
        self._drop_level_if_empty(price)

    def remove(self, price: int, node: _Resting) -> None:
        self.levels[price].remove(node)
        self._forget(node.owner, price)
        self._drop_level_if_empty(price)

    def owner_would_match(self, owner: int, incoming_limit: int) -> bool:
        """True if ``owner`` rests on this side at a price an incoming opposite order at ``incoming_limit`` crosses."""
        counts = self.own.get(owner)
        if not counts:
            return False
        if self.is_bid:  # incoming sell crosses bids >= its limit
            return max(counts) >= incoming_limit
        return min(counts) <= incoming_limit  # incoming buy crosses asks <= its limit

    def _forget(self, owner: int, price: int) -> None:
        counts = self.own[owner]
        counts[price] -= 1
        if counts[price] == 0:
            del counts[price]
            if not counts:
                del self.own[owner]

    def _drop_level_if_empty(self, price: int) -> None:
        if not self.levels[price]:
            del self.levels[price]
            self.prices.pop(bisect.bisect_left(self.prices, price))


class _Book:
    def __init__(self) -> None:
        self.bids = _BookSide(is_bid=True)
        self.asks = _BookSide(is_bid=False)
        self.last_trade = (0, NO_PRICE)


class PyExchange:
    def __init__(self, n_assets: int) -> None:
        if n_assets <= 0:
            raise ValueError("n_assets must be positive")
        self._n_assets = n_assets
        self._books = [_Book() for _ in range(n_assets)]
        self._index: dict[int, tuple[_BookSide, int, _Resting]] = {}
        self._next_order_id = 1
        self._next_seq = 1

    @property
    def n_assets(self) -> int:
        return self._n_assets

    def _is_valid(self, asset: int, owner: int, side: int, qty: int, limit: int, tif: int) -> bool:
        return (0 <= asset < self._n_assets and owner >= 0 and side in (_BUY, _SELL)
                and qty > 0 and limit > 0 and tif in _VALID_TIF)

    def submit(self, asset: int, owner: int, side: int, qty: int, limit: int, tif: int) -> tuple[Report, np.ndarray]:
        asset, owner, side, qty, limit, tif = (int(v) for v in (asset, owner, side, qty, limit, tif))
        order_id = self._next_order_id
        self._next_order_id += 1

        if not self._is_valid(asset, owner, side, qty, limit, tif):
            return Report(order_id, Status.REJECTED_INVALID, 0, 0, math.nan), empty_fills()

        book = self._books[asset]
        same, opposite = (book.bids, book.asks) if side == _BUY else (book.asks, book.bids)
        if opposite.owner_would_match(owner, limit):
            return Report(order_id, Status.REJECTED_SELF_TRADE, 0, 0, math.nan), empty_fills()

        remaining, notional, fills = qty, 0, []
        while remaining > 0:
            price = opposite.best()
            if price is None or (price > limit if side == _BUY else price < limit):
                break
            queue = opposite.levels[price]
            while remaining > 0 and queue:
                maker = queue[0]
                q = min(remaining, maker.remaining)
                fills.append((self._next_seq, asset, price, q, order_id, maker.order_id, owner, maker.owner, side))
                book.last_trade = (self._next_seq, price)
                self._next_seq += 1
                remaining -= q
                maker.remaining -= q
                notional += price * q
                if maker.remaining == 0:
                    del self._index[maker.order_id]
                    opposite.pop_front(price)

        filled = qty - remaining
        resting = 0
        if remaining > 0 and tif != _IOC:
            node = _Resting(order_id, owner, remaining)
            same.add(limit, node)
            self._index[order_id] = (same, limit, node)
            resting = remaining

        if filled == qty:
            status = Status.FILLED
        elif filled == 0:
            status = Status.RESTING if resting else Status.CANCELLED_IOC
        else:
            status = Status.PARTIAL_RESTING if resting else Status.PARTIAL_CANCELLED
        avg = notional / filled if filled else math.nan
        return Report(order_id, status, filled, resting, avg), np.array(fills, dtype=FILL_DTYPE)

    def submit_batch(self, orders: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        reports, fills = [], []
        for o in orders:
            r, f = self.submit(o["asset"], o["owner"], o["side"], o["qty"], o["limit"], o["tif"])
            reports.append((r.order_id, int(r.status), r.filled_qty, r.resting_qty, r.avg_price))
            fills.append(f)
        all_fills = np.concatenate(fills) if fills else empty_fills()
        return np.array(reports, dtype=REPORT_DTYPE), all_fills

    def cancel(self, order_id: int) -> int:
        entry = self._index.pop(int(order_id), None)
        if entry is None:
            return 0
        side, price, node = entry
        side.remove(price, node)
        return node.remaining

    def _book(self, asset: int) -> _Book:
        if not 0 <= asset < self._n_assets:
            raise IndexError(f"asset {asset} out of range")
        return self._books[asset]

    def top(self, asset: int) -> Top:
        book = self._book(asset)
        bid, ask = book.bids.best(), book.asks.best()
        return Top(
            NO_PRICE if bid is None else bid, 0 if bid is None else book.bids.level_qty(bid),
            NO_PRICE if ask is None else ask, 0 if ask is None else book.asks.level_qty(ask),
        )

    def depth(self, asset: int, levels: int) -> np.ndarray:
        if levels < 0:
            raise ValueError("levels must be non-negative")
        book = self._book(asset)
        out = np.zeros(levels, dtype=DEPTH_DTYPE)
        out["bid"] = NO_PRICE
        out["ask"] = NO_PRICE
        for k, price in enumerate(book.bids.prices_best_first()[:levels]):
            out["bid"][k], out["bid_qty"][k] = price, book.bids.level_qty(price)
        for k, price in enumerate(book.asks.prices_best_first()[:levels]):
            out["ask"][k], out["ask_qty"][k] = price, book.asks.level_qty(price)
        return out

    def last_trade(self, asset: int) -> tuple[int, int]:
        return self._book(asset).last_trade
