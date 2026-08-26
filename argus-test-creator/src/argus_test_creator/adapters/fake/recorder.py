"""FakeRecorder — the basis of most automated tests and the built-in demo.

It is *controllable*: the UI (or a test) sends taps/keys through the recorder,
which applies them to the demo app and emits the corresponding raw events with
before/after captures. It can also emit synthetic pointer streams
(down/move*/up) to exercise normalization, and simulate disconnects.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from PIL.Image import Image

from argus_test_creator.core.errors import ScreenshotError, TargetConnectionError
from argus_test_creator.demo.movies import MoviesDemoApp
from argus_test_creator.models.capabilities import RecorderCapabilities, TargetProfile
from argus_test_creator.models.common import Point
from argus_test_creator.models.recording import RecordingEvent, RecordingEventType
from argus_test_creator.recording.adapter import EventSink, RecorderRegistry
from argus_test_creator.targets.catalog import PLATFORM_CAPABILITIES


class FakeRecorder:
    def __init__(self, target: TargetProfile, options: dict[str, Any] | None = None) -> None:
        self.target = target
        options = {**target.settings, **(options or {})}
        size = tuple(int(v) for v in options.get("screen_size", (1280, 720)))
        self.app = MoviesDemoApp(size=(size[0], size[1]),
                                 loading_frames=int(options.get("loading_frames", 1)))
        self._capabilities = PLATFORM_CAPABILITIES["fake"]
        self._connected = False
        self._sink: EventSink | None = None
        self._lock = threading.Lock()
        self.available = bool(options.get("available", True))
        self.fail_screenshot = False
        self._clock = datetime.now(UTC)
        self.screenshot_count = 0

    # -- RecorderAdapter -------------------------------------------------------------

    @property
    def capabilities(self) -> RecorderCapabilities:
        return self._capabilities

    def connect(self) -> None:
        if not self.available:
            raise TargetConnectionError(
                "Fake target is marked unavailable.",
                remediation="Set the target available and retry.",
            )
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._sink = None

    @property
    def connected(self) -> bool:
        return self._connected

    def screenshot(self) -> Image:
        if not self._connected:
            raise TargetConnectionError("Fake target is not connected.")
        if self.fail_screenshot:
            raise ScreenshotError("Fake screenshot failure injected.",
                                  remediation="Reconnect device and retry.")
        self.screenshot_count += 1
        return self.app.render()

    def last_screen_metadata(self) -> dict[str, Any]:
        return self.app.screen_metadata()

    def screen_size(self) -> tuple[int, int]:
        return self.app.size

    def start_recording(self, sink: EventSink) -> None:
        if not self._connected:
            raise TargetConnectionError("Connect the fake target before recording.")
        self._sink = sink

    def stop_recording(self) -> None:
        self._sink = None

    def describe_limitations(self) -> list[str]:
        return list(self._capabilities.limitations)

    # -- ControllableRecorder (the UI's "remote") ---------------------------------------

    def send_tap(self, x: int, y: int) -> None:
        with self._lock:
            self.app.tap(x, y)
            self._emit(RecordingEventType.CLICK, position=Point(x=x, y=y), button="left")

    def send_key(self, key: str) -> None:
        with self._lock:
            self.app.key(key)
            self._emit(RecordingEventType.KEY_PRESS, key=key)

    def send_text(self, text: str) -> None:
        for char in text:
            self.send_key("SPACE" if char == " " else char)

    def send_drag(self, x1: int, y1: int, x2: int, y2: int, *, moves: int = 5,
                  duration_ms: int = 600) -> None:
        """Emit a raw pointer stream so the normalizer has real work to do."""
        with self._lock:
            start = self._tick(0)
            self._emit(RecordingEventType.POINTER_DOWN, position=Point(x=x1, y=y1),
                       button="left", timestamp=start)
            for i in range(1, moves + 1):
                t = start + timedelta(milliseconds=duration_ms * i / (moves + 1))
                px = x1 + (x2 - x1) * i // (moves + 1)
                py = y1 + (y2 - y1) * i // (moves + 1)
                self._emit(RecordingEventType.POINTER_MOVE, position=Point(x=px, y=py),
                           timestamp=t, droppable=True)
            self._emit(RecordingEventType.POINTER_UP, position=Point(x=x2, y=y2), button="left",
                       timestamp=start + timedelta(milliseconds=duration_ms))
            self.app.tap(x2, y2) if x1 == x2 and y1 == y2 else None

    def send_long_press(self, x: int, y: int, duration_ms: int = 900) -> None:
        with self._lock:
            start = self._tick(0)
            self._emit(RecordingEventType.POINTER_DOWN, position=Point(x=x, y=y), button="left",
                       timestamp=start)
            self._emit(RecordingEventType.POINTER_UP, position=Point(x=x, y=y), button="left",
                       timestamp=start + timedelta(milliseconds=duration_ms))

    def simulate_disconnect(self) -> None:
        self._connected = False
        self.available = False

    def simulate_reconnect(self) -> None:
        self.available = True
        self._connected = True

    # -- internals ---------------------------------------------------------------------

    def _tick(self, ms: int) -> datetime:
        self._clock = max(self._clock + timedelta(milliseconds=ms), datetime.now(UTC))
        return self._clock

    def _emit(
        self,
        event_type: RecordingEventType,
        *,
        droppable: bool = False,
        timestamp: datetime | None = None,
        **fields: Any,
    ) -> None:
        if self._sink is None:
            return
        event = RecordingEvent(
            event_type=event_type,
            timestamp=timestamp or self._tick(50),
            metadata={"screen": self.app.state.screen},
            **fields,
        )
        self._sink.push(event, droppable=droppable)


def register(registry: RecorderRegistry) -> None:
    registry.register("fake", FakeRecorder)
