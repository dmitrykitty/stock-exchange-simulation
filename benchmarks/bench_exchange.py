"""Exchange throughput: orders per second for each backend and call style.

Run: ``python benchmarks/bench_exchange.py [--orders N] [--assets K]``

Measures the reference PyExchange, the C++ engine through the contract adapter (one order per call and
batches), and the raw lob_core module without numpy conversion, to show where the time goes.
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from marketsim.market import PyExchange
from marketsim.market.api import ORDER_DTYPE
from marketsim.market.cpp_exchange import CppExchange, is_available


def random_orders(n: int, n_assets: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    orders = np.zeros(n, dtype=ORDER_DTYPE)
    orders["asset"] = rng.integers(0, n_assets, n)
    orders["owner"] = rng.integers(0, 1000, n)
    orders["side"] = rng.choice([1, -1], n)
    orders["qty"] = rng.integers(1, 50, n)
    orders["limit"] = 10_000 + rng.integers(-50, 51, n)
    orders["tif"] = rng.choice([0, 0, 0, 1], n)
    return orders


def rate(fn, n: int) -> float:
    start = time.perf_counter()
    fn()
    return n / (time.perf_counter() - start)


def one_by_one(ex, orders):
    def run():
        for o in orders:
            ex.submit(int(o["asset"]), int(o["owner"]), int(o["side"]), int(o["qty"]), int(o["limit"]), int(o["tif"]))
    return run


def batched(ex, orders, size):
    def run():
        for i in range(0, len(orders), size):
            ex.submit_batch(orders[i:i + size])
    return run


def raw_lob_core(n_assets, orders):
    import lob_core

    rows = [tuple(int(v) for v in o) for o in orders]

    def run():
        ex = lob_core.Exchange(n_assets)
        for a, w, s, q, p, t in rows:
            ex.submit(a, w, s, q, p, t)
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=int, default=200_000)
    parser.add_argument("--assets", type=int, default=18)
    args = parser.parse_args()
    orders = random_orders(args.orders, args.assets)

    results = [("PyExchange, one order per call", rate(one_by_one(PyExchange(args.assets), orders), len(orders)))]
    if is_available():
        results += [
            ("CppExchange, one order per call", rate(one_by_one(CppExchange(args.assets), orders), len(orders))),
            ("CppExchange, batches of 100", rate(batched(CppExchange(args.assets), orders, 100), len(orders))),
            ("CppExchange, batches of 1000", rate(batched(CppExchange(args.assets), orders, 1000), len(orders))),
            ("lob_core raw (no numpy conversion)", rate(raw_lob_core(args.assets, orders), len(orders))),
        ]
    else:
        print("lob_core is not built: pip install -e cpp/")
    print(f"{args.orders} orders, {args.assets} assets")
    for name, r in results:
        print(f"  {name:38s} {r:12,.0f} orders/s")


if __name__ == "__main__":
    main()
