"""Marshal EventBus events and worker Jobs onto the Qt GUI thread."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal

from argus_test_creator.core.events import Event, EventBus
from argus_test_creator.core.workers import Job


class EventBridge(QObject):
    """Subscribes to the bus (any thread) and re-emits ``event_received`` on the GUI thread."""

    event_received = Signal(object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._subscription = bus.subscribe_all(self._on_event)
        self._handlers: dict[type[Event], list[Callable[[Any], None]]] = {}
        self.event_received.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    def _on_event(self, event: Event) -> None:
        self.event_received.emit(event)

    def on(self, event_type: type[Event], handler: Callable[[Any], None]) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def _dispatch(self, event: object) -> None:
        for cls in type(event).__mro__:
            for handler in self._handlers.get(cls, ()):
                handler(event)

    def close(self) -> None:
        self._subscription.cancel()


class JobWatcher(QObject):
    """Emits ``finished(result)`` / ``failed(exception)`` on the GUI thread when a Job ends."""

    finished = Signal(object)
    failed = Signal(object)

    def __init__(self, job: Job[Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.job = job
        job.on_done(self._done)

    def _done(self, job: Job[Any]) -> None:
        if job.future.cancelled():
            return
        exc = job.future.exception()
        if exc is not None:
            self.failed.emit(exc)
        else:
            self.finished.emit(job.future.result())


def watch(job: Job[Any], on_result: Callable[[Any], None],
          on_error: Callable[[BaseException], None], parent: QObject) -> JobWatcher:
    watcher = JobWatcher(job, parent)
    watcher.finished.connect(on_result, Qt.ConnectionType.QueuedConnection)
    watcher.failed.connect(on_error, Qt.ConnectionType.QueuedConnection)
    return watcher
