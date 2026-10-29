"""Property-based differential test: random submits and cancels give identical results in C++ and Python.

hypothesis shrinks a failing stream to a minimal example, which is the fastest way to locate a C++ bug.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from marketsim.market import PyExchange
from marketsim.market.cpp_exchange import CppExchange, is_available

pytestmark = pytest.mark.skipif(not is_available(), reason="lob_core not built")

N_ASSETS = 2

submit_op = st.tuples(
    st.just("submit"),
    st.integers(0, N_ASSETS),              # N_ASSETS itself is an invalid asset
    st.integers(0, 3),                     # few owners -> frequent self-trade situations
    st.sampled_from([1, -1, 1, -1, 0]),    # 0 is an invalid side
    st.integers(-1, 12),                   # qty <= 0 is invalid
    st.integers(94, 106),
    st.sampled_from([0, 0, 1, 2, 7]),      # 7 is an invalid time in force
)
cancel_op = st.tuples(st.just("cancel"), st.integers(0, 10_000))


def _same_report(a, b) -> bool:
    same_avg = (math.isnan(a.avg_price) and math.isnan(b.avg_price)) or a.avg_price == b.avg_price
    return (a.order_id, a.status, a.filled_qty, a.resting_qty) == (b.order_id, b.status, b.filled_qty, b.resting_qty) and same_avg


@settings(max_examples=300, deadline=None)
@given(st.lists(st.one_of(submit_op, submit_op, cancel_op), min_size=1, max_size=150))
def test_cpp_matches_python_with_cancels(ops):
    py, cpp = PyExchange(N_ASSETS), CppExchange(N_ASSETS)
    ids: list[int] = []
    for op in ops:
        if op[0] == "submit":
            (r_py, f_py), (r_cpp, f_cpp) = py.submit(*op[1:]), cpp.submit(*op[1:])
            assert _same_report(r_py, r_cpp)
            assert len(f_py) == len(f_cpp) and bool(np.all(f_py == f_cpp))
            ids.append(r_py.order_id)
        elif ids:
            order_id = ids[op[1] % len(ids)]
            assert py.cancel(order_id) == cpp.cancel(order_id)
        for a in range(N_ASSETS):
            assert bool(np.all(py.depth(a, 6) == cpp.depth(a, 6)))
            assert py.last_trade(a) == cpp.last_trade(a)
