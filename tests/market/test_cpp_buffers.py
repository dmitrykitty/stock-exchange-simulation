"""The packed-buffer path between lob_core and numpy: layout check, ownership, edge cases."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from marketsim.market import PyExchange
from marketsim.market.api import DEPTH_DTYPE, FILL_DTYPE, ORDER_DTYPE, REPORT_DTYPE
from marketsim.market.cpp_exchange import CppExchange, _check_layout, is_available

pytestmark = pytest.mark.skipif(not is_available(), reason="lob_core not built")


def test_layout_check_rejects_a_mismatch():
    import lob_core

    layout = copy.deepcopy(lob_core.WIRE_LAYOUT)
    _check_layout(layout)
    fields = list(layout["fill"]["fields"])
    fields[0], fields[1] = fields[1], fields[0]
    layout["fill"]["fields"] = tuple(fields)
    with pytest.raises(ImportError):
        _check_layout(layout)


def test_results_are_writable_and_do_not_alias():
    ex = CppExchange(1)
    ex.submit(0, 1, -1, 5, 101, 0)
    ex.submit(0, 2, -1, 5, 101, 0)
    _, first = ex.submit(0, 3, 1, 5, 101, 0)
    _, second = ex.submit(0, 3, 1, 5, 101, 0)
    assert first.dtype == FILL_DTYPE and first.flags.writeable
    first["qty"] = 0
    assert int(second["qty"][0]) == 5


def test_empty_results_have_the_contract_dtype():
    ex = CppExchange(1)
    _, fills = ex.submit(0, 1, 1, 5, 100, 1)
    assert len(fills) == 0 and fills.dtype == FILL_DTYPE
    reports, fills = ex.submit_batch(np.zeros(0, dtype=ORDER_DTYPE))
    assert reports.dtype == REPORT_DTYPE and fills.dtype == FILL_DTYPE and len(reports) == len(fills) == 0
    assert ex.depth(0, 0).dtype == DEPTH_DTYPE and len(ex.depth(0, 0)) == 0


def test_batch_accepts_slices_and_other_layouts():
    orders = np.zeros(6, dtype=ORDER_DTYPE)
    orders["side"] = [-1, 1, -1, 1, -1, 1]
    orders["owner"] = [1, 2, 3, 4, 5, 6]
    orders["qty"] = 3
    orders["limit"] = 100
    every_other = orders[::2]                                   # not contiguous
    aligned = orders.astype(np.dtype(ORDER_DTYPE.descr, align=True))
    for batch in (every_other, aligned):
        py_r, py_f = PyExchange(1).submit_batch(batch)
        cpp_r, cpp_f = CppExchange(1).submit_batch(batch)
        assert (py_r[["order_id", "status"]] == cpp_r[["order_id", "status"]]).all()
        assert len(py_f) == len(cpp_f)


def test_depth_buffer_survives_its_exchange():
    ex = CppExchange(1)
    ex.submit(0, 1, 1, 4, 99, 0)
    depth = ex.depth(0, 3)
    del ex
    assert int(depth["bid"][0]) == 99 and int(depth["bid_qty"][0]) == 4
