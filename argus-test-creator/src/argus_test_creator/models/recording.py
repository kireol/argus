"""Recording models: raw events, screen captures, OCR observations, normalized actions.

The chain is ``RecordingEvent`` (raw, faithful) → ``NormalizedAction``
(deterministic rules) → ``StepDraft`` (authoring). Every stage keeps
provenance back to the raw event ids.

These models distinguish the different kinds of facts the Creator sees:
"the user clicked here" (event), "this is what the screen looked like"
(capture), "OCR read this text" (OCR observation). None of them is treated
as truth about the application.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from argus_test_creator.core.ids import new_id
from argus_test_creator.models.common import Point, Rect


class RecordingMode(StrEnum):
    EXACT = "exact"  # record what I do
    SMART = "smart"  # normalize obvious noise


class RecordingEventType(StrEnum):
    POINTER_DOWN = "pointer_down"
    POINTER_UP = "pointer_up"
    POINTER_MOVE = "pointer_move"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    SCROLL = "scroll"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"
    KEY_PRESS = "key_press"
    TEXT_INPUT = "text_input"
    NAVIGATION = "navigation"
    APP_STARTED = "app_started"
    APP_STOPPED = "app_stopped"
    SCREEN_CHANGED = "screen_changed"
    #: A recorder that recognizes gestures itself (Android touch) emits one
    #: semantic event per gesture; ``metadata["gesture"]`` names it
    #: (tap/swipe/long_press/multi_touch) and ``metadata["fingers"]`` holds
    #: multi-touch trajectories.
    GESTURE = "gesture"
    #: The target vanished mid-recording / came back (adapter-level facts).
    CONNECTION_LOST = "connection_lost"
    CONNECTION_RESTORED = "connection_restored"
    CUSTOM = "custom"


class RecordingEvent(BaseModel):
    """One raw fact observed by a recorder adapter."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: new_id("evt"))
    sequence: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: RecordingEventType
    position: Point | None = None
    #: Secondary position (drag end, scroll delta target).
    position_end: Point | None = None
    button: str | None = None
    key: str | None = None
    text: str | None = None
    modifiers: tuple[str, ...] = ()
    duration_ms: int | None = None
    capture_before: str | None = None
    capture_after: str | None = None
    #: Browser DOM / accessibility evidence, window titles, etc. Never
    #: converted into an Argus action automatically.
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScreenCapture(BaseModel):
    """A screenshot persisted to disk (never held in RAM by the model)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: new_id("cap"))
    path: str
    width: int
    height: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sha256: str | None = None
    thumbnail_path: str | None = None
    #: Which raw event this capture belongs to and whether before/after it.
    event_id: str | None = None
    phase: str | None = None  # "before" | "after" | "manual" | "live"


class OCRWordObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float | None = None
    region: Rect | None = None


class OCRObservation(BaseModel):
    """Text OCR found in a capture. Evidence, not truth."""

    model_config = ConfigDict(frozen=True)

    capture_id: str
    provider: str
    text: str
    words: tuple[OCRWordObservation, ...] = ()
    region: Rect | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def lines(self) -> list[str]:
        return [line.strip() for line in self.text.splitlines() if line.strip()]


class NormalizedActionKind(StrEnum):
    TAP = "tap"
    DOUBLE_TAP = "double_tap"
    LONG_PRESS = "long_press"
    DRAG = "drag"
    SWIPE = "swipe"
    MULTI_TOUCH = "multi_touch"
    KEY = "key"
    TYPE_TEXT = "type_text"
    SCROLL = "scroll"
    NAVIGATE = "navigate"
    APP_START = "app_start"
    APP_STOP = "app_stop"
    PAUSE = "pause"  # an observed gap in interaction; never becomes a fixed wait by default


class NormalizedAction(BaseModel):
    """A semantic action derived from raw events by deterministic rules."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: new_id("act"))
    kind: NormalizedActionKind
    source_event_ids: tuple[str, ...] = ()
    position: Point | None = None
    position_end: Point | None = None
    key: str | None = None
    text: str | None = None
    duration_ms: int | None = None
    capture_before: str | None = None
    capture_after: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    def describe(self) -> str:
        p = f"({self.position.x}, {self.position.y})" if self.position else ""
        match self.kind:
            case NormalizedActionKind.TAP:
                return f"Tap {p}"
            case NormalizedActionKind.DOUBLE_TAP:
                return f"Double-tap {p}"
            case NormalizedActionKind.LONG_PRESS:
                return f"Long press {p}"
            case NormalizedActionKind.DRAG | NormalizedActionKind.SWIPE:
                end = self.position_end
                e = f"({end.x}, {end.y})" if end else ""
                return f"{self.kind.value.title()} {p} → {e}"
            case NormalizedActionKind.MULTI_TOUCH:
                fingers = self.metadata.get("fingers") or ()
                return f"Multi-touch ({len(fingers)} fingers)"
            case NormalizedActionKind.KEY:
                return f"Press {self.key}"
            case NormalizedActionKind.TYPE_TEXT:
                return f"Type {self.text!r}"
            case NormalizedActionKind.SCROLL:
                return f"Scroll {p}"
            case NormalizedActionKind.NAVIGATE:
                return f"Navigate to {self.text}"
            case NormalizedActionKind.APP_START:
                return "Start application"
            case NormalizedActionKind.APP_STOP:
                return "Stop application"
            case NormalizedActionKind.PAUSE:
                return f"Pause {self.duration_ms}ms"
        return self.kind.value
