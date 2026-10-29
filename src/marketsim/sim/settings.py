"""Validated, simulation-owned parameters inside the free-form K4 agents section."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields

from marketsim.experiments.config import ConfigError


@dataclass(frozen=True)
class HerdingSettings:
    h: float = 0.0
    neighbors: int = 5
    ttl: float = 0.5


@dataclass(frozen=True)
class AgentSettings:
    ticker: str = "SYNTH"
    initial_price_ticks: int = 10_000
    valuers: int = 40
    trend: int = 20
    noise: int = 20
    market_makers_per_asset: int = 1
    n_funds: int = 8
    concentration: float = 0.5
    total_cash_cents: int = 80_000_000
    total_shares: int = 8_000
    market_maker_cash_cents: int = 20_000_000
    market_maker_shares: int = 2_000
    valuation_std: float = 0.02
    gamma: float = 0.05
    rho: float = 0.02
    threshold: float = 0.05
    decision_rate: float = 2.0
    market_maker_rate: float = 20.0
    market_maker_spread_ticks: int = 10
    market_maker_qty: int = 20
    inventory_skew_ticks: float = 0.02
    trend_lookback: float = 1.0
    trend_scale: float = 0.02
    aggressive_prob: float = 0.5
    protection_fraction: float = 0.01
    passive_offset_ticks: int = 0
    tif: str = "GTD"
    order_lifetime: float = 0.5
    debug: bool = False
    ablation: str = "none"
    herding: HerdingSettings = field(default_factory=HerdingSettings)

    @classmethod
    def from_dict(cls, raw: dict) -> AgentSettings:
        """Reject misspellings and invalid parameters before creating any run output."""
        if not isinstance(raw, dict):
            raise ConfigError("agents must be a mapping")
        data = dict(raw)
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ConfigError(f"unknown agents keys: {sorted(unknown)}")
        herd = data.pop("herding", {})
        if not isinstance(herd, dict) or set(herd) - {f.name for f in fields(HerdingSettings)}:
            raise ConfigError("herding must contain only h, neighbors, ttl")
        settings = cls(**data, herding=HerdingSettings(**herd))
        nonnegative_ints = (
            "valuers", "trend", "noise", "market_makers_per_asset", "n_funds", "total_cash_cents",
            "total_shares", "market_maker_cash_cents", "market_maker_shares", "passive_offset_ticks",
        )
        positive_ints = ("initial_price_ticks", "market_maker_qty", "market_maker_spread_ticks")
        for name in nonnegative_ints + positive_ints:
            value = getattr(settings, name)
            if type(value) is not int or value < (1 if name in positive_ints else 0):
                raise ConfigError(f"agents.{name} must be a valid integer")
        if settings.market_maker_spread_ticks < 2:
            raise ConfigError("market_maker_spread_ticks must be >= 2")
        if settings.initial_price_ticks > 10**12:
            raise ConfigError("initial_price_ticks must be <= 1000000000000")
        count = settings.valuers + settings.trend + settings.noise
        if not 0 < settings.n_funds < count:
            raise ConfigError("need investors and 0 < n_funds < investor count")
        if not isinstance(settings.ticker, str) or not settings.ticker.strip():
            raise ConfigError("ticker must be a non-empty string")
        if type(settings.debug) is not bool:
            raise ConfigError("debug must be boolean")
        if settings.tif not in ("GTC", "GTD", "IOC"):
            raise ConfigError("tif must be GTC, GTD or IOC")
        if settings.ablation not in ("none", "no_herding", "no_trend"):
            raise ConfigError("ablation must be none, no_herding or no_trend")
        positive = ("gamma", "decision_rate", "market_maker_rate", "trend_lookback", "trend_scale",
                    "order_lifetime", "protection_fraction")
        unit = ("rho", "threshold", "aggressive_prob", "concentration")
        for name in positive + unit + ("valuation_std", "inventory_skew_ticks"):
            value = getattr(settings, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ConfigError(f"agents.{name} must be finite")
            if value < 0 or (name in positive and value == 0) or (name in unit and value > 1):
                raise ConfigError(f"agents.{name} is outside its allowed range")
        if settings.valuation_std > 0.25 or settings.protection_fraction > 0.5:
            raise ConfigError("valuation_std must be <= 0.25 and protection_fraction <= 0.5")
        h = settings.herding
        if type(h.neighbors) is not int or h.neighbors < 0:
            raise ConfigError("herding.neighbors must be a non-negative integer")
        for name, value in (("h", h.h), ("ttl", h.ttl)):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ConfigError(f"herding.{name} must be finite")
        if not 0 <= h.h <= 1 or h.ttl <= 0:
            raise ConfigError("need 0 <= herding.h <= 1 and herding.ttl > 0")
        mm = settings.market_makers_per_asset
        if settings.total_cash_cents + mm * settings.market_maker_cash_cents > 2**63 - 1:
            raise ConfigError("total cash exceeds int64")
        if settings.total_shares + mm * settings.market_maker_shares > 2**63 - 1:
            raise ConfigError("total shares exceeds int64")
        return settings
