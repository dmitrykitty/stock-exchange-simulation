"""Exchange and gateway (contract K1, owner: O).

Usage inside the simulation::

    from marketsim.market import make_exchange, Gateway
    ex = make_exchange("python", n_assets=3)   # or "cpp" once the engine is built
    gw = Gateway(ex, cash, shares)
"""

from __future__ import annotations

from marketsim.market.api import (
    CONTRACT_VERSION,
    DEPTH_DTYPE,
    FILL_DTYPE,
    NO_PRICE,
    ORDER_DTYPE,
    REPORT_DTYPE,
    Exchange,
    MarketDataView,
    MarketGateway,
    PortfolioView,
    Report,
    Side,
    Status,
    TimeInForce,
    Top,
)
from marketsim.market.gateway import Gateway
from marketsim.market.py_exchange import PyExchange


def make_exchange(backend: str, n_assets: int) -> Exchange:
    """backend: "python" (reference) or "cpp" (lob_core)."""
    if backend == "python":
        return PyExchange(n_assets)
    if backend == "cpp":
        from marketsim.market.cpp_exchange import CppExchange

        return CppExchange(n_assets)
    raise ValueError(f"unknown backend: {backend!r}")


__all__ = [
    "CONTRACT_VERSION", "DEPTH_DTYPE", "FILL_DTYPE", "NO_PRICE", "ORDER_DTYPE", "REPORT_DTYPE",
    "Exchange", "Gateway", "MarketDataView", "MarketGateway", "PortfolioView", "PyExchange", "Report",
    "Side", "Status", "TimeInForce", "Top", "make_exchange",
]
