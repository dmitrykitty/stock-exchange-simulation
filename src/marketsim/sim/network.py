"""Multi-asset DES on a dependency graph (F5, F6): valuers update their valuations from the graph (contract K2).

Every company of the graph is an asset, in ``graph.companies()`` order. Investors share one wallet across assets
and trade the assets they follow; one or more market makers quote each asset. Valuers follow the rule of
docs/model/model_description.md, section 4.2:

    ln V_a,i(t) = ln V_a,i(t') + sum_j w^_a,ij [ln P^obs_a,j(t) - ln P^obs_a,j(t')]

Summing these increments over an agent's decisions telescopes, so the valuation at any moment is

    ln V_a,i(t) = ln V_a,i(0) + sum_j w^_a,ij [ln P^obs_a,j(t) - ln P^obs_a,j(0)],

which is what is computed here (same result, no per-decision state). Parents and weights: myopic valuers use
``w_direct`` (external parents only), network valuers ``w_full`` (also companies). ``w^ = w (1 + eta)`` with a
fixed personal error eta per agent, asset and parent. ``P^obs(t) = P(t - d_a)``: for an external node, the level
of the session containing ``t - d_a``; for a company, the midpoint on this exchange (last trade, then start price,
when a side is empty), taken live when ``d_a = 0`` and otherwise from the last book snapshot not after ``t - d_a``.

F6 adds, all off by default: per-asset parameters scaled by the start price, shares held only by the investors
that trade an asset, rebalancers (funds that keep target portfolio weights) and a risk limit (an investor in a
drawdown sells its positions proportionally and stays out for a cooldown).

All randomness comes from the run seed; the same config and seed give the same log.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, replace
from decimal import Decimal

import numpy as np

from marketsim.agents.herding import HerdingNetwork, susceptibility
from marketsim.agents.strategies import (
    OrderIntent,
    clip_signal,
    make_order,
    maker_orders,
    market_price,
    noise_signal,
    rebalance_order,
    risk_sale,
    target_order,
    trend_signal,
    valuer_signal,
)
from marketsim.experiments.config import ConfigError, RunConfig
from marketsim.graph.api import DependencyGraph
from marketsim.market import Gateway, make_exchange
from marketsim.market.api import Report, Status, TimeInForce
from marketsim.sim.recorder import Recorder
from marketsim.sim.scheduler import Scheduler
from marketsim.sim.settings import AgentSettings

INVESTOR_KINDS = ("valuer_myopic", "valuer_network", "trend", "noise")
_REJECTED = (Status.REJECTED_FUNDS, Status.REJECTED_INVALID, Status.REJECTED_SELF_TRADE)


@dataclass
class NetworkPopulation:
    kinds: list[str]
    funds: np.ndarray            # bool per agent (investors only can be funds)
    cash: np.ndarray             # int64 per agent
    shares: np.ndarray           # int64 (agents, assets)
    maker_asset: list[int]       # asset of every market maker, in maker order
    follows: np.ndarray          # bool (agents, assets): assets an agent trades


def _allocate(total: int, indices: np.ndarray, out: np.ndarray) -> None:
    amount, remainder = divmod(total, len(indices))
    out[indices] = amount
    out[indices[:remainder]] += 1


def make_network_population(
    settings: AgentSettings, n_assets: int, rng: np.random.Generator, attention_rng: np.random.Generator,
    total_shares: list[int] | None = None, maker_shares: list[int] | None = None,
) -> NetworkPopulation:
    """Investor groups, fund quotas and integer resources as in F4, with shares in every asset.

    ``total_shares`` / ``maker_shares``: per-asset amounts (default: the settings' values for every asset).
    Rebalancers (F6) come after the four investor groups and are all funds.
    """
    s = settings
    total_shares = total_shares or [s.total_shares] * n_assets
    maker_shares = maker_shares or [s.market_maker_shares] * n_assets
    counts = (s.valuers_myopic, s.valuers_network, s.trend, s.noise)
    kinds = [kind for kind, size in zip(INVESTOR_KINDS, counts) for _ in range(size)]
    discretionary = len(kinds)
    kinds += ["rebalancer"] * s.rebalancers
    n = len(kinds)
    maker_asset = [asset for asset in range(n_assets) for _ in range(s.market_makers_per_asset)]
    kinds += ["market_maker"] * len(maker_asset)
    funds = np.zeros(len(kinds), dtype=bool)
    # Largest-remainder quotas keep the fund strategy mix proportional and independent of the seed and C.
    quotas = [size * s.n_funds // discretionary for size in counts]
    remainders = [size * s.n_funds % discretionary for size in counts]
    for index in sorted(range(len(counts)), key=lambda i: (-remainders[i], i))[:s.n_funds - sum(quotas)]:
        quotas[index] += 1
    offset = 0
    for size, quota in zip(counts, quotas):
        if quota:
            funds[offset + rng.choice(size, quota, replace=False)] = True
        offset += size
    funds[discretionary:n] = True
    fund_indices = np.flatnonzero(funds)
    retail_indices = np.flatnonzero(~funds[:n])
    follows = np.zeros((len(kinds), n_assets), dtype=bool)
    if s.attention == 0 or s.attention >= n_assets:
        follows[:n] = True
    else:
        for agent in range(n):
            follows[agent, np.sort(attention_rng.choice(n_assets, s.attention, replace=False))] = True
    for k, asset in enumerate(maker_asset):
        follows[n + k, asset] = True
    cash = np.zeros(len(kinds), dtype=np.int64)
    shares = np.zeros((len(kinds), n_assets), dtype=np.int64)
    fund_cash = int(Decimal(str(s.concentration)) * s.total_cash_cents)
    _allocate(fund_cash, fund_indices, cash)
    _allocate(s.total_cash_cents - fund_cash, retail_indices, cash)
    for asset in range(n_assets):
        fund_shares = int(Decimal(str(s.concentration)) * total_shares[asset])
        fund_holders, retail_holders = fund_indices, retail_indices
        if s.holdings == "followed":
            fund_holders = fund_indices[follows[fund_indices, asset]]
            retail_holders = retail_indices[follows[retail_indices, asset]]
            if not len(fund_holders) and not len(retail_holders):
                raise ConfigError(f"no investor follows asset {asset}; raise attention or the investor count")
            if not len(fund_holders):
                fund_shares = 0
            elif not len(retail_holders):
                fund_shares = total_shares[asset]
        if fund_shares:
            _allocate(fund_shares, fund_holders, shares[:, asset])
        if total_shares[asset] - fund_shares:
            _allocate(total_shares[asset] - fund_shares, retail_holders, shares[:, asset])
    cash[n:] = s.market_maker_cash_cents
    for k, asset in enumerate(maker_asset):
        shares[n + k, asset] = maker_shares[asset]
    return NetworkPopulation(kinds, funds, cash, shares, maker_asset, follows)


class NetworkSimulation:
    def __init__(
        self, config: RunConfig, settings: AgentSettings, seed: int, graph: DependencyGraph,
        external: np.ndarray, external_names: list[str], reference: np.ndarray,
    ) -> None:
        """``external``: levels (n_sessions, n_external) in ``external_names`` order; ``reference``: R in ticks
        (n_sessions, n_assets). Both already cover every session of the run."""
        self.config, self.settings, self.graph = config, settings, graph
        s = settings
        self.assets = graph.companies()
        n_assets = len(self.assets)
        self.start_ticks = [round(graph.nodes[c].start_price * 100) for c in self.assets]
        # Children of a SeedSequence depend only on their position, so the ninth leaves the first eight (F5) as is.
        (population_seed, value_seed, network_seed, wake_seed, decision_seed, herd_seed, attention_seed,
         weight_seed, risk_seed) = np.random.SeedSequence(seed).spawn(9)
        # Per-asset parameters. With price_scaling the settings describe a stock at initial_price_ticks.
        self.asset_settings = [s] * n_assets
        self.asset_total_shares = [s.total_shares] * n_assets
        self.asset_maker_shares = [s.market_maker_shares] * n_assets
        if s.price_scaling:
            factors = [start / s.initial_price_ticks for start in self.start_ticks]
            self.asset_settings = [replace(
                s, market_maker_qty=max(1, round(s.market_maker_qty / f)),
                market_maker_spread_ticks=max(2, round(s.market_maker_spread_ticks * f)),
                inventory_skew_ticks=s.inventory_skew_ticks * f * f,
            ) for f in factors]
            self.asset_total_shares = [round(s.total_shares / f) for f in factors]
            self.asset_maker_shares = [round(s.market_maker_shares / f) for f in factors]
        self.population = make_network_population(
            s, n_assets, np.random.default_rng(population_seed), np.random.default_rng(attention_seed),
            self.asset_total_shares, self.asset_maker_shares,
        )
        n = len(self.population.kinds)
        self.investors = s.investors
        self._wake_rng = [np.random.default_rng(x) for x in wake_seed.spawn(n)]
        self._decision_rng = [np.random.default_rng(x) for x in decision_seed.spawn(n)]
        self.followed = [np.flatnonzero(self.population.follows[a]).tolist() for a in range(n)]
        self.network = HerdingNetwork(
            self.investors, s.herding.neighbors, s.herding.ttl, np.random.default_rng(network_seed),
            n_assets=n_assets, follows=self.population.follows[:self.investors],
        )
        hs = s.herding
        self.herd_h = susceptibility(self.investors, hs.h, hs.followers, hs.h_std, np.random.default_rng(herd_seed))
        rebalancers = [a for a in range(self.investors) if self.population.kinds[a] == "rebalancer"]
        self.herd_h[rebalancers] = 0.0  # a mandate, not an opinion: rebalancers do not herd
        self.gateway = Gateway(make_exchange(config.exchange_backend, n_assets), self.population.cash,
                               self.population.shares)
        # Top of book and market price per asset, valid until an order of that asset is placed or withdrawn.
        self._tops: list = [None] * n_assets
        self._prices: list[int | None] = [None] * n_assets
        self.scheduler = Scheduler()
        self.recorder = Recorder()
        self._open: list[set[int]] = [set() for _ in range(n)]
        self._owners: dict[int, int] = {}
        self._external = external
        self._external_names = external_names
        self._reference = reference
        self._history_times = [0.0]
        self._history_prices = [[p] for p in self.start_ticks]  # per asset, aligned with _history_times
        self.invariant_checks = 0
        self._init_valuers(np.random.default_rng(value_seed), np.random.default_rng(weight_seed))
        # Rebalancers keep the initial value weights of the assets they follow (the rest of the wealth is cash).
        self._targets: dict[int, dict[int, float]] = {}
        for agent in rebalancers:
            wealth = self._wealth(self.gateway.portfolio(agent))
            self._targets[agent] = {
                i: int(self.population.shares[agent, i]) * self.start_ticks[i] / wealth for i in self.followed[agent]
            }
        # Risk limit state per investor: running wealth peak, end of the cooldown, shares to keep per asset.
        self.risk_triggers = 0
        self._derisk_until = [-math.inf] * self.investors
        self._risk_floor: list[dict[int, int]] = [{} for _ in range(self.investors)]
        self._peak = ([self._wealth(self.gateway.portfolio(a)) for a in range(self.investors)]
                      if s.risk_limit > 0 else [])
        self.risk_limited = np.zeros(self.investors, dtype=bool)
        if s.risk_limit > 0:
            limited = round(s.risk_limit_share * self.investors)
            chosen = (np.arange(self.investors) if limited == self.investors else
                      np.random.default_rng(risk_seed).choice(self.investors, limited, replace=False))
            self.risk_limited[chosen] = True

    # ----- valuers -------------------------------------------------------------------------------------------
    def _init_valuers(self, value_rng: np.random.Generator, weight_rng: np.random.Generator) -> None:
        """Initial valuations, personal weights and delays; parents resolved once per (kind, asset)."""
        s, cfg = self.settings, self.config
        company_pos = {c: i for i, c in enumerate(self.assets)}
        external_pos = {name: k for k, name in enumerate(self._external_names)}
        hidden = set() if cfg.use_market_index else {"sp500"}
        self._parents: dict[tuple[str, int], list[tuple[str, int, float]]] = {}
        for kind, mode in (("valuer_myopic", "direct"), ("valuer_network", "full")):
            for i, company in enumerate(self.assets):
                items = []
                for parent, weight in self.graph.parents(company, mode).items():
                    if parent in hidden:
                        continue
                    if parent in company_pos:
                        items.append(("company", company_pos[parent], weight))
                    else:
                        items.append(("external", external_pos[parent], weight))
                self._parents[(kind, i)] = items
        n = len(self.population.kinds)
        self.delay = np.zeros(n)
        valuers = [a for a in range(self.investors) if self.population.kinds[a].startswith("valuer")]
        if s.observation_delay > 0:
            self.delay[valuers] = value_rng.uniform(0.0, 2 * s.observation_delay, len(valuers))
        # Per valuer and followed asset: ln V(0) and perceived weights, drawn in a fixed order.
        self._ln_v0: dict[tuple[int, int], float] = {}
        self._weights: dict[tuple[int, int], np.ndarray] = {}
        self._ln_v_last = np.full((n, len(self.assets)), np.nan)
        for agent in valuers:
            kind = self.population.kinds[agent]
            for i in self.followed[agent]:
                error = float(np.clip(value_rng.normal(0.0, s.valuation_std), -0.9, 0.9))
                self._ln_v0[(agent, i)] = math.log(self.start_ticks[i] * (1 + error))
                base = np.array([w for _, _, w in self._parents[(kind, i)]], dtype=float)
                if s.weight_error_std > 0 and len(base):
                    base = base * (1 + weight_rng.normal(0.0, s.weight_error_std, len(base)))
                self._weights[(agent, i)] = base
                self._ln_v_last[agent, i] = self._ln_v0[(agent, i)]

    def _session_of(self, t: float) -> int:
        return min(max(math.floor(t), 0), len(self._external) - 1)

    def _top(self, j: int):
        top = self._tops[j]
        if top is None:
            top = self._tops[j] = self.gateway.market().top(j)
        return top

    def _price(self, j: int) -> int:
        price = self._prices[j]
        if price is None:
            price = self._prices[j] = market_price(self.gateway.market(), self.start_ticks[j], j)
        return price

    def _observed_company(self, j: int, t: float, now: float) -> int:
        if t >= now:
            return self._price(j)
        index = max(0, bisect_right(self._history_times, t) - 1)
        return self._history_prices[j][index]

    def valuation(self, agent: int, asset: int, now: float) -> float:
        """Valuation in ticks of ``agent`` for ``asset`` at ``now`` (the telescoped F5 rule)."""
        kind = self.population.kinds[agent]
        seen_at = now - float(self.delay[agent])
        ln_v = self._ln_v0[(agent, asset)]
        weights = self._weights[(agent, asset)]
        for (source, j, _), w in zip(self._parents[(kind, asset)], weights):
            if source == "external":
                change = math.log(self._external[self._session_of(seen_at), j] / self._external[0, j])
            else:
                change = math.log(self._observed_company(j, seen_at, now) / self.start_ticks[j])
            ln_v += w * change
        self._ln_v_last[agent, asset] = ln_v
        return math.exp(ln_v)

    # ----- events --------------------------------------------------------------------------------------------
    def _check(self) -> None:
        self.gateway.check_invariants()
        self.invariant_checks += 1

    def _next_wake(self, agent: int, now: float) -> None:
        s = self.settings
        rate = s.market_maker_rate if agent >= self.investors else s.decision_rate
        interval = float(self._wake_rng[agent].exponential(1 / rate))
        time = max(now + interval, math.nextafter(now, math.inf))
        if time < self.config.n_sessions:
            self.scheduler.schedule(time, "wake", agent)

    def _withdraw(self, agent: int, order_id: int, now: float, kind: str) -> None:
        if kind == "order_expired":
            remaining = self.gateway.expire(order_id, now)
        else:
            remaining = self.gateway.cancel(agent, order_id, now)
        self._open[agent].discard(order_id)
        if remaining:
            # The order left a book; its asset is not tracked here, so every cached quote is dropped.
            self._tops = [None] * len(self.assets)
            self._prices = [None] * len(self.assets)
            self.recorder.event(now, kind, {"order_id": order_id, "agent": agent, "remaining_qty": remaining})

    def _place(self, agent: int, asset: int, intent: OrderIntent, now: float) -> Report:
        r = self.gateway.place(agent, asset, intent.side, intent.qty, intent.limit, intent.tif, now)
        self._tops[asset] = self._prices[asset] = None
        self.recorder.order(now, agent, intent.side, intent.qty, intent.limit, intent.tif, r, asset)
        self._owners[r.order_id] = agent
        if r.resting_qty:
            self._open[agent].add(r.order_id)
            if intent.tif == TimeInForce.GTD:
                self.scheduler.schedule(now + self.settings.order_lifetime, "expire", (agent, r.order_id))
        completed = self.recorder.fills(now, self.gateway.drain_fills())
        for order_id in completed:
            self._open[self._owners[order_id]].discard(order_id)
        return r

    def _wealth(self, portfolio) -> int:
        holdings = portfolio.shares_available + portfolio.shares_reserved
        # Zero holdings add nothing, so only held assets are priced.
        return portfolio.cash_available + portfolio.cash_reserved + sum(
            int(holdings[j]) * self._price(j) for j in np.flatnonzero(holdings)
        )

    def _derisking(self, agent: int, now: float) -> bool:
        """Risk limit (F6): True while the agent is selling after a drawdown beyond ``risk_limit``."""
        s = self.settings
        if now < self._derisk_until[agent]:
            return True
        portfolio = self.gateway.portfolio(agent)
        wealth = self._wealth(portfolio)
        if self._derisk_until[agent] > -math.inf:  # the cooldown is over: start again from the current wealth
            self._derisk_until[agent] = -math.inf
            self._peak[agent] = wealth
        self._peak[agent] = max(self._peak[agent], wealth)
        drawdown = 1 - wealth / self._peak[agent]
        if drawdown <= s.risk_limit:
            return False
        self._derisk_until[agent] = now + s.risk_cooldown
        self._risk_floor[agent] = {
            asset: math.floor((1 - s.risk_sell_fraction) * int(portfolio.shares_available[asset]))
            for asset in self.followed[agent]
        }
        self.risk_triggers += 1
        self.recorder.event(now, "risk_limit", {"agent": agent, "drawdown": round(drawdown, 6)})
        return True

    def _own_signal(self, agent: int, kind: str, asset: int, price: int, now: float) -> float:
        s = self.settings
        if kind.startswith("valuer"):
            return valuer_signal(self.valuation(agent, asset, now), price, s.gamma)
        if kind == "trend" and s.ablation != "no_trend":
            index = max(0, bisect_right(self._history_times, now - s.trend_lookback) - 1)
            return trend_signal(price, self._history_prices[asset][index], s.trend_scale)
        return noise_signal(self._decision_rng[agent])

    def _wake(self, agent: int, now: float) -> None:
        s = self.settings
        kind = self.population.kinds[agent]
        if kind == "market_maker":
            asset = self.population.maker_asset[agent - self.investors]
            # The maker uses the visible center before removing its own two quotes.
            price = self._price(asset)
            for order_id in sorted(self._open[agent]):
                self._withdraw(agent, order_id, now, "order_cancelled")
            portfolio = self.gateway.portfolio(agent)
            for intent in maker_orders(price, portfolio, self.asset_maker_shares[asset],
                                       self.asset_settings[asset], asset):
                self._place(agent, asset, intent, now)
        else:
            for order_id in sorted(self._open[agent]):
                self._withdraw(agent, order_id, now, "order_cancelled")
            h = 0.0 if s.ablation == "no_herding" else float(self.herd_h[agent])
            derisking = bool(self.risk_limited[agent]) and self._derisking(agent, now)
            # Followed assets are decided one after another; each decision sees the book and the wallet left by
            # the previous one, keeping the event-by-event semantics of the model.
            for asset in self.followed[agent]:
                price = self._price(asset)
                portfolio = self.gateway.portfolio(agent)
                if derisking:
                    # Sell down to the floor fixed when the limit was hit; no purchases during the cooldown.
                    excess = int(portfolio.shares_available[asset]) - self._risk_floor[agent].get(asset, 0)
                    own = -1.0 if excess > 0 else 0.0
                    herd, _ = self.network.mix(agent, own, now, h, asset)
                    signal = own
                    intent = risk_sale(price, excess, s) if excess > 0 else None
                elif kind == "rebalancer":
                    target_value = self._targets[agent][asset] * self._wealth(portfolio)
                    held = int(portfolio.shares_available[asset]) + int(portfolio.shares_reserved[asset])
                    deviation = (target_value - held * price) / target_value if target_value > 0 else 0.0
                    own = clip_signal(deviation)
                    herd, signal = self.network.mix(agent, own, now, h, asset)
                    intent = rebalance_order(deviation, target_value, price, self._top(asset), portfolio,
                                             self.asset_settings[asset], self._decision_rng[agent], asset)
                elif s.position_sizing == "target":
                    own = self._own_signal(agent, kind, asset, price, now)
                    herd, signal = self.network.mix(agent, own, now, h, asset)
                    # The neutral position is the initial number of shares: at a zero signal everybody wants what
                    # exists, whatever the price has done since the start.
                    held = int(portfolio.shares_available[asset]) + int(portfolio.shares_reserved[asset])
                    neutral = int(self.population.shares[agent, asset]) * price
                    gap = neutral * (1 + s.position_tilt * signal) - held * price
                    intent = None
                    if abs(signal) > s.threshold or abs(gap) > s.rebalance_band * neutral:
                        intent = target_order(gap, s.rho * self._wealth(portfolio), price, self._top(asset),
                                              portfolio, self.asset_settings[asset], self._decision_rng[agent], asset)
                else:
                    own = self._own_signal(agent, kind, asset, price, now)
                    herd, signal = self.network.mix(agent, own, now, h, asset)
                    intent = make_order(signal, price, self._top(asset), portfolio, self.asset_settings[asset],
                                        self._decision_rng[agent], asset, self._wealth(portfolio))
                self.recorder.rows["signals"].add(now, agent, asset, own, herd, signal)
                direction = 0
                if intent is not None:
                    report = self._place(agent, asset, intent, now)
                    if report.status not in _REJECTED:
                        direction = intent.side
                self.network.publish(agent, direction, now, asset)
        if s.debug:
            self._check()
        self._next_wake(agent, now)

    def _snapshot(self, now: float) -> None:
        n_assets = len(self.assets)
        v_mean = np.empty(n_assets)
        v_std = np.zeros(n_assets)
        valuers = self._ln_v_last[:self.investors]
        for i in range(n_assets):
            column = valuers[:, i]
            column = column[~np.isnan(column)]
            if len(column):
                values = np.exp(column)
                v_mean[i], v_std[i] = values.mean(), values.std()
            else:
                v_mean[i] = self.start_ticks[i]
        session = self._session_of(now)
        self.recorder.snapshot_assets(now, self.gateway.market(), v_mean, v_std, self._reference[session])

    def resources(self) -> dict:
        """Totals from public portfolio views, per asset and overall, using Python integers."""
        portfolios = [self.gateway.portfolio(i) for i in range(len(self.population.kinds))]
        n_assets = len(self.assets)
        by_asset = [sum(int(p.shares_available[j]) + int(p.shares_reserved[j]) for p in portfolios)
                    for j in range(n_assets)]
        return {
            "cash_cents": sum(p.cash_available + p.cash_reserved for p in portfolios),
            "shares": sum(by_asset),
            "shares_by_asset": by_asset,
            "cash_reserved": sum(p.cash_reserved for p in portfolios),
            "shares_reserved": sum(int(p.shares_reserved[j]) for p in portfolios for j in range(n_assets)),
        }

    def execute(self) -> None:
        """Process events in time/sequence order; cancel remaining orders at termination."""
        cfg = self.config
        self._check()
        self.scheduler.schedule(0.0, "session", 0)
        for agent in range(self.investors, len(self.population.kinds)):
            self.scheduler.schedule(0.0, "wake", agent)
        for agent in range(self.investors):
            self._next_wake(agent, 0.0)
        self.scheduler.schedule(0.0, "sample", 0)
        for event in self.scheduler.before(cfg.n_sessions):
            now = event.time
            if event.kind == "wake":
                self._wake(event.payload, now)
            elif event.kind == "expire":
                agent, order_id = event.payload
                self._withdraw(agent, order_id, now, "order_expired")
                if self.settings.debug:
                    self._check()
            elif event.kind == "session":
                session = event.payload
                levels = {name: float(self._external[min(session, len(self._external) - 1), k])
                          for k, name in enumerate(self._external_names)}
                self.recorder.event(now, "session_start", {"session": session, **levels})
                if session + 1 < cfg.n_sessions:
                    self.scheduler.schedule(now + 1, "session", session + 1)
            elif event.kind == "sample":
                self._snapshot(now)
                self._history_times.append(now)
                for j in range(len(self.assets)):
                    self._history_prices[j].append(self._price(j))
                next_time = (event.payload + 1) * cfg.sample_dt
                if next_time < cfg.n_sessions:
                    self.scheduler.schedule(next_time, "sample", event.payload + 1)
        end = float(cfg.n_sessions)
        self._snapshot(end)
        for agent, orders in enumerate(self._open):
            for order_id in sorted(orders):
                self._withdraw(agent, order_id, end, "run_end_cancelled")
        self._check()
