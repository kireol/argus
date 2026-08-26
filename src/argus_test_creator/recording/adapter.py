"""RecorderAdapter — the platform-neutral recording interface.

An adapter observes a target (browser, desktop, Android, fake...) and pushes
:class:`RecordingEvent` objects into an :class:`EventSink`. It never touches
the authoring model or the UI. Adapters register through the
``argus_test_creator.recorders`` entry-point group so platforms can be added
without changing the core.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from importlib import metadata
from typing import Any, Protocol, runtime_checkable

from PIL.Image import Image

from argus_test_creator.core.errors import RecordingError
from argus_test_creator.models.capabilities import RecorderCapabilities, TargetProfile
from argus_test_creator.models.recording import RecordingEvent


class EventSink:
    """Bounded queue between recorder threads and the session (backpressure).

    High-frequency events (pointer moves) are dropped first when the queue is
    full; discrete events (clicks, keys) block briefly instead of being lost.
    """

    def __init__(self, maxsize: int = 2048) -> None:
        self._queue: queue.Queue[RecordingEvent | None] = queue.Queue(maxsize=maxsize)
        self.dropped = 0
        self._closed = threading.Event()
        self._paused = threading.Event()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def push(self, event: RecordingEvent, *, droppable: bool = False) -> bool:
        if self._closed.is_set() or self._paused.is_set():
            return False
        try:
            self._queue.put(event, block=not droppable, timeout=None if droppable else 2.0)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def pop(self, timeout: float = 0.2) -> RecordingEvent | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def task_done(self) -> None:
        self._queue.task_done()

    def join(self) -> None:
        """Block until every pushed event has been processed."""
        self._queue.join()

    def close(self) -> None:
        self._closed.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def qsize(self) -> int:
        return self._queue.qsize()


@runtime_checkable
class RecorderAdapter(Protocol):
    """What every platform recorder implements."""

    target: TargetProfile

    @property
    def capabilities(self) -> RecorderCapabilities: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    @property
    def connected(self) -> bool: ...

    def screenshot(self) -> Image:
        """Full-resolution capture of the current screen."""
        ...

    def screen_size(self) -> tuple[int, int]: ...

    def start_recording(self, sink: EventSink) -> None:
        """Begin pushing events into ``sink`` (from the adapter's own thread)."""
        ...

    def stop_recording(self) -> None: ...

    def describe_limitations(self) -> list[str]: ...


class ControllableRecorder(Protocol):
    """Optional: targets whose input the Creator must *send* (Roku remote, fake demo)."""

    def send_key(self, key: str) -> None: ...

    def send_tap(self, x: int, y: int) -> None: ...

    def send_text(self, text: str) -> None: ...


RecorderFactory = Callable[[TargetProfile, dict[str, Any]], RecorderAdapter]


class RecorderRegistry:
    """Maps adapter kinds to factories; loads ``argus_test_creator.recorders`` entry points."""

    def __init__(self) -> None:
        self._factories: dict[str, RecorderFactory] = {}
        self._loaded = False

    def register(self, kind: str, factory: RecorderFactory) -> None:
        self._factories[kind] = factory

    def kinds(self) -> list[str]:
        self._load_entry_points()
        return sorted(self._factories)

    def create(self, target: TargetProfile, options: dict[str, Any] | None = None) -> RecorderAdapter:  # noqa: E501
        self._load_entry_points()
        factory = self._factories.get(target.adapter)
        if factory is None:
            raise RecordingError(
                f"No recorder adapter for {target.adapter!r}.",
                remediation=f"Available adapters: {', '.join(self.kinds()) or '<none>'}.",
            )
        return factory(target, options or {})

    def _load_entry_points(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            entry_points = list(metadata.entry_points(group="argus_test_creator.recorders"))
        except Exception:  # noqa: BLE001 - metadata can fail in frozen apps
            entry_points = []
        for entry_point in entry_points:
            if entry_point.name in self._factories:
                continue
            try:
                entry_point.load()(self)
            except Exception:  # noqa: BLE001 - optional dependency missing; skip adapter
                continue
