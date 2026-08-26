"""Deterministic event normalization (no AI).

Exact mode keeps one action per discrete event. Smart mode applies rules:

* pointer_down + pointer_move* + pointer_up → ``drag`` (or ``swipe`` when the
  gesture is fast/short), ``long_press`` when held in place, ``tap`` otherwise
* two taps at the same spot within the double-click interval → ``double_tap``
* consecutive printable key presses → one ``type_text``
* pointer moves without a button held are dropped (noise)
* gaps between actions are recorded as ``pause`` metadata, never as fixed waits

The normalizer is a pure function of the event list, so it can re-run after
recovery or when the user switches modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from argus_test_creator.models.common import Point
from argus_test_creator.models.recording import (
    NormalizedAction,
    NormalizedActionKind,
    RecordingEvent,
    RecordingEventType,
    RecordingMode,
)

_PRINTABLE_MAX_LEN = 1
_NAMED_TYPING_KEYS = {"Space": " "}


@dataclass(frozen=True)
class NormalizerConfig:
    tap_max_distance_px: int = 8
    long_press_min_ms: int = 600
    swipe_max_duration_ms: int = 350
    double_tap_max_gap_ms: int = 350
    typing_max_gap_ms: int = 1500


class EventNormalizer:
    def __init__(self, config: NormalizerConfig | None = None) -> None:
        self._config = config or NormalizerConfig()

    def normalize(self, events: list[RecordingEvent], mode: RecordingMode) -> list[NormalizedAction]:  # noqa: E501
        ordered = sorted(events, key=lambda e: (e.sequence, e.timestamp))
        actions = self._gestures(ordered)
        if mode == RecordingMode.SMART:
            actions = self._merge_double_taps(actions)
            actions = self._merge_typing(actions)
        return actions

    # -- pass 1: raw events → gestures ------------------------------------------------

    def _gestures(self, events: list[RecordingEvent]) -> list[NormalizedAction]:
        actions: list[NormalizedAction] = []
        pending_down: RecordingEvent | None = None
        moves: list[RecordingEvent] = []
        for event in events:
            match event.event_type:
                case RecordingEventType.POINTER_DOWN:
                    pending_down = event
                    moves = []
                case RecordingEventType.POINTER_MOVE:
                    if pending_down is not None:
                        moves.append(event)
                    # Moves without a button held are noise: dropped.
                case RecordingEventType.POINTER_UP:
                    if pending_down is None:
                        continue
                    actions.append(self._gesture(pending_down, moves, event))
                    pending_down, moves = None, []
                case RecordingEventType.GESTURE:
                    action = self._semantic_gesture(event)
                    if action is not None:
                        actions.append(action)
                case RecordingEventType.CLICK:
                    actions.append(self._simple(NormalizedActionKind.TAP, event))
                case RecordingEventType.DOUBLE_CLICK:
                    actions.append(self._simple(NormalizedActionKind.DOUBLE_TAP, event))
                case RecordingEventType.KEY_PRESS:
                    actions.append(self._simple(NormalizedActionKind.KEY, event))
                case RecordingEventType.TEXT_INPUT:
                    actions.append(self._simple(NormalizedActionKind.TYPE_TEXT, event))
                case RecordingEventType.SCROLL:
                    actions.append(self._simple(NormalizedActionKind.SCROLL, event))
                case RecordingEventType.NAVIGATION:
                    actions.append(self._simple(NormalizedActionKind.NAVIGATE, event))
                case RecordingEventType.APP_STARTED:
                    actions.append(self._simple(NormalizedActionKind.APP_START, event))
                case RecordingEventType.APP_STOPPED:
                    actions.append(self._simple(NormalizedActionKind.APP_STOP, event))
                case _:
                    continue  # key_down/key_up/screen_changed/custom carry no action
        if pending_down is not None:
            # Recording stopped mid-gesture: keep it as a tap at the down point.
            actions.append(self._simple(NormalizedActionKind.TAP, pending_down))
        return actions

    def _gesture(
        self, down: RecordingEvent, moves: list[RecordingEvent], up: RecordingEvent
    ) -> NormalizedAction:
        cfg = self._config
        start = down.position or Point(x=0, y=0)
        end = up.position or (moves[-1].position if moves else start) or start
        distance = max(abs(end.x - start.x), abs(end.y - start.y))
        duration = int((up.timestamp - down.timestamp).total_seconds() * 1000)
        ids = (down.id, *(m.id for m in moves), up.id)
        common: dict[str, Any] = {
            "id": _action_id(ids[0]),
            "source_event_ids": ids,
            "position": start,
            "duration_ms": duration,
            "capture_before": down.capture_before,
            "capture_after": up.capture_after,
            "timestamp": down.timestamp,
            "metadata": {**down.metadata, "move_count": len(moves)},
        }
        if distance <= cfg.tap_max_distance_px:
            if duration >= cfg.long_press_min_ms:
                return NormalizedAction(kind=NormalizedActionKind.LONG_PRESS, **common)
            return NormalizedAction(kind=NormalizedActionKind.TAP, **common)
        kind = (
            NormalizedActionKind.SWIPE
            if duration <= cfg.swipe_max_duration_ms
            else NormalizedActionKind.DRAG
        )
        return NormalizedAction(kind=kind, position_end=end, **common)

    #: ``metadata["gesture"]`` values a recorder may emit with ``GESTURE`` events.
    _GESTURE_KINDS = {
        "tap": NormalizedActionKind.TAP,
        "double_tap": NormalizedActionKind.DOUBLE_TAP,
        "long_press": NormalizedActionKind.LONG_PRESS,
        "swipe": NormalizedActionKind.SWIPE,
        "drag": NormalizedActionKind.DRAG,
        "multi_touch": NormalizedActionKind.MULTI_TOUCH,
    }

    def _semantic_gesture(self, event: RecordingEvent) -> NormalizedAction | None:
        """A recorder already recognized the gesture: trust it, keep provenance."""
        kind = self._GESTURE_KINDS.get(str(event.metadata.get("gesture", "")))
        if kind is None:
            return None  # unknown gesture kinds stay in the raw journal only
        return self._simple(kind, event)

    @staticmethod
    def _simple(kind: NormalizedActionKind, event: RecordingEvent) -> NormalizedAction:
        return NormalizedAction(
            id=_action_id(event.id),
            kind=kind,
            source_event_ids=(event.id,),
            position=event.position,
            position_end=event.position_end,
            key=event.key,
            text=event.text,
            duration_ms=event.duration_ms,
            capture_before=event.capture_before,
            capture_after=event.capture_after,
            timestamp=event.timestamp,
            metadata=dict(event.metadata),
        )

    # -- pass 2: smart merges -----------------------------------------------------------

    def _merge_double_taps(self, actions: list[NormalizedAction]) -> list[NormalizedAction]:
        out: list[NormalizedAction] = []
        for action in actions:
            prev = out[-1] if out else None
            if (
                prev is not None
                and prev.kind == action.kind == NormalizedActionKind.TAP
                and prev.position is not None and action.position is not None
                and _near(prev.position, action.position, self._config.tap_max_distance_px)
                and _gap_ms(prev, action) <= self._config.double_tap_max_gap_ms
            ):
                out[-1] = prev.model_copy(update={
                    "kind": NormalizedActionKind.DOUBLE_TAP,
                    "source_event_ids": prev.source_event_ids + action.source_event_ids,
                    "capture_after": action.capture_after or prev.capture_after,
                })
                continue
            out.append(action)
        return out

    def _merge_typing(self, actions: list[NormalizedAction]) -> list[NormalizedAction]:
        out: list[NormalizedAction] = []
        for action in actions:
            char = _typed_char(action)
            prev = out[-1] if out else None
            if (
                char is not None
                and prev is not None
                and prev.kind == NormalizedActionKind.TYPE_TEXT
                and _gap_ms(prev, action) <= self._config.typing_max_gap_ms
            ):
                out[-1] = prev.model_copy(update={
                    "text": (prev.text or "") + char,
                    "source_event_ids": prev.source_event_ids + action.source_event_ids,
                    "capture_after": action.capture_after or prev.capture_after,
                    "duration_ms": _gap_ms(prev, action) + (prev.duration_ms or 0),
                })
                continue
            if char is not None and action.kind == NormalizedActionKind.KEY:
                out.append(action.model_copy(update={
                    "kind": NormalizedActionKind.TYPE_TEXT, "text": char, "key": None,
                }))
                continue
            out.append(action)
        return out


def _action_id(first_event_id: str) -> str:
    """Stable id: the same events always yield the same action id (recovery, re-normalize)."""
    return f"act_{first_event_id.removeprefix('evt_')}"


def _typed_char(action: NormalizedAction) -> str | None:
    if action.kind == NormalizedActionKind.TYPE_TEXT:
        return action.text
    if action.kind != NormalizedActionKind.KEY or action.key is None:
        return None
    if action.metadata.get("modifiers"):
        return None
    key = action.key
    if key in _NAMED_TYPING_KEYS:
        return _NAMED_TYPING_KEYS[key]
    if len(key) == _PRINTABLE_MAX_LEN and key.isprintable():
        return key
    return None


def _near(a: Point, b: Point, tolerance: int) -> bool:
    return abs(a.x - b.x) <= tolerance and abs(a.y - b.y) <= tolerance


def _gap_ms(a: NormalizedAction, b: NormalizedAction) -> int:
    delta = (b.timestamp - a.timestamp).total_seconds() * 1000 - (a.duration_ms or 0)
    return max(int(delta), 0)
