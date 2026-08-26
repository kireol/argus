"""AndroidGestureRecognizer — raw input events → semantic gestures.

Pipeline::

    AndroidRawInputEvent → TouchState (slots) → touch sequence → RecognizedGesture

A *touch sequence* starts when the first finger lands and ends when the last
finger lifts. Its classification:

* max one finger, moved ≤ ``tap_max_distance_px``, held < ``long_press_min_ms`` → ``Tap``
* max one finger, moved ≤ ``tap_max_distance_px``, held ≥ ``long_press_min_ms`` → ``LongPress``
* max one finger, moved further → ``Swipe`` (hundreds of samples → one gesture)
* two or more fingers at any point → ``MultiTouch`` with every trajectory —
  never two independent taps

Hardware/navigation keys (``EV_KEY``) become ``KeyPress`` on release. Any
touch sequence that ends without usable coordinates becomes ``UnknownGesture``
rather than disappearing. Thresholds live in :class:`GestureConfig` only.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from argus_test_creator.adapters.android.coordinates import AndroidCoordinateMapper
from argus_test_creator.adapters.android.keys import map_linux_key
from argus_test_creator.adapters.android.models import (
    AndroidRawInputEvent,
    EventType,
    KeyPress,
    KeyValue,
    LongPress,
    MultiTouch,
    RecognizedGesture,
    Swipe,
    Tap,
    TouchPoint,
    UnknownGesture,
)
from argus_test_creator.adapters.android.touch_state import SlotSnapshot, TouchFrame, TouchState


@dataclass(frozen=True)
class GestureConfig:
    tap_max_duration_ms: int = 500
    tap_max_distance_px: int = 20
    long_press_min_ms: int = 500
    #: Max samples kept per finger in a Swipe/MultiTouch path (evenly thinned).
    path_max_points: int = 16
    #: Fingers whose lifetime overlaps by less than this are still one multi-touch
    #: (the OS treats near-simultaneous contacts as one gesture too).
    multi_touch_min_overlap_ms: int = 0


@dataclass
class _Finger:
    tracking_id: int
    slot_id: int
    start: float
    points: list[TouchPoint]
    ended: bool = False


class AndroidGestureRecognizer:
    def __init__(
        self,
        *,
        mapper: AndroidCoordinateMapper | None = None,
        config: GestureConfig | None = None,
        touch_device: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or GestureConfig()
        self._mapper = mapper
        self._touch_device = touch_device
        self._clock = clock
        self._state = TouchState()
        self._fingers: dict[tuple[int, int], _Finger] = {}  # (slot, tracking) → finger
        self._sequence_start: float | None = None
        self._sequence_events = 0
        self._max_fingers = 0
        self._keys_down: dict[tuple[str, str], float] = {}
        self.ignored = 0
        self.recognized = 0

    # -- configuration -----------------------------------------------------------------------------

    @property
    def mapper(self) -> AndroidCoordinateMapper | None:
        return self._mapper

    def set_mapper(self, mapper: AndroidCoordinateMapper | None) -> None:
        self._mapper = mapper

    @property
    def in_gesture(self) -> bool:
        return self._sequence_start is not None

    # -- feeding ----------------------------------------------------------------------------------

    def feed(self, event: AndroidRawInputEvent) -> list[RecognizedGesture]:
        """Consume one raw event; returns zero or more finished gestures."""
        if event.timestamp is None:
            event = event.model_copy(update={"timestamp": self._clock()})
        if event.event_type == EventType.EV_KEY and event.code != "BTN_TOUCH":
            return self._key(event)
        if self._touch_device is not None and event.device != self._touch_device:
            self.ignored += 1
            return []
        if event.event_type not in (EventType.EV_ABS, EventType.EV_SYN, EventType.EV_KEY):
            self.ignored += 1
            return []
        if self.in_gesture or event.event_type != EventType.EV_SYN:
            self._sequence_events += 1
        frame = self._state.feed(event)
        if frame is None:
            return []
        return self._frame(frame)

    def flush(self) -> list[RecognizedGesture]:
        """Finish an in-progress sequence (recording stopped mid-gesture)."""
        if not self.in_gesture:
            return []
        now = self._clock()
        for finger in self._fingers.values():
            finger.ended = True
        gesture = self._classify(now, incomplete=True)
        self._state.reset()
        self._reset_sequence()
        return [gesture] if gesture is not None else []

    # -- touch --------------------------------------------------------------------------------------

    def _frame(self, frame: TouchFrame) -> list[RecognizedGesture]:
        out: list[RecognizedGesture] = []
        for snap in frame.started:
            if self._sequence_start is None:
                self._sequence_start = snap.start_time
                self._sequence_events = 1
            key = (snap.slot_id, snap.tracking_id)
            self._fingers[key] = _Finger(
                tracking_id=snap.tracking_id, slot_id=snap.slot_id, start=snap.start_time,
                points=[self._point(snap)],
            )
        for snap in frame.moved:
            finger = self._fingers.get((snap.slot_id, snap.tracking_id))
            if finger is not None:
                finger.points.append(self._point(snap))
        for snap in frame.ended:
            finger = self._fingers.get((snap.slot_id, snap.tracking_id))
            if finger is not None:
                last = self._point(snap)
                if not finger.points or (finger.points[-1].x, finger.points[-1].y) != (
                    last.x, last.y
                ) or finger.points[-1].t != last.t:
                    finger.points.append(last)
                finger.ended = True
        concurrent = len(frame.active)
        self._max_fingers = max(self._max_fingers, concurrent, len(frame.started))
        if self.in_gesture and concurrent == 0 and all(f.ended for f in self._fingers.values()):
            gesture = self._classify(frame.time)
            if gesture is not None:
                out.append(gesture)
            self._reset_sequence()
        return out

    def _point(self, snap: SlotSnapshot) -> TouchPoint:
        if self._mapper is None:
            return TouchPoint(x=snap.x, y=snap.y, t=snap.time)
        p = self._mapper.map(snap.x, snap.y)
        return TouchPoint(x=p.x, y=p.y, t=snap.time)

    def _classify(self, end_time: float, *, incomplete: bool = False) -> RecognizedGesture | None:
        assert self._sequence_start is not None
        fingers = [f for f in self._fingers.values() if f.points]
        start = self._sequence_start
        duration = max(int(round((end_time - start) * 1000)), 0)
        meta = {"incomplete": True} if incomplete else {}
        common = {"timestamp": start, "duration_ms": duration,
                  "raw_event_count": self._sequence_events, "metadata": meta}
        if not fingers:
            self.recognized += 1
            return UnknownGesture(reason="touch sequence without coordinates", **common)
        if self._max_fingers >= 2 or len(fingers) >= 2:
            self.recognized += 1
            paths = tuple(_thin(f.points, self.config.path_max_points)
                          for f in sorted(fingers, key=lambda f: f.start))
            return MultiTouch(fingers=paths, **common)
        (finger,) = fingers
        first, last = finger.points[0], finger.points[-1]
        distance = max(abs(last.x - first.x), abs(last.y - first.y))
        cfg = self.config
        self.recognized += 1
        if distance <= cfg.tap_max_distance_px:
            if duration >= cfg.long_press_min_ms:
                return LongPress(x=first.x, y=first.y, **common)
            return Tap(x=first.x, y=first.y, **common)
        return Swipe(start_x=first.x, start_y=first.y, end_x=last.x, end_y=last.y,
                     path=_thin(finger.points, cfg.path_max_points), **common)

    def _reset_sequence(self) -> None:
        self._fingers.clear()
        self._sequence_start = None
        self._sequence_events = 0
        self._max_fingers = 0

    # -- keys --------------------------------------------------------------------------------------

    def _key(self, event: AndroidRawInputEvent) -> list[RecognizedGesture]:
        assert event.timestamp is not None
        mapped = map_linux_key(event.code)
        if mapped is None:
            self.ignored += 1
            return []
        key = (event.device, event.code)
        if event.value == KeyValue.DOWN:
            self._keys_down[key] = event.timestamp
            return []
        if event.value == KeyValue.REPEAT:
            return []
        down_at = self._keys_down.pop(key, event.timestamp)
        self.recognized += 1
        return [KeyPress(
            key=mapped.argus_key, linux_key=mapped.linux_key, mapped=mapped.mapped,
            timestamp=down_at, duration_ms=max(int(round((event.timestamp - down_at) * 1000)), 0),
            raw_event_count=2,
            metadata={"android_keycode": mapped.android_keycode, "device": event.device},
        )]


def _thin(points: list[TouchPoint], limit: int) -> tuple[TouchPoint, ...]:
    if len(points) <= limit:
        return tuple(points)
    if limit < 2:
        return (points[0], points[-1])
    step = (len(points) - 1) / (limit - 1)
    return tuple(points[round(i * step)] for i in range(limit))


__all__ = ["AndroidGestureRecognizer", "GestureConfig"]
