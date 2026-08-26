"""GESTURE events → NormalizedAction → StepDraft: the generic pipeline changes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from argus_test_creator.models import (
    NormalizedActionKind,
    Point,
    RecorderCapabilities,
    RecordingEvent,
    RecordingEventType,
    RecordingMode,
)
from argus_test_creator.models.authoring import AuthoringDocument
from argus_test_creator.recording import EventNormalizer, actions_to_steps
from argus_test_creator.serialization import document_to_yaml

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def gesture(name: str, ms: int, **fields) -> RecordingEvent:
    meta = fields.pop("metadata", {})
    return RecordingEvent(event_type=RecordingEventType.GESTURE,
                          timestamp=T0 + timedelta(milliseconds=ms),
                          metadata={"gesture": name, **meta}, **fields)


def test_gesture_events_map_one_to_one_to_actions():
    events = [
        gesture("tap", 0, position=Point(x=10, y=20), duration_ms=90),
        gesture("swipe", 1000, position=Point(x=10, y=500), position_end=Point(x=10, y=100),
                duration_ms=180),
        gesture("long_press", 2000, position=Point(x=30, y=40), duration_ms=900),
        gesture("multi_touch", 3000, position=Point(x=1, y=1), duration_ms=300, metadata={
            "fingers": [[{"x": 1, "y": 1, "t": 0.0}, {"x": 5, "y": 1, "t": 0.3}],
                        [{"x": 9, "y": 1, "t": 0.0}, {"x": 5, "y": 1, "t": 0.3}]],
        }),
        RecordingEvent(event_type=RecordingEventType.KEY_PRESS, key="BACK",
                       timestamp=T0 + timedelta(seconds=4)),
        gesture("unknown-kind", 5000, position=Point(x=0, y=0)),
    ]
    for mode in (RecordingMode.EXACT, RecordingMode.SMART):
        actions = EventNormalizer().normalize(events, mode)
        assert [a.kind for a in actions] == [
            NormalizedActionKind.TAP, NormalizedActionKind.SWIPE, NormalizedActionKind.LONG_PRESS,
            NormalizedActionKind.MULTI_TOUCH, NormalizedActionKind.KEY,
        ]
        assert actions[1].position_end == Point(x=10, y=100)
        assert actions[2].duration_ms == 900
        assert actions[3].describe() == "Multi-touch (2 fingers)"
        assert actions[0].source_event_ids == (events[0].id,)


def test_two_quick_gesture_taps_become_double_tap_in_smart_mode():
    events = [gesture("tap", 0, position=Point(x=10, y=10)),
              gesture("tap", 200, position=Point(x=12, y=11))]
    smart = EventNormalizer().normalize(events, RecordingMode.SMART)
    exact = EventNormalizer().normalize(events, RecordingMode.EXACT)
    assert [a.kind for a in smart] == [NormalizedActionKind.DOUBLE_TAP]
    assert [a.kind for a in exact] == [NormalizedActionKind.TAP, NormalizedActionKind.TAP]


def test_connection_events_carry_no_action():
    events = [
        RecordingEvent(event_type=RecordingEventType.CONNECTION_LOST, timestamp=T0),
        RecordingEvent(event_type=RecordingEventType.CONNECTION_RESTORED, timestamp=T0),
    ]
    assert EventNormalizer().normalize(events, RecordingMode.SMART) == []


def test_steps_for_android_gestures_are_semantic_argus_actions():
    events = [
        gesture("tap", 0, position=Point(x=10, y=20)),
        gesture("swipe", 1000, position=Point(x=10, y=500), position_end=Point(x=10, y=100),
                duration_ms=180),
        gesture("long_press", 2000, position=Point(x=30, y=40), duration_ms=900),
        gesture("multi_touch", 3000, position=Point(x=1, y=1), duration_ms=300, metadata={
            "fingers": [[{"x": 1, "y": 1, "t": 0.0}, {"x": 5, "y": 1, "t": 0.3}],
                        [{"x": 9, "y": 1, "t": 0.0}, {"x": 5, "y": 1, "t": 0.3}]],
        }),
        RecordingEvent(event_type=RecordingEventType.KEY_PRESS, key="BACK",
                       timestamp=T0 + timedelta(seconds=4)),
    ]
    actions = EventNormalizer().normalize(events, RecordingMode.SMART)
    caps = RecorderCapabilities(supports_tap=True, supports_swipe=True, supports_long_press=True,
                                supports_multi_touch=True, supports_keyboard=True)
    steps, warnings = actions_to_steps(actions, session_id="s", capabilities=caps)
    assert warnings == []
    assert [s.action for s in steps] == [
        "device.tap", "device.swipe", "device.long_press", "device.multi_touch", "device.key",
    ]
    assert steps[1].params == {"from_x": 10, "from_y": 500, "to_x": 10, "to_y": 100,
                               "duration": "180ms"}
    assert steps[2].params == {"x": 30, "y": 40, "duration": "900ms"}
    assert steps[3].params["fingers"] == [
        {"from_x": 1, "from_y": 1, "to_x": 5, "to_y": 1},
        {"from_x": 9, "from_y": 1, "to_x": 5, "to_y": 1},
    ]
    assert steps[4].params == {"key": "BACK"}
    document = AuthoringDocument()
    document.metadata.id = "AND-1"
    document.metadata.name = "android"
    document.steps.extend(steps)
    yaml_text = document_to_yaml(document)
    assert "device.multi_touch" in yaml_text
    for forbidden in ("EV_ABS", "EV_SYN", "getevent", "ABS_MT", "/dev/input"):
        assert forbidden not in yaml_text


def test_multi_touch_without_capability_degrades_to_log_with_warning():
    events = [gesture("multi_touch", 0, position=Point(x=1, y=1), metadata={
        "fingers": [[{"x": 1, "y": 1, "t": 0.0}], [{"x": 9, "y": 1, "t": 0.0}]]})]
    actions = EventNormalizer().normalize(events, RecordingMode.SMART)
    steps, warnings = actions_to_steps(actions, session_id=None,
                                       capabilities=RecorderCapabilities(supports_tap=True))
    assert steps[0].action == "log" and warnings
