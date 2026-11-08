"""Transparent F4 trading rules. Orders have integer tick limits and integer quantities."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from marketsim.market.api import NO_PRICE, MarketDataView, PortfolioView, Side, TimeInForce, Top
from marketsim.sim.settings import AgentSettings


@dataclass(frozen=True)
class OrderIntent:
    side: int
    qty: int
    limit: int
    tif: int


def market_price(market: MarketDataView, fallback: int) -> int:
    """Use an integer midpoint, then last trade, then the configured initial price."""
    top = market.top(0)
    if top.bid != NO_PRICE and top.ask != NO_PRICE:
        return max(1, (top.bid + top.ask) // 2)
    _, last = market.last_trade(0)
    return last if last != NO_PRICE else fallback


def valuer_signal(valuation: float, price: int, gamma: float) -> float:
    """Undervaluation produces a buy signal, capped in [-1, 1]."""
    return float(np.clip((valuation - price) / (gamma * price), -1.0, 1.0))


def trend_signal(price: int, past_price: int, scale: float) -> float:
    """Scale the price return over the configured lookback window."""
    return float(np.clip((price - past_price) / (past_price * scale), -1.0, 1.0))


def noise_signal(rng: np.random.Generator) -> float:
    """Draw a private signal; all randomness comes from the run's seeded generator."""
    return float(rng.uniform(-1.0, 1.0))


def make_order(
    signal: float, price: int, top: Top, portfolio: PortfolioView,
    settings: AgentSettings, rng: np.random.Generator,
) -> OrderIntent | None:
    """Convert a signal to a resource-capped passive limit or protected IOC order."""
    s = settings
    if abs(signal) <= s.threshold:
        return None
    side = int(Side.BUY if signal > 0 else Side.SELL)
    aggressive = rng.random() < s.aggressive_prob
    if aggressive:
        # These are intermediate calculations; the actual order limit is always an integer.
        protected = price * (1 + side * s.protection_fraction)
        limit = math.floor(protected) if side == Side.BUY else math.ceil(protected)
        tif = int(TimeInForce.IOC)
    else:
        if side == Side.BUY:
            base = top.bid if top.bid != NO_PRICE else price - s.market_maker_spread_ticks // 2
            limit = base - s.passive_offset_ticks
        else:
            base = top.ask if top.ask != NO_PRICE else price + s.market_maker_spread_ticks // 2
            limit = base + s.passive_offset_ticks
        tif = int(TimeInForce[s.tif])
    limit = max(1, int(limit))
    cash = portfolio.cash_available + portfolio.cash_reserved
    shares = int(portfolio.shares_available[0]) + int(portfolio.shares_reserved[0])
    wealth = cash + shares * price
    qty = math.floor(s.rho * abs(signal) * wealth / price)
    capacity = portfolio.cash_available // limit if side == Side.BUY else int(portfolio.shares_available[0])
    qty = min(qty, capacity)
    return OrderIntent(side, qty, limit, tif) if qty > 0 else None


def maker_orders(
    price: int, portfolio: PortfolioView, initial_shares: int, settings: AgentSettings,
) -> list[OrderIntent]:
    """Quote both sides; excess inventory lowers the center and scarce resources cap size."""
    s = settings
    inventory = int(portfolio.shares_available[0]) + int(portfolio.shares_reserved[0])
    center = price - round(s.inventory_skew_ticks * (inventory - initial_shares))
    bid = max(1, center - s.market_maker_spread_ticks // 2)
    ask = max(bid + 1, center + (s.market_maker_spread_ticks + 1) // 2)
    tif = int(TimeInForce.GTD if s.tif == "GTD" else TimeInForce.GTC)
    out = []
    buy_qty = min(s.market_maker_qty, portfolio.cash_available // bid)
    sell_qty = min(s.market_maker_qty, int(portfolio.shares_available[0]))
    if buy_qty:
        out.append(OrderIntent(int(Side.BUY), buy_qty, bid, tif))
    if sell_qty:
        out.append(OrderIntent(int(Side.SELL), sell_qty, ask, tif))
    return out
