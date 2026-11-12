"""Transparent trading rules (F4-F6). Orders have integer tick limits and integer quantities."""

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


def market_price(market: MarketDataView, fallback: int, asset: int = 0) -> int:
    """Use an integer midpoint, then last trade, then the configured initial price."""
    top = market.top(asset)
    if top.bid != NO_PRICE and top.ask != NO_PRICE:
        return max(1, (top.bid + top.ask) // 2)
    _, last = market.last_trade(asset)
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


def _limit_and_tif(
    side: int, price: int, top: Top, settings: AgentSettings, rng: np.random.Generator,
) -> tuple[int, int]:
    """Aggressive (protected IOC) or passive (at the own best quote) limit; draws once from ``rng``."""
    s = settings
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
    return max(1, int(limit)), tif


def make_order(
    signal: float, price: int, top: Top, portfolio: PortfolioView,
    settings: AgentSettings, rng: np.random.Generator, asset: int = 0, wealth: int | None = None,
) -> OrderIntent | None:
    """Use a one-share minimum above threshold, capped by free resources; rho=0 disables orders.

    ``wealth`` is the whole portfolio value in cents (cash plus all holdings at market prices); by default it is
    computed for a one-asset portfolio, as in F4.
    """
    s = settings
    if abs(signal) <= s.threshold or s.rho == 0:
        return None
    side = int(Side.BUY if signal > 0 else Side.SELL)
    limit, tif = _limit_and_tif(side, price, top, s, rng)
    if wealth is None:
        cash = portfolio.cash_available + portfolio.cash_reserved
        shares = int(portfolio.shares_available[asset]) + int(portfolio.shares_reserved[asset])
        wealth = cash + shares * price
    qty = max(1, math.floor(s.rho * abs(signal) * wealth / price))
    capacity = portfolio.cash_available // limit if side == Side.BUY else int(portfolio.shares_available[asset])
    qty = min(qty, capacity)
    return OrderIntent(side, qty, limit, tif) if qty > 0 else None


def rebalance_order(
    deviation: float, target_value: float, price: int, top: Top, portfolio: PortfolioView,
    settings: AgentSettings, rng: np.random.Generator, asset: int,
) -> OrderIntent | None:
    """Order of a rebalancer (F6): bring the position back to its target value.

    ``deviation`` = (target value - current value) / target value. Inside the band nothing happens; outside, the
    whole gap is ordered (whole shares), priced like any investor order and capped by free resources.
    """
    s = settings
    if abs(deviation) <= s.rebalance_band:
        return None
    side = int(Side.BUY if deviation > 0 else Side.SELL)
    limit, tif = _limit_and_tif(side, price, top, s, rng)
    qty = math.floor(abs(deviation) * target_value / price)
    capacity = portfolio.cash_available // limit if side == Side.BUY else int(portfolio.shares_available[asset])
    qty = min(qty, capacity)
    return OrderIntent(side, qty, limit, tif) if qty > 0 else None


def target_order(
    gap_value: float, cap_value: float, price: int, top: Top, portfolio: PortfolioView,
    settings: AgentSettings, rng: np.random.Generator, asset: int,
) -> OrderIntent | None:
    """Order of an investor with a target position (F6): close the gap between the target and the held value.

    ``gap_value`` = target value - held value in cents; at most ``cap_value`` is ordered per decision. Whole
    shares only: a gap smaller than one share gives no order.
    """
    qty = math.floor(min(abs(gap_value), cap_value) / price)
    if qty < 1:
        return None
    side = int(Side.BUY if gap_value > 0 else Side.SELL)
    limit, tif = _limit_and_tif(side, price, top, settings, rng)
    capacity = portfolio.cash_available // limit if side == Side.BUY else int(portfolio.shares_available[asset])
    qty = min(qty, capacity)
    return OrderIntent(side, qty, limit, tif) if qty > 0 else None


def risk_sale(price: int, qty: int, settings: AgentSettings) -> OrderIntent:
    """Protected IOC sale used when an investor's risk limit is hit (F6)."""
    return OrderIntent(int(Side.SELL), qty, max(1, math.ceil(price * (1 - settings.protection_fraction))),
                       int(TimeInForce.IOC))


def maker_orders(
    price: int, portfolio: PortfolioView, initial_shares: int, settings: AgentSettings, asset: int = 0,
) -> list[OrderIntent]:
    """Quote both sides; excess inventory lowers the center and scarce resources cap size."""
    s = settings
    inventory = int(portfolio.shares_available[asset]) + int(portfolio.shares_reserved[asset])
    center = price - round(s.inventory_skew_ticks * (inventory - initial_shares))
    bid = max(1, center - s.market_maker_spread_ticks // 2)
    ask = max(bid + 1, center + (s.market_maker_spread_ticks + 1) // 2)
    tif = int(TimeInForce.GTD if s.tif == "GTD" else TimeInForce.GTC)
    out = []
    buy_qty = min(s.market_maker_qty, portfolio.cash_available // bid)
    sell_qty = min(s.market_maker_qty, int(portfolio.shares_available[asset]))
    if buy_qty:
        out.append(OrderIntent(int(Side.BUY), buy_qty, bid, tif))
    if sell_qty:
        out.append(OrderIntent(int(Side.SELL), sell_qty, ask, tif))
    return out
