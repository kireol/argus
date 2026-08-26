from __future__ import annotations

from datetime import UTC, datetime, timedelta

from argus_test_creator.models import (
    NormalizedActionKind,
    Point,
    RecordingEvent,
    RecordingEventType,
    RecordingMode,
)
from argus_test_creator.recording import EventNormalizer, actions_to_steps
from argus_test_creator.recording.adapter import EventSink
from argus_test_creator.recording.normalizer import NormalizerConfig
from argus_test_creator.targets import PLATFORM_CAPABILITIES

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def ev(kind: RecordingEventType, ms: int, **fields) -> RecordingEvent:
    return RecordingEvent(event_type=kind, timestamp=T0 + timedelta(milliseconds=ms), **fields)


def pointer(kind, ms, x, y):
    return ev(kind, ms, position=Point(x=x, y=y), button="left")


def test_drag_from_pointer_stream():
    events = [pointer(RecordingEventType.POINTER_DOWN, 0, 10, 10)]
    events += [pointer(RecordingEventType.POINTER_MOVE, 50 * i, 10 + 20 * i, 10)
               for i in range(1, 18)]
    events.append(pointer(RecordingEventType.POINTER_UP, 900, 400, 10))
    actions = EventNormalizer().normalize(events, RecordingMode.SMART)
    assert [a.kind for a in actions] == [NormalizedActionKind.DRAG]
    assert actions[0].position_end == Point(x=400, y=10)
    assert len(actions[0].source_event_ids) == 19
    assert actions[0].metadata["move_count"] == 17


def test_swipe_when_fast():
    events = [pointer(RecordingEventType.POINTER_DOWN, 0, 10, 10),
              pointer(RecordingEventType.POINTER_MOVE, 100, 200, 10),
              pointer(RecordingEventType.POINTER_UP, 200, 400, 10)]
    actions = EventNormalizer().normalize(events, RecordingMode.SMART)
    assert actions[0].kind == NormalizedActionKind.SWIPE


def test_tap_and_long_press():
    tap = [pointer(RecordingEventType.POINTER_DOWN, 0, 10, 10),
           pointer(RecordingEventType.POINTER_UP, 100, 12, 11)]
    hold = [pointer(RecordingEventType.POINTER_DOWN, 500, 10, 10),
            pointer(RecordingEventType.POINTER_UP, 1400, 10, 10)]
    actions = EventNormalizer().normalize(tap + hold, RecordingMode.SMART)
    assert [a.kind for a in actions] == [NormalizedActionKind.TAP,
                                         NormalizedActionKind.LONG_PRESS]
    assert actions[1].duration_ms == 900


def test_double_tap_merge_only_in_smart_mode():
    events = [ev(RecordingEventType.CLICK, 0, position=Point(x=5, y=5)),
              ev(RecordingEventType.CLICK, 200, position=Point(x=6, y=5)),
              ev(RecordingEventType.CLICK, 2000, position=Point(x=6, y=5))]
    smart = EventNormalizer().normalize(events, RecordingMode.SMART)
    assert [a.kind for a in smart] == [NormalizedActionKind.DOUBLE_TAP, NormalizedActionKind.TAP]
    exact = EventNormalizer().normalize(events, RecordingMode.EXACT)
    assert [a.kind for a in exact] == [NormalizedActionKind.TAP] * 3


def test_typing_merges_and_breaks_on_gap_or_named_key():
    keys = ["B", "a", "t", "Space", "m"]
    events = [ev(RecordingEventType.KEY_PRESS, 100 * i, key=k) for i, k in enumerate(keys)]
    events.append(ev(RecordingEventType.KEY_PRESS, 600, key="ENTER"))
    events.append(ev(RecordingEventType.KEY_PRESS, 5000, key="x"))
    actions = EventNormalizer().normalize(events, RecordingMode.SMART)
    assert [a.kind for a in actions] == [NormalizedActionKind.TYPE_TEXT, NormalizedActionKind.KEY,
                                         NormalizedActionKind.TYPE_TEXT]
    assert actions[0].text == "Bat m" and actions[1].key == "ENTER" and actions[2].text == "x"
    exact = EventNormalizer().normalize(events, RecordingMode.EXACT)
    assert all(a.kind == NormalizedActionKind.KEY for a in exact)


def test_moves_without_button_are_dropped_and_unfinished_gesture_kept():
    events = [pointer(RecordingEventType.POINTER_MOVE, 0, 1, 1),
              pointer(RecordingEventType.POINTER_MOVE, 10, 2, 2),
              pointer(RecordingEventType.POINTER_DOWN, 20, 3, 3)]
    actions = EventNormalizer().normalize(events, RecordingMode.SMART)
    assert [a.kind for a in actions] == [NormalizedActionKind.TAP]


def test_normalizer_is_deterministic_and_sorted_by_sequence():
    a = ev(RecordingEventType.CLICK, 0, position=Point(x=1, y=1)).model_copy(update={"sequence": 2})
    b = ev(RecordingEventType.KEY_PRESS, 0, key="ENTER").model_copy(update={"sequence": 1})
    n = EventNormalizer(NormalizerConfig())
    first = n.normalize([a, b], RecordingMode.SMART)
    second = n.normalize([b, a], RecordingMode.SMART)
    assert [x.kind for x in first] == [x.kind for x in second] == [NormalizedActionKind.KEY,
                                                                   NormalizedActionKind.TAP]


def test_actions_to_steps_mapping():
    events = [
        ev(RecordingEventType.CLICK, 0, position=Point(x=1, y=2)),
        ev(RecordingEventType.KEY_PRESS, 100, key="H"),
        ev(RecordingEventType.KEY_PRESS, 200, key="i"),
        ev(RecordingEventType.KEY_PRESS, 300, key="Space"),
        ev(RecordingEventType.KEY_PRESS, 400, key="ENTER"),
        pointer(RecordingEventType.POINTER_DOWN, 1000, 0, 0),
        pointer(RecordingEventType.POINTER_UP, 2000, 100, 100),
        ev(RecordingEventType.APP_STARTED, 3000),
        ev(RecordingEventType.NAVIGATION, 3100, text="http://x"),
    ]
    actions = EventNormalizer().normalize(events, RecordingMode.SMART)
    steps, warnings = actions_to_steps(actions, session_id="s", capabilities=None)
    assert [s.action for s in steps] == ["device.tap", "device.key", "device.key", "device.key",
                                         "device.key", "device.drag", "device.start", "log"]
    assert [s.params.get("key") for s in steps[1:5]] == ["H", "i", "SPACE", "ENTER"]
    assert steps[1].name == "Type 'Hi '" and steps[2].name is None
    assert steps[5].params == {"from_x": 0, "from_y": 0, "to_x": 100, "to_y": 100,
                               "duration": "1s"}
    assert all(s.provenance.source == "recording" and s.provenance.session_id == "s"
               for s in steps)
    assert warnings == []


def test_actions_to_steps_downgrades_drag_without_capability():
    events = [pointer(RecordingEventType.POINTER_DOWN, 0, 0, 0),
              pointer(RecordingEventType.POINTER_UP, 2000, 100, 100)]
    actions = EventNormalizer().normalize(events, RecordingMode.SMART)
    caps = PLATFORM_CAPABILITIES["web"].model_copy(update={"supports_drag": False})
    steps, warnings = actions_to_steps(actions, session_id=None, capabilities=caps)
    assert steps[0].action == "device.swipe" and warnings


def test_event_sink_backpressure_drops_droppable_only():
    sink = EventSink(maxsize=2)
    e = ev(RecordingEventType.POINTER_MOVE, 0)
    assert sink.push(e, droppable=True) and sink.push(e, droppable=True)
    assert not sink.push(e, droppable=True) and sink.dropped == 1
    assert sink.pop(0.01) is e
    sink.close()
    assert not sink.push(e)
