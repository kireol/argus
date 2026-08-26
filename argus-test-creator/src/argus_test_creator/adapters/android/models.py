"""Typed Android models: devices, input devices, raw Linux input events, gestures.

Nothing in here touches ADB or the UI. ``AndroidRawInputEvent`` is the
*only* representation of a ``getevent`` line the rest of the recorder ever
sees; ``RecognizedGesture`` is what the gesture recognizer produces and what
the recorder turns into ordinary :class:`RecordingEvent`s.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# -- devices -----------------------------------------------------------------------


class AndroidDevice(BaseModel):
    """One line of ``adb devices -l``."""

    model_config = ConfigDict(frozen=True)

    serial: str
    #: ``device``, ``unauthorized``, ``offline``, ``recovery``...
    state: str
    model: str | None = None
    product: str | None = None
    transport_id: str | None = None

    @property
    def usable(self) -> bool:
        return self.state == "device"

    def label(self) -> str:
        name = (self.model or "").replace("_", " ").strip()
        return f"{name} — {self.serial}" if name else self.serial


class AndroidDeviceInfo(BaseModel):
    """Facts gathered from the selected device after connection."""

    model_config = ConfigDict(frozen=True)

    serial: str
    model: str | None = None
    android_version: str | None = None
    sdk: int | None = None
    #: Physical (natural-orientation) screen size in pixels.
    natural_width: int = 0
    natural_height: int = 0
    #: Display rotation: 0, 1, 2, 3 (× 90°).
    rotation: int = 0

    @property
    def screen_size(self) -> tuple[int, int]:
        """Size in the *current* orientation (what a screenshot returns)."""
        if self.rotation in (1, 3):
            return (self.natural_height, self.natural_width)
        return (self.natural_width, self.natural_height)


class AxisRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    min: int
    max: int
    resolution: int = 0

    @property
    def span(self) -> int:
        return self.max - self.min


class AndroidInputDevice(BaseModel):
    """One ``/dev/input/eventN`` as reported by ``getevent -lp``."""

    model_config = ConfigDict(frozen=True)

    path: str
    name: str
    #: Event-type name → tuple of code names (``"EV_ABS": ("ABS_MT_POSITION_X", ...)``).
    capabilities: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    axis_ranges: dict[str, AxisRange] = Field(default_factory=dict)
    properties: tuple[str, ...] = ()

    @property
    def abs_codes(self) -> tuple[str, ...]:
        return self.capabilities.get("EV_ABS", ())

    @property
    def key_codes(self) -> tuple[str, ...]:
        return self.capabilities.get("EV_KEY", ())

    @property
    def uses_mt_protocol(self) -> bool:
        return "ABS_MT_POSITION_X" in self.abs_codes and "ABS_MT_POSITION_Y" in self.abs_codes

    @property
    def is_touchscreen(self) -> bool:
        has_xy = ("ABS_X" in self.abs_codes and "ABS_Y" in self.abs_codes)
        return self.uses_mt_protocol or (has_xy and "BTN_TOUCH" in self.key_codes)

    @property
    def is_direct(self) -> bool:
        return "INPUT_PROP_DIRECT" in self.properties

    @property
    def has_keys(self) -> bool:
        return bool(self.key_codes)

    def x_range(self) -> AxisRange | None:
        return self.axis_ranges.get("ABS_MT_POSITION_X") or self.axis_ranges.get("ABS_X")

    def y_range(self) -> AxisRange | None:
        return self.axis_ranges.get("ABS_MT_POSITION_Y") or self.axis_ranges.get("ABS_Y")


# -- raw events ----------------------------------------------------------------------


class EventType(StrEnum):
    EV_SYN = "EV_SYN"
    EV_KEY = "EV_KEY"
    EV_REL = "EV_REL"
    EV_ABS = "EV_ABS"
    EV_MSC = "EV_MSC"
    EV_SW = "EV_SW"
    EV_LED = "EV_LED"
    EV_SND = "EV_SND"
    EV_REP = "EV_REP"
    EV_FF = "EV_FF"
    EV_PWR = "EV_PWR"
    UNKNOWN = "UNKNOWN"


class AbsCode(StrEnum):
    ABS_X = "ABS_X"
    ABS_Y = "ABS_Y"
    ABS_PRESSURE = "ABS_PRESSURE"
    ABS_MT_SLOT = "ABS_MT_SLOT"
    ABS_MT_TOUCH_MAJOR = "ABS_MT_TOUCH_MAJOR"
    ABS_MT_POSITION_X = "ABS_MT_POSITION_X"
    ABS_MT_POSITION_Y = "ABS_MT_POSITION_Y"
    ABS_MT_TRACKING_ID = "ABS_MT_TRACKING_ID"
    ABS_MT_PRESSURE = "ABS_MT_PRESSURE"


class SynCode(StrEnum):
    SYN_REPORT = "SYN_REPORT"
    SYN_MT_REPORT = "SYN_MT_REPORT"
    SYN_DROPPED = "SYN_DROPPED"


class KeyValue:
    UP = 0
    DOWN = 1
    REPEAT = 2


#: ``ABS_MT_TRACKING_ID`` value that ends a contact.
TRACKING_ID_NONE = -1


class AndroidRawInputEvent(BaseModel):
    """One parsed ``getevent`` line."""

    model_config = ConfigDict(frozen=True)

    #: Kernel timestamp in seconds (monotonic, from ``getevent -t``); ``None`` without ``-t``.
    timestamp: float | None
    device: str
    event_type: EventType
    #: Symbolic code (``ABS_MT_POSITION_X``, ``KEY_BACK``, ``SYN_REPORT``, or the raw hex
    #: text when ``getevent`` could not name it).
    code: str
    #: Signed integer value (``ffffffff`` → -1; ``DOWN``/``UP``/``REPEAT`` → 1/0/2).
    value: int
    #: Unrecognised/unnamed parts preserved verbatim for diagnostics.
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_syn_report(self) -> bool:
        return self.event_type == EventType.EV_SYN and self.code == SynCode.SYN_REPORT

    @property
    def is_known(self) -> bool:
        return self.event_type != EventType.UNKNOWN and not self.code.isdigit()


# -- gestures -----------------------------------------------------------------------


class TouchPoint(BaseModel):
    """A screen-space sample of one finger."""

    model_config = ConfigDict(frozen=True)

    x: int
    y: int
    t: float


class GestureKind(StrEnum):
    TAP = "tap"
    SWIPE = "swipe"
    LONG_PRESS = "long_press"
    MULTI_TOUCH = "multi_touch"
    KEY_PRESS = "key_press"
    UNKNOWN = "unknown"


class RecognizedGesture(BaseModel):
    """Base class; ``kind`` discriminates. Coordinates are *screen* pixels."""

    model_config = ConfigDict(frozen=True)

    kind: GestureKind
    #: Kernel timestamp (seconds) of the gesture start.
    timestamp: float
    duration_ms: int = 0
    #: How many raw input events contributed (for diagnostics only).
    raw_event_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Tap(RecognizedGesture):
    kind: GestureKind = GestureKind.TAP
    x: int
    y: int


class LongPress(RecognizedGesture):
    kind: GestureKind = GestureKind.LONG_PRESS
    x: int
    y: int


class Swipe(RecognizedGesture):
    kind: GestureKind = GestureKind.SWIPE
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    #: Simplified path (start, a few intermediate samples, end).
    path: tuple[TouchPoint, ...] = ()


class MultiTouch(RecognizedGesture):
    kind: GestureKind = GestureKind.MULTI_TOUCH
    #: One trajectory per finger, each with at least a start and an end sample.
    fingers: tuple[tuple[TouchPoint, ...], ...]

    @property
    def finger_count(self) -> int:
        return len(self.fingers)


class KeyPress(RecognizedGesture):
    kind: GestureKind = GestureKind.KEY_PRESS
    #: Argus key name (``BACK``), or ``KEY_<LINUX>`` for keys without a mapping.
    key: str
    linux_key: str
    mapped: bool = True


class UnknownGesture(RecognizedGesture):
    """Something the recognizer saw but could not classify (kept, never silently dropped)."""

    kind: GestureKind = GestureKind.UNKNOWN
    reason: str = ""


__all__ = [
    "TRACKING_ID_NONE", "AbsCode", "AndroidDevice", "AndroidDeviceInfo", "AndroidInputDevice",
    "AndroidRawInputEvent", "AxisRange", "EventType", "GestureKind", "KeyPress", "KeyValue",
    "LongPress", "MultiTouch", "RecognizedGesture", "Swipe", "SynCode", "Tap", "TouchPoint",
    "UnknownGesture",
]
