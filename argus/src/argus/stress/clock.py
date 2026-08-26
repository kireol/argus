"""Injectable clocks.

The engine measures deadlines with :meth:`Clock.monotonic` and pauses with
:meth:`Clock.sleep` — never ``time.time`` for deadlines. Tests use
:class:`FakeClock` so hours-long policies run in milliseconds.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def now(self) -> datetime:
        """Wall-clock time (for human-readable timestamps only)."""
        ...


class MonotonicClock:
    """The real clock."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """A clock that only advances when told (or when something sleeps)."""

    def __init__(self, start: float = 1000.0, *, epoch: datetime | None = None) -> None:
        self._now = float(start)
        self._start = float(start)
        self._epoch = epoch or datetime(2026, 1, 1, tzinfo=UTC)
        self._lock = threading.Lock()
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self._now += max(seconds, 0.0)

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += max(seconds, 0.0)

    def now(self) -> datetime:
        from datetime import timedelta

        return self._epoch + timedelta(seconds=self.monotonic() - self._start)


__all__ = ["Clock", "FakeClock", "MonotonicClock"]
