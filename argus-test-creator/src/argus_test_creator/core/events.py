"""Typed, thread-safe event bus.

Services publish domain events (RecordingStarted, StepAdded, ...); the UI and
other services subscribe. Publishing never raises because of a subscriber —
subscriber exceptions are collected and reported through ``on_error``.

The bus is deliberately synchronous: workers publish from background threads
and the UI layer marshals to the GUI thread (see ``ui.bridge``).
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

E = TypeVar("E", bound="Event")
Handler = Callable[[Any], None]


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base event; subclasses are plain frozen dataclasses."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class Subscription:
    """Handle returned by ``subscribe``; call ``cancel()`` to unsubscribe."""

    def __init__(self, bus: EventBus, event_type: type[Event], handler: Handler) -> None:
        self._bus = bus
        self._event_type = event_type
        self._handler = handler

    def cancel(self) -> None:
        self._bus.unsubscribe(self._event_type, self._handler)


class EventBus:
    def __init__(self, on_error: Callable[[Event, BaseException], None] | None = None) -> None:
        self._handlers: dict[type[Event], list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()
        self._on_error = on_error
        self.errors: list[tuple[Event, BaseException]] = []

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Subscription:
        with self._lock:
            self._handlers[event_type].append(handler)
        return Subscription(self, event_type, handler)

    def subscribe_all(self, handler: Callable[[Event], None]) -> Subscription:
        return self.subscribe(Event, handler)

    def unsubscribe(self, event_type: type[Event], handler: Handler) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def publish(self, event: Event) -> None:
        with self._lock:
            targets: list[Handler] = []
            for cls in type(event).__mro__:
                if cls is object:
                    continue
                targets.extend(self._handlers.get(cls, ()))
        for handler in targets:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 — a bad subscriber must not break publishers
                self.errors.append((event, exc))
                if self._on_error is not None:
                    self._on_error(event, exc)

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()
