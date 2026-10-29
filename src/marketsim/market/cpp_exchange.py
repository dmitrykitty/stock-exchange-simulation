"""Adapter from the C++ engine ``lob_core`` to contract K1 (owner: O).

``lob_core`` is built separately: ``pip install -e cpp/`` (needs a C++ compiler and CMake).
If it is not installed, ``CppExchange`` raises ImportError and the rest of the project runs on PyExchange.

Results cross the language boundary as one contiguous buffer of packed records per call (the ``*_packed``
methods of lob_core). numpy takes the buffer without a copy and ``.view(dtype)`` reinterprets it with the
contract dtypes, so no Python object is created per trade or per report. At import time the record layout
reported by C++ (``lob_core.WIRE_LAYOUT``) is checked against the dtypes of market/api.py; a mismatch
refuses to load instead of silently corrupting data.
"""

from __future__ import annotations

import numpy as np

from marketsim.market.api import (
    CONTRACT_VERSION,
    DEPTH_DTYPE,
    FILL_DTYPE,
    ORDER_DTYPE,
    REPORT_DTYPE,
    Report,
    Status,
    Top,
)

_STATUS = {int(s): s for s in Status}
_DTYPES = {"order": ORDER_DTYPE, "fill": FILL_DTYPE, "report": REPORT_DTYPE, "level": DEPTH_DTYPE}
_lob_core = None


def _check_layout(layout: dict) -> None:
    for name, dtype in _DTYPES.items():
        cpp = layout[name]
        python_fields = [(field, dtype.fields[field][1]) for field in dtype.names]
        cpp_fields = [(str(field), int(offset)) for field, offset in cpp["fields"]]
        if int(cpp["size"]) != dtype.itemsize or cpp_fields != python_fields:
            raise ImportError(
                f"lob_core record layout of {name!r} {cpp_fields} (size {cpp['size']}) does not match "
                f"the contract dtype {python_fields} (size {dtype.itemsize})"
            )


def _import_lob_core():
    global _lob_core
    if _lob_core is not None:
        return _lob_core
    try:
        import lob_core
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError("lob_core is not built. Build the engine with: pip install -e cpp/") from exc
    if lob_core.CONTRACT_VERSION != CONTRACT_VERSION:
        raise ImportError(
            f"lob_core contract version {lob_core.CONTRACT_VERSION} != Python contract version {CONTRACT_VERSION}"
        )
    _check_layout(lob_core.WIRE_LAYOUT)
    _lob_core = lob_core
    return lob_core


def is_available() -> bool:
    try:
        _import_lob_core()
    except ImportError:
        return False
    return True


def _records(buffer, dtype: np.dtype) -> np.ndarray:
    """A packed byte buffer from lob_core (or None) as a writable array of ``dtype`` records, without copying."""
    if buffer is None:
        return np.zeros(0, dtype=dtype)
    return buffer.view(dtype)


class CppExchange:
    def __init__(self, n_assets: int) -> None:
        self._ex = _import_lob_core().Exchange(n_assets)

    @property
    def n_assets(self) -> int:
        return int(self._ex.n_assets)

    def submit(self, asset: int, owner: int, side: int, qty: int, limit: int, tif: int) -> tuple[Report, np.ndarray]:
        order_id, status, filled, resting, avg, fills = self._ex.submit_packed(asset, owner, side, qty, limit, tif)
        return Report(order_id, _STATUS[status], filled, resting, avg), _records(fills, FILL_DTYPE)

    def submit_batch(self, orders: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        packed = np.ascontiguousarray(orders, dtype=ORDER_DTYPE).view(np.uint8)
        reports, fills = self._ex.submit_batch_packed(packed)
        return _records(reports, REPORT_DTYPE), _records(fills, FILL_DTYPE)

    def cancel(self, order_id: int) -> int:
        return int(self._ex.cancel(order_id))

    def top(self, asset: int) -> Top:
        return Top(*self._ex.top(asset))

    def depth(self, asset: int, levels: int) -> np.ndarray:
        if levels < 0:
            raise ValueError("levels must be non-negative")
        return _records(self._ex.depth_packed(asset, levels), DEPTH_DTYPE)

    def last_trade(self, asset: int) -> tuple[int, int]:
        return self._ex.last_trade(asset)
