"""Sequential one-asset DES integrating strategies through the public K1 gateway only."""

from __future__ import annotations

from bisect import bisect_right

import numpy as np

from marketsim.agents.herding import HerdingNetwork
from marketsim.agents.strategies import (
    OrderIntent,
    make_order,
    maker_orders,
    market_price,
    noise_signal,
    trend_signal,
    valuer_signal,
)
from marketsim.experiments.config import RunConfig
from marketsim.market import Gateway, make_exchange
from marketsim.market.api import TimeInForce
from marketsim.sim.population import make_population
from marketsim.sim.recorder import Recorder
from marketsim.sim.scheduler import Scheduler
from marketsim.sim.settings import AgentSettings


class Simulation:
    def __init__(self, config: RunConfig, settings: AgentSettings, seed: int, external_levels: dict) -> None:
        self.config, self.settings = config, settings
        population_seed, value_seed, network_seed, wake_seed, decision_seed = np.random.SeedSequence(seed).spawn(5)
        self.population = make_population(settings, np.random.default_rng(population_seed))
        n = len(self.population.kinds)
        self.investors = settings.valuers + settings.trend + settings.noise
        self._wake_rng = [np.random.default_rng(s) for s in wake_seed.spawn(n)]
        self._decision_rng = [np.random.default_rng(s) for s in decision_seed.spawn(n)]
        errors = np.random.default_rng(value_seed).normal(0, settings.valuation_std, settings.valuers)
        self.valuations = settings.initial_price_ticks * (1 + np.clip(errors, -0.9, 0.9))
        self.network = HerdingNetwork(
            self.investors, settings.herding.neighbors, settings.herding.ttl,
            np.random.default_rng(network_seed),
        )
        self.gateway = Gateway(
            make_exchange(config.exchange_backend, 1), self.population.cash, self.population.shares,
        )
        self.scheduler = Scheduler()
        self.recorder = Recorder()
        self._open: list[set[int]] = [set() for _ in range(n)]
        self._owners: dict[int, int] = {}
        self._history_times = [0.0]
        self._history_prices = [settings.initial_price_ticks]
        self._external_levels = external_levels
        self.invariant_checks = 0

    def _check(self) -> None:
        self.gateway.check_invariants()
        self.invariant_checks += 1

    def _next_wake(self, agent: int, now: float) -> None:
        s = self.settings
        rate = s.market_maker_rate if agent >= self.investors else s.decision_rate
        interval = float(self._wake_rng[agent].exponential(1 / rate))
        time = max(now + interval, float(np.nextafter(now, np.inf)))
        if time < self.config.n_sessions:
            self.scheduler.schedule(time, "wake", agent)

    def _withdraw(self, agent: int, order_id: int, now: float, kind: str) -> None:
        if kind == "order_expired":
            remaining = self.gateway.expire(order_id, now)
        else:
            remaining = self.gateway.cancel(agent, order_id, now)
        self._open[agent].discard(order_id)
        if remaining:
            self.recorder.event(now, kind, {"order_id": order_id, "agent": agent, "remaining_qty": remaining})

    def _place(self, agent: int, intent: OrderIntent, now: float) -> None:
        r = self.gateway.place(agent, 0, intent.side, intent.qty, intent.limit, intent.tif, now)
        self.recorder.order(now, agent, intent.side, intent.qty, intent.limit, intent.tif, r)
        self._owners[r.order_id] = agent
        if r.resting_qty:
            self._open[agent].add(r.order_id)
            if intent.tif == TimeInForce.GTD:
                self.scheduler.schedule(now + self.settings.order_lifetime, "expire", (agent, r.order_id))
        completed = self.recorder.fills(now, self.gateway.drain_fills())
        for order_id in completed:
            self._open[self._owners[order_id]].discard(order_id)

    def _wake(self, agent: int, now: float) -> None:
        s = self.settings
        # The maker uses the visible center before removing its own two quotes.
        price = market_price(self.gateway.market(), s.initial_price_ticks)
        for order_id in sorted(self._open[agent]):
            self._withdraw(agent, order_id, now, "order_cancelled")
        portfolio = self.gateway.portfolio(agent)
        kind = self.population.kinds[agent]
        if kind == "market_maker":
            for intent in maker_orders(price, portfolio, s.market_maker_shares, s):
                self._place(agent, intent, now)
        else:
            # Other agents observe the book after cancellation, as specified by F4.
            price = market_price(self.gateway.market(), s.initial_price_ticks)
            if kind == "valuer":
                own = valuer_signal(float(self.valuations[agent]), price, s.gamma)
            elif kind == "trend" and s.ablation != "no_trend":
                index = max(0, bisect_right(self._history_times, now - s.trend_lookback) - 1)
                own = trend_signal(price, self._history_prices[index], s.trend_scale)
            else:
                own = noise_signal(self._decision_rng[agent])
            h = 0.0 if s.ablation == "no_herding" else s.herding.h
            herd, signal = self.network.mix(agent, own, now, h)
            direction = int(np.sign(signal)) if abs(signal) > s.threshold else 0
            self.network.publish(agent, direction, now)
            self.recorder.rows["signals"].append({
                "t": now, "agent": agent, "asset": 0, "own_signal": own,
                "herd_signal": herd, "final_signal": signal,
            })
            intent = make_order(signal, price, self.gateway.market().top(0), portfolio, s, self._decision_rng[agent])
            if intent is not None:
                self._place(agent, intent, now)
        if s.debug:
            self._check()
        self._next_wake(agent, now)

    def resources(self) -> dict:
        """Snapshot totals from public portfolio views, using Python integers for sums."""
        portfolios = [self.gateway.portfolio(i) for i in range(len(self.population.kinds))]
        return {
            "cash_cents": sum(p.cash_available + p.cash_reserved for p in portfolios),
            "shares": sum(int(p.shares_available[0]) + int(p.shares_reserved[0]) for p in portfolios),
            "cash_reserved": sum(p.cash_reserved for p in portfolios),
            "shares_reserved": sum(int(p.shares_reserved[0]) for p in portfolios),
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
                self.recorder.event(now, "session_start", {"session": event.payload, **self._external_levels})
                if event.payload + 1 < cfg.n_sessions:
                    self.scheduler.schedule(now + 1, "session", event.payload + 1)
            elif event.kind == "sample":
                self.recorder.snapshot(now, self.gateway.market(), self.valuations, self.settings.initial_price_ticks)
                self._history_times.append(now)
                self._history_prices.append(market_price(self.gateway.market(), self.settings.initial_price_ticks))
                next_time = (event.payload + 1) * cfg.sample_dt
                if next_time < cfg.n_sessions:
                    self.scheduler.schedule(next_time, "sample", event.payload + 1)
        end = float(cfg.n_sessions)
        self.recorder.snapshot(end, self.gateway.market(), self.valuations, self.settings.initial_price_ticks)
        for agent, orders in enumerate(self._open):
            for order_id in sorted(orders):
                self._withdraw(agent, order_id, end, "run_end_cancelled")
        self._check()
