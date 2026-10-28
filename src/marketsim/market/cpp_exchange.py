"""Adapter from the C++ engine ``lob_core`` to contract K1 (owner: O).

``lob_core`` is built separately: ``pip install -e cpp/`` (needs a C++ compiler and CMake).
If it is not installed, ``CppExchange`` raises ImportError and the rest of the project runs on PyExchange.

The conversion to numpy currently goes through lists of Python objects: simple and correct, but slow.
Optimizing it (returning buffers instead of lists) is internal to the engine and does not change the contract.
"""

from __future__ import annotations

import math

import numpy as np

from marketsim.market.api import CONTRACT_VERSION, DEPTH_DTYPE, FILL_DTYPE, REPORT_DTYPE, Report, Status, Top


def _import_lob_core():
    try:
        import lob_core
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError("lob_core is not built. Build the engine with: pip install -e cpp/") from exc
    if lob_core.CONTRACT_VERSION != CONTRACT_VERSION:
        raise ImportError(
            f"lob_core contract version {lob_core.CONTRACT_VERSION} != Python contract version {CONTRACT_VERSION}"
        )
    return lob_core


def is_available() -> bool:
    try:
        _import_lob_core()
    except ImportError:
        return False
    return True


def _fills_to_array(fills) -> np.ndarray:
    return np.array(
        [(f.seq, f.asset, f.price, f.qty, f.taker_order, f.maker_order, f.taker_owner, f.maker_owner, f.taker_side)
         for f in fills],
        dtype=FILL_DTYPE,
    )


def _report(r) -> Report:
    avg = float(r.avg_price) if r.filled_qty > 0 else math.nan
    return Report(int(r.order_id), Status(r.status), int(r.filled_qty), int(r.resting_qty), avg)


class CppExchange:
    def __init__(self, n_assets: int) -> None:
        self._lob = _import_lob_core()
        self._ex = self._lob.Exchange(n_assets)

    @property
    def n_assets(self) -> int:
        return int(self._ex.n_assets)

    def submit(self, asset: int, owner: int, side: int, qty: int, limit: int, tif: int) -> tuple[Report, np.ndarray]:
        report, fills = self._ex.submit(asset, owner, side, qty, limit, tif)
        return _report(report), _fills_to_array(fills)

    def submit_batch(self, orders: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        requests = []
        for o in orders:
            r = self._lob.OrderRequest()
            r.asset, r.owner, r.side = int(o["asset"]), int(o["owner"]), int(o["side"])
            r.qty, r.limit, r.tif = int(o["qty"]), int(o["limit"]), int(o["tif"])
            requests.append(r)
        reports, fills = self._ex.submit_batch(requests)
        rep = np.array(
            [(r.order_id, r.status, r.filled_qty, r.resting_qty, r.avg_price if r.filled_qty > 0 else math.nan)
             for r in reports],
            dtype=REPORT_DTYPE,
        )
        return rep, _fills_to_array(fills)

    def cancel(self, order_id: int) -> int:
        return int(self._ex.cancel(order_id))

    def top(self, asset: int) -> Top:
        return Top(*(int(v) for v in self._ex.top(asset)))

    def depth(self, asset: int, levels: int) -> np.ndarray:
        return np.array(self._ex.depth(asset, levels), dtype=DEPTH_DTYPE)

    def last_trade(self, asset: int) -> tuple[int, int]:
        seq, price = self._ex.last_trade(asset)
        return int(seq), int(price)
