"""AndroidRecordingDiagnostics — thread-safe counters for the developer diagnostics view.

Counters are incremented from the getevent reader thread and read (as an
immutable snapshot) from the UI on a throttled timer. Nothing here holds raw
events; only numbers and the last few strings.
"""

from __future__ import annotations

import collections
import threading
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiagnosticsSnapshot:
    adb: str = "unknown"
    serial: str = ""
    model: str = ""
    android_version: str = ""
    input_device: str = ""
    input_device_name: str = ""
    touchscreen: bool = False
    screen_size: tuple[int, int] = (0, 0)
    rotation: int = 0
    stream_alive: bool = False
    raw_events: int = 0
    recognized: int = 0
    ignored: int = 0
    unknown: int = 0
    malformed: int = 0
    dropped: int = 0
    current_action: str = ""
    last_error: str = ""
    recent_gestures: tuple[str, ...] = ()

    def render(self) -> str:
        check = "✓" if self.touchscreen else "✗"
        w, h = self.screen_size
        return "\n".join([
            "Android Recording Diagnostics", "",
            f"ADB: {self.adb}",
            f"Device: {self.serial}" + (f" ({self.model}, Android {self.android_version})"
                                        if self.model else ""),
            f"Input device: {self.input_device or '—'}"
            + (f" ({self.input_device_name})" if self.input_device_name else ""),
            f"Touchscreen: {check}",
            f"Resolution: {w} × {h}  rotation {self.rotation * 90}°",
            f"getevent stream: {'running' if self.stream_alive else 'stopped'}", "",
            f"Raw events: {self.raw_events:,}",
            f"Recognized actions: {self.recognized:,}",
            f"Ignored events: {self.ignored:,}",
            f"Unknown events: {self.unknown:,}",
            f"Malformed lines: {self.malformed:,}",
            f"Dropped (backpressure): {self.dropped:,}",
            f"Current action: {self.current_action or '—'}",
            *( [f"Last error: {self.last_error}"] if self.last_error else [] ),
        ])


@dataclass
class AndroidRecordingDiagnostics:
    adb: str = "unknown"
    serial: str = ""
    model: str = ""
    android_version: str = ""
    input_device: str = ""
    input_device_name: str = ""
    touchscreen: bool = False
    screen_size: tuple[int, int] = (0, 0)
    rotation: int = 0
    stream_alive: bool = False
    raw_events: int = 0
    recognized: int = 0
    ignored: int = 0
    unknown: int = 0
    malformed: int = 0
    dropped: int = 0
    current_action: str = ""
    last_error: str = ""
    _recent: collections.deque[str] = field(default_factory=lambda: collections.deque(maxlen=10))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def count_raw(self, n: int = 1) -> None:
        with self._lock:
            self.raw_events += n

    def gesture(self, description: str) -> None:
        with self._lock:
            self.recognized += 1
            self.current_action = description
            self._recent.append(description)

    def update(self, **fields: object) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self, key, value)

    def snapshot(self) -> DiagnosticsSnapshot:
        with self._lock:
            return DiagnosticsSnapshot(
                adb=self.adb, serial=self.serial, model=self.model,
                android_version=self.android_version, input_device=self.input_device,
                input_device_name=self.input_device_name, touchscreen=self.touchscreen,
                screen_size=self.screen_size, rotation=self.rotation,
                stream_alive=self.stream_alive, raw_events=self.raw_events,
                recognized=self.recognized, ignored=self.ignored, unknown=self.unknown,
                malformed=self.malformed, dropped=self.dropped,
                current_action=self.current_action, last_error=self.last_error,
                recent_gestures=tuple(self._recent),
            )

    def reset_counters(self) -> None:
        with self._lock:
            self.raw_events = self.recognized = self.ignored = self.unknown = 0
            self.malformed = self.dropped = 0
            self.current_action = ""
            self._recent.clear()


__all__ = ["AndroidRecordingDiagnostics", "DiagnosticsSnapshot"]
