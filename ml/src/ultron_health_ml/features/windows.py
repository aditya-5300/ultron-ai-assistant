"""Time bounds and evidence checks shared by every feature family."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Generic, TypeVar

import numpy as np

from ultron_health_ml.features.schema import WindowQuality

MICROSECONDS = 1_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_TICK = timedelta(microseconds=1)
Value = TypeVar("Value", float, tuple[float, float])


def timestamp_ticks(value: datetime) -> int:
    return (value - _EPOCH) // _TICK


@dataclass
class History(Generic[Value]):
    times: list[int] = field(default_factory=list)
    values: list[Value] = field(default_factory=list)
    floor: int | None = None

    def clear(self, floor: int):
        self.times.clear()
        self.values.clear()
        self.floor = floor

    def append(self, time: int, value: Value):
        if self.times and time <= self.times[-1]:
            raise ValueError("history observations must be unique and chronological")
        self.times.append(time)
        self.values.append(value)

    def prune(self, cutoff: int):
        start = bisect_left(self.times, cutoff)
        if start:
            del self.times[:start]
            del self.values[:start]

    def bounds(self, end: int, duration: int, *, before: int | None = None) -> slice:
        start = bisect_left(self.times, end - duration * MICROSECONDS)
        stop = bisect_right(self.times, end) if before is None else bisect_left(self.times, min(end + 1, before))
        return slice(start, max(start, stop))


def assess(times: np.ndarray, gaps: np.ndarray, bounds: slice, duration: int,
           minimum_observations: int, minimum_coverage: float) -> WindowQuality:
    count = bounds.stop - bounds.start
    span = (int(times[bounds.stop - 1]) - int(times[bounds.start])) / MICROSECONDS if count > 1 else 0.0
    coverage = span / duration
    largest_gap = float(gaps[bounds.start:bounds.stop - 1].max()) / MICROSECONDS if count > 1 else None
    return WindowQuality(count >= minimum_observations and coverage >= minimum_coverage,
                         count, coverage, largest_gap)
