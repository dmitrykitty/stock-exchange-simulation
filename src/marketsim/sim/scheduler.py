"""Stable discrete-event scheduling; no wall clock or random state."""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import count


@dataclass(order=True, frozen=True)
class Event:
    time: float
    seq: int
    kind: str = field(compare=False)
    payload: object = field(default=None, compare=False)


class Scheduler:
    def __init__(self) -> None:
        self.now = 0.0
        self._queue: list[Event] = []
        self._seq = count()

    def schedule(self, time: float, kind: str, payload: object = None) -> None:
        """Insert an event; equal-time events execute in insertion order."""
        if not math.isfinite(time) or time < self.now:
            raise ValueError("event time must be finite and not earlier than now")
        heapq.heappush(self._queue, Event(float(time), next(self._seq), kind, payload))

    def before(self, horizon: float) -> Iterator[Event]:
        """Yield events strictly before the horizon, including newly scheduled ones."""
        while self._queue and self._queue[0].time < horizon:
            event = heapq.heappop(self._queue)
            self.now = event.time
            yield event
