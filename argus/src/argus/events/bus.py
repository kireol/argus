"""Minimal synchronous publish/subscribe event bus."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from argus.events.events import Event

E = TypeVar("E", bound=Event)

Handler = Callable[[Event], None]

logger = logging.getLogger("argus.events")


class EventBus:
    """Dispatches events to subscribers.

    Subscribers may register for a specific event type or for all events.
    A failing subscriber never breaks the test run — errors are logged and
    dispatch continues.
    """

    def __init__(self) -> None:
        self._subscribers: dict[type[Event] | None, list[Handler]] = {}

    def subscribe(self, handler: Handler, event_type: type[Event] | None = None) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, handler: Handler, event_type: type[Event] | None = None) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, event: Event) -> None:
        handlers = list(self._subscribers.get(type(event), []))
        handlers.extend(self._subscribers.get(None, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - subscriber errors must not break runs
                logger.exception("Event subscriber failed for %s", type(event).__name__)
