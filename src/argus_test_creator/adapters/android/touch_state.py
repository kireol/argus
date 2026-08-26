"""TouchState — multi-touch slot tracking (Linux MT protocol B, with A/legacy fallback).

Feed raw events; on every ``SYN_REPORT`` a :class:`TouchFrame` describes what
changed in that frame (which slots began, moved, or ended). Gesture logic
lives elsewhere — this module only knows *where fingers are*.

Protocol B (``ABS_MT_SLOT`` + ``ABS_MT_TRACKING_ID``) is the modern norm:
``TRACKING_ID >= 0`` starts a contact in the current slot, ``-1`` ends it.
Some devices report a single contact through ``ABS_X``/``ABS_Y`` with
``BTN_TOUCH`` (no slots); that is mapped onto slot 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from argus_test_creator.adapters.android.models import (
    TRACKING_ID_NONE,
    AbsCode,
    AndroidRawInputEvent,
    EventType,
)

_BTN_TOUCH = "BTN_TOUCH"


@dataclass
class TouchSlot:
    slot_id: int
    tracking_id: int = TRACKING_ID_NONE
    x: int | None = None
    y: int | None = None
    active: bool = False
    start_time: float = 0.0
    last_time: float = 0.0
    #: Raw samples (x, y, t) since the contact began — bounded by ``max_samples``.
    samples: list[tuple[int, int, float]] = field(default_factory=list)
    #: Set inside a frame when the slot's coordinates changed (cleared on SYN_REPORT).
    dirty: bool = False

    @property
    def has_position(self) -> bool:
        return self.x is not None and self.y is not None


@dataclass(frozen=True)
class SlotSnapshot:
    slot_id: int
    tracking_id: int
    x: int
    y: int
    start_time: float
    time: float
    samples: tuple[tuple[int, int, float], ...]


@dataclass(frozen=True)
class TouchFrame:
    """What one ``SYN_REPORT`` frame changed."""

    time: float
    started: tuple[SlotSnapshot, ...] = ()
    moved: tuple[SlotSnapshot, ...] = ()
    ended: tuple[SlotSnapshot, ...] = ()
    #: Every slot still active *after* this frame.
    active: tuple[SlotSnapshot, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.started or self.moved or self.ended)


class TouchState:
    def __init__(self, *, max_samples: int = 512) -> None:
        self._slots: dict[int, TouchSlot] = {}
        self._current = 0
        self._max_samples = max_samples
        self._pending_start: set[int] = set()
        self._pending_end: set[int] = set()
        self._legacy = False  # ABS_X/ABS_Y + BTN_TOUCH without tracking ids
        self._last_time = 0.0
        self.frames = 0

    # -- queries -----------------------------------------------------------------------------

    @property
    def active_slots(self) -> list[TouchSlot]:
        return [s for s in self._slots.values() if s.active]

    @property
    def active_count(self) -> int:
        return len(self.active_slots)

    def slot(self, slot_id: int) -> TouchSlot:
        slot = self._slots.get(slot_id)
        if slot is None:
            slot = TouchSlot(slot_id=slot_id)
            self._slots[slot_id] = slot
        return slot

    # -- feeding -----------------------------------------------------------------------------

    def feed(self, event: AndroidRawInputEvent) -> TouchFrame | None:
        """Consume one raw event; returns a frame on ``SYN_REPORT`` (else ``None``)."""
        t = event.timestamp if event.timestamp is not None else self._last_time
        self._last_time = t
        if event.event_type == EventType.EV_SYN:
            if event.is_syn_report:
                return self._syn_report(t)
            return None
        if event.event_type == EventType.EV_ABS:
            self._abs(event, t)
        elif event.event_type == EventType.EV_KEY and event.code == _BTN_TOUCH:
            self._btn_touch(event.value, t)
        return None

    def reset(self) -> None:
        self._slots.clear()
        self._pending_start.clear()
        self._pending_end.clear()
        self._current = 0

    # -- internals -----------------------------------------------------------------------------

    def _abs(self, event: AndroidRawInputEvent, t: float) -> None:
        code = event.code
        if code == AbsCode.ABS_MT_SLOT:
            self._current = max(event.value, 0)
            return
        slot = self.slot(self._current)
        if code == AbsCode.ABS_MT_TRACKING_ID:
            self._legacy = False
            if event.value == TRACKING_ID_NONE:
                if slot.active:
                    self._pending_end.add(slot.slot_id)
            else:
                slot.tracking_id = event.value
                if not slot.active:
                    self._begin(slot, t)
            return
        if code in (AbsCode.ABS_MT_POSITION_X, AbsCode.ABS_X):
            slot.x = event.value
            slot.dirty = True
        elif code in (AbsCode.ABS_MT_POSITION_Y, AbsCode.ABS_Y):
            slot.y = event.value
            slot.dirty = True
        if code in (AbsCode.ABS_X, AbsCode.ABS_Y):
            self._legacy = self._legacy or not any(
                s.tracking_id != TRACKING_ID_NONE for s in self._slots.values()
            )

    def _btn_touch(self, value: int, t: float) -> None:
        # Legacy single-touch: BTN_TOUCH brackets the contact. With protocol B the
        # tracking id already did that, so only act when no tracking ids are in use.
        slot = self.slot(0)
        tracked = any(s.tracking_id != TRACKING_ID_NONE for s in self._slots.values())
        if value == 1 and not slot.active and not tracked:
            self._legacy = True
            self._begin(slot, t)
        elif value == 0 and slot.active and self._legacy:
            self._pending_end.add(0)

    def _begin(self, slot: TouchSlot, t: float) -> None:
        slot.active = True
        slot.start_time = t
        slot.last_time = t
        slot.samples = []
        slot.dirty = True
        self._pending_start.add(slot.slot_id)
        self._pending_end.discard(slot.slot_id)

    def _syn_report(self, t: float) -> TouchFrame:
        self.frames += 1
        started: list[SlotSnapshot] = []
        moved: list[SlotSnapshot] = []
        ended: list[SlotSnapshot] = []
        for slot in self._slots.values():
            if not slot.active:
                continue
            if slot.dirty and slot.has_position:
                assert slot.x is not None and slot.y is not None
                if len(slot.samples) < self._max_samples:
                    slot.samples.append((slot.x, slot.y, t))
                else:
                    slot.samples[-1] = (slot.x, slot.y, t)
                slot.last_time = t
            snapshot = self._snapshot(slot, t)
            if slot.slot_id in self._pending_start:
                if snapshot is not None:
                    started.append(snapshot)
                    self._pending_start.discard(slot.slot_id)
            elif slot.dirty and snapshot is not None:
                moved.append(snapshot)
            if slot.slot_id in self._pending_end:
                if snapshot is not None:
                    ended.append(snapshot)
                slot.active = False
                slot.tracking_id = TRACKING_ID_NONE
                slot.samples = []
                self._pending_end.discard(slot.slot_id)
                self._pending_start.discard(slot.slot_id)
            slot.dirty = False
        active = tuple(
            s for s in (self._snapshot(slot, t) for slot in self._slots.values() if slot.active)
            if s is not None
        )
        return TouchFrame(time=t, started=tuple(started), moved=tuple(moved),
                          ended=tuple(ended), active=active)

    @staticmethod
    def _snapshot(slot: TouchSlot, t: float) -> SlotSnapshot | None:
        if not slot.has_position:
            return None
        assert slot.x is not None and slot.y is not None
        return SlotSnapshot(
            slot_id=slot.slot_id, tracking_id=slot.tracking_id, x=slot.x, y=slot.y,
            start_time=slot.start_time, time=t, samples=tuple(slot.samples),
        )


__all__ = ["SlotSnapshot", "TouchFrame", "TouchSlot", "TouchState"]
