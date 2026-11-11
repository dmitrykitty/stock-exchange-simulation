"""Validated, simulation-owned parameters inside the free-form K4 agents section.

Two modes share these settings. With ``graph: single_asset`` (F4) the market has one synthetic asset and the
valuers hold constant valuations. With a K2 graph (F5+) every company of the graph is an asset, valuers are
myopic or network valuers that update their valuations from the graph, and ``valuers`` must be zero. Fields
added for F5 and F6 default to values that leave F4 and F5 runs bit-identical.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields, replace

from marketsim.experiments.config import ConfigError


@dataclass(frozen=True)
class HerdingSettings:
    h: float = 0.0
    neighbors: int = 5
    ttl: float = 0.5
    # Heterogeneous susceptibility: a share ``followers`` of investors herds, the rest decides alone (h = 0).
    # Each follower's own h is drawn once per run from N(h, h_std) clipped to [0, 1]. Defaults: everyone has h.
    followers: float = 1.0
    h_std: float = 0.0


# Investor mixes for experiment E4 (F5): shares of myopic valuers, network valuers, trend followers, noise.
# Calibrated on the F5 pilot: with 45% trend followers every chain run crashed (an asset lost > 25% from its peak),
# so the trend-heavy mix uses 30% (docs/guides/f5_graph_valuers.md).
COMPOSITIONS = {
    "network": (0.15, 0.45, 0.20, 0.20),
    "mixed": (0.30, 0.30, 0.20, 0.20),
    "trend": (0.35, 0.15, 0.30, 0.20),
}


@dataclass(frozen=True)
class AgentSettings:
    ticker: str = "SYNTH"
    initial_price_ticks: int = 10_000
    valuers: int = 40
    # F5 graph mode: valuers that know only external parents (w_direct) / also company parents (w_full).
    valuers_myopic: int = 0
    valuers_network: int = 0
    # Named mix from COMPOSITIONS applied to the total investor count, or "custom" to use the counts as given.
    composition: str = "custom"
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
    # F5 graph mode: assets each investor trades (0 = all), mean observation delay of parents' prices in sessions
    # (each agent draws its delay uniformly from [0, 2 * mean]), and std of each agent's relative error in each
    # graph weight (perceived weight = w * (1 + error)).
    attention: int = 0
    observation_delay: float = 0.0
    weight_error_std: float = 0.0
    # F6 full network. ``assets``: tickers to trade (empty = every company of the graph); the subset must contain
    # the company parents of its members, so the graph weights keep their meaning.
    assets: tuple[str, ...] = ()
    # ``price_scaling``: quantities and tick parameters are stated for a stock at ``initial_price_ticks`` and
    # converted for each asset by f = start price / initial_price_ticks: share counts (total_shares,
    # market_maker_shares, market_maker_qty) are divided by f, market_maker_spread_ticks is multiplied by f and
    # inventory_skew_ticks by f^2, so every asset has the same value outstanding, the same quote value, the same
    # relative spread and the same relative price shift per dollar of maker inventory.
    price_scaling: bool = False
    # ``holdings``: "all" gives every investor shares of every asset (F5); "followed" splits each asset's shares
    # among the investors that trade it, so nobody holds positions it never trades.
    holdings: str = "all"
    # Rebalancers: funds that keep the initial value weights of their followed assets. They trade an asset when
    # its weight is off target by more than ``rebalance_band`` (relative), back to the target; they ignore herding.
    # Every rebalancer is a fund, in addition to the ``n_funds`` funds drawn from the other investor groups.
    rebalancers: int = 0
    rebalance_band: float = 0.05
    # Risk limit (stop-loss) of every investor: when wealth falls more than ``risk_limit`` below its running peak
    # the agent sells ``risk_sell_fraction`` of each followed position with protected IOC orders and does not buy
    # for ``risk_cooldown`` sessions; then the peak restarts from the current wealth. 0 disables the limit.
    risk_limit: float = 0.0
    risk_sell_fraction: float = 1.0
    risk_cooldown: float = 5.0
    herding: HerdingSettings = field(default_factory=HerdingSettings)

    @property
    def investors(self) -> int:
        return (self.valuers + self.valuers_myopic + self.valuers_network + self.trend + self.noise
                + self.rebalancers)

    @classmethod
    def from_dict(cls, raw: dict, graph_mode: bool = False) -> AgentSettings:
        """Reject misspellings and invalid parameters before creating any run output."""
        if not isinstance(raw, dict):
            raise ConfigError("agents must be a mapping")
        data = dict(raw)
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ConfigError(f"unknown agents keys: {sorted(unknown)}")
        herd = data.pop("herding", {})
        if not isinstance(herd, dict) or set(herd) - {f.name for f in fields(HerdingSettings)}:
            raise ConfigError("herding must contain only h, neighbors, ttl, followers, h_std")
        if graph_mode:
            data.setdefault("valuers", 0)
        if "assets" in data:
            tickers = data["assets"]
            if (not isinstance(tickers, (list, tuple)) or not all(isinstance(t, str) and t for t in tickers)
                    or len(set(tickers)) != len(tickers)):
                raise ConfigError("agents.assets must be a list of distinct tickers")
            data["assets"] = tuple(tickers)
        settings = cls(**data, herding=HerdingSettings(**herd))
        nonnegative_ints = (
            "valuers", "valuers_myopic", "valuers_network", "trend", "noise", "market_makers_per_asset",
            "n_funds", "total_cash_cents", "total_shares", "market_maker_cash_cents", "market_maker_shares",
            "passive_offset_ticks", "attention", "rebalancers",
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
        if settings.market_maker_spread_ticks > 10**12 or settings.passive_offset_ticks > 10**12:
            raise ConfigError("quote offsets must be <= 1000000000000 ticks")
        if graph_mode:
            if settings.valuers:
                raise ConfigError("with a graph use valuers_myopic and valuers_network instead of valuers")
            if settings.composition not in ("custom", *COMPOSITIONS):
                raise ConfigError(f"composition must be custom or one of {sorted(COMPOSITIONS)}")
            if settings.composition != "custom":
                settings = settings.with_composition()
        else:
            changed = [f.name for f in fields(cls) if f.name in _GRAPH_FIELDS and getattr(settings, f.name) != f.default]
            if changed:
                raise ConfigError(f"agents {changed} need a graph (F5); graph='single_asset' supports F4 only")
        count = settings.investors - settings.rebalancers
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
        if type(settings.price_scaling) is not bool:
            raise ConfigError("price_scaling must be boolean")
        if settings.holdings not in ("all", "followed"):
            raise ConfigError("holdings must be all or followed")
        positive += ("risk_cooldown",)
        unit += ("rebalance_band", "risk_limit", "risk_sell_fraction")
        for name in positive + unit + ("valuation_std", "inventory_skew_ticks", "observation_delay", "weight_error_std"):
            value = getattr(settings, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ConfigError(f"agents.{name} must be finite")
            if value < 0 or (name in positive and value == 0) or (name in unit and value > 1):
                raise ConfigError(f"agents.{name} is outside its allowed range")
        if settings.valuation_std > 0.25 or settings.protection_fraction > 0.5:
            raise ConfigError("valuation_std must be <= 0.25 and protection_fraction <= 0.5")
        if settings.weight_error_std > 1 or settings.observation_delay > 10:
            raise ConfigError("weight_error_std must be <= 1 and observation_delay <= 10 sessions")
        if settings.rebalancers and settings.rebalance_band == 0:
            raise ConfigError("rebalance_band must be > 0")
        if settings.risk_limit and settings.risk_sell_fraction == 0:
            raise ConfigError("risk_sell_fraction must be > 0 when risk_limit is set")
        h = settings.herding
        if type(h.neighbors) is not int or h.neighbors < 0:
            raise ConfigError("herding.neighbors must be a non-negative integer")
        for name, value in (("h", h.h), ("ttl", h.ttl), ("followers", h.followers), ("h_std", h.h_std)):
            if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
                raise ConfigError(f"herding.{name} must be finite")
        if not 0 <= h.h <= 1 or h.ttl <= 0:
            raise ConfigError("need 0 <= herding.h <= 1 and herding.ttl > 0")
        if not 0 <= h.followers <= 1 or not 0 <= h.h_std <= 1:
            raise ConfigError("need 0 <= herding.followers <= 1 and 0 <= herding.h_std <= 1")
        mm = settings.market_makers_per_asset
        if settings.total_cash_cents + mm * settings.market_maker_cash_cents > 2**63 - 1:
            raise ConfigError("total cash exceeds int64")
        if settings.total_shares + mm * settings.market_maker_shares > 2**63 - 1:
            raise ConfigError("total shares exceeds int64")
        if settings.inventory_skew_ticks * (settings.total_shares + mm * settings.market_maker_shares) > 10**12:
            raise ConfigError("maximum inventory skew must be <= 1000000000000 ticks")
        return settings

    def with_composition(self) -> AgentSettings:
        """Replace the four investor counts by the named mix, keeping their total (largest remainders)."""
        total = self.valuers_myopic + self.valuers_network + self.trend + self.noise
        shares = COMPOSITIONS[self.composition]
        counts = [int(total * s) for s in shares]
        order = sorted(range(4), key=lambda i: (-(total * shares[i] - counts[i]), i))
        for i in order[:total - sum(counts)]:
            counts[i] += 1
        return replace(self, valuers_myopic=counts[0], valuers_network=counts[1], trend=counts[2], noise=counts[3])


def identity_dict(settings: AgentSettings) -> dict:
    """Settings as used in run identities: F5 and F6 fields at their defaults are left out, so the run_ids of
    earlier phases are unchanged."""
    data = asdict(settings)
    for f in fields(AgentSettings):
        if f.name in _GRAPH_FIELDS and getattr(settings, f.name) == f.default:
            data.pop(f.name)
    for name in ("followers", "h_std"):
        if data["herding"][name] == getattr(HerdingSettings, name):
            data["herding"].pop(name)
    return data


_F6_FIELDS = ("assets", "price_scaling", "holdings", "rebalancers", "rebalance_band", "risk_limit",
              "risk_sell_fraction", "risk_cooldown")
# Fields that exist only with a graph; at their defaults they are left out of run identities.
_GRAPH_FIELDS = ("valuers_myopic", "valuers_network", "composition", "attention", "observation_delay",
                 "weight_error_std", *_F6_FIELDS)
