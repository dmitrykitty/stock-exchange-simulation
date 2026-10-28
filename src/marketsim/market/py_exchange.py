"""Reference exchange implementation in pure Python (owner: O).

Simple and slow, but it must be correct. It exists so that
1) S can write agents without waiting for the C++ engine;
2) the C++ engine can be checked with differential tests (same orders -> same trades).

Skeleton: methods raise NotImplementedError, so contract tests are skipped for now.
Implementation plan: docs/plans/phases/F3_exchange_engine.md.
"""

from __future__ import annotations

import numpy as np

from marketsim.market.api import Report, Top


class PyExchange:
    def __init__(self, n_assets: int) -> None:
        if n_assets <= 0:
            raise ValueError("n_assets must be positive")
        self._n_assets = n_assets

    @property
    def n_assets(self) -> int:
        return self._n_assets

    def submit(self, asset: int, owner: int, side: int, qty: int, limit: int, tif: int) -> tuple[Report, np.ndarray]:
        raise NotImplementedError("PyExchange.submit (owner: O, see docs/plans/phases/F3_exchange_engine.md)")

    def submit_batch(self, orders: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        raise NotImplementedError("PyExchange.submit_batch (owner: O)")

    def cancel(self, order_id: int) -> int:
        raise NotImplementedError("PyExchange.cancel (owner: O)")

    def top(self, asset: int) -> Top:
        raise NotImplementedError("PyExchange.top (owner: O)")

    def depth(self, asset: int, levels: int) -> np.ndarray:
        raise NotImplementedError("PyExchange.depth (owner: O)")

    def last_trade(self, asset: int) -> tuple[int, int]:
        raise NotImplementedError("PyExchange.last_trade (owner: O)")
