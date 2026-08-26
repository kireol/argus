from __future__ import annotations

from pathlib import Path

from argus_test_creator.adapters.android.coordinates import AndroidCoordinateMapper
from argus_test_creator.adapters.android.gestures import AndroidGestureRecognizer, GestureConfig
from argus_test_creator.adapters.android.getevent_parser import GetEventParser
from argus_test_creator.adapters.android.models import (
    AxisRange,
    GestureKind,
    KeyPress,
    LongPress,
    MultiTouch,
    Swipe,
    Tap,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "android"
TS = "/dev/input/event2"


def identity_mapper() -> AndroidCoordinateMapper:
    return AndroidCoordinateMapper(x_range=AxisRange(min=0, max=4095),
                                   y_range=AxisRange(min=0, max=4095),
                                   natural_width=4096, natural_height=4096)


def recognize(text: str, *, config: GestureConfig | None = None,
              mapper: AndroidCoordinateMapper | None = None, touch_device: str | None = TS):
    rec = AndroidGestureRecognizer(mapper=mapper or identity_mapper(), config=config,
                                   touch_device=touch_device, clock=lambda: 999.0)
    parser = GetEventParser()
    out = []
    for event in parser.parse_lines(text):
        out.extend(rec.feed(event))
    return rec, out


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_tap_fixture_becomes_one_tap():
    rec, gestures = recognize(fixture("tap_event.txt"))
    assert len(gestures) == 1
    tap = gestures[0]
    assert isinstance(tap, Tap)
    assert (tap.x, tap.y) == (0x200, 0x400)
    assert tap.duration_ms == 120
    assert tap.timestamp == 10.0
    assert tap.raw_event_count > 5
    assert rec.recognized == 1


def test_swipe_fixture_becomes_one_swipe_not_many_steps():
    _, gestures = recognize(fixture("swipe_event.txt"))
    assert len(gestures) == 1
    swipe = gestures[0]
    assert isinstance(swipe, Swipe)
    assert (swipe.start_x, swipe.start_y) == (0x200, 0xC00)
    assert (swipe.end_x, swipe.end_y) == (0x200, 0x400)
    assert swipe.duration_ms == 180
    assert 2 <= len(swipe.path) <= 16
    assert swipe.path[0].y == 0xC00 and swipe.path[-1].y == 0x400


def test_long_press_fixture():
    _, gestures = recognize(fixture("long_press_event.txt"))
    assert len(gestures) == 1
    lp = gestures[0]
    assert isinstance(lp, LongPress)
    assert (lp.x, lp.y) == (0x300, 0x300)
    assert lp.duration_ms == 900


def test_multitouch_fixture_is_one_gesture_never_two_taps():
    _, gestures = recognize(fixture("multitouch_event.txt"))
    assert len(gestures) == 1
    mt = gestures[0]
    assert isinstance(mt, MultiTouch)
    assert mt.finger_count == 2
    first, second = mt.fingers
    assert first[0].x == 0x200 and first[-1].x == 0x100
    assert second[0].x == 0x600 and second[-1].x == 0x700
    assert mt.duration_ms == 300


def test_key_fixture_maps_and_preserves_unknown():
    _, gestures = recognize(fixture("key_event.txt"))
    assert [g.kind for g in gestures] == [GestureKind.KEY_PRESS, GestureKind.KEY_PRESS]
    back, volume = gestures
    assert isinstance(back, KeyPress) and back.key == "BACK" and back.mapped
    assert back.duration_ms == 80 and back.linux_key == "KEY_BACK"
    assert isinstance(volume, KeyPress) and volume.key == "VOLUME_UP"


def test_thresholds_are_configurable():
    cfg = GestureConfig(long_press_min_ms=100, tap_max_distance_px=1)
    _, gestures = recognize(fixture("tap_event.txt"), config=cfg)
    # 2px movement > 1px and 120ms > 100ms → classified as long press by the new thresholds?
    # movement exceeds the tap distance → swipe wins.
    assert isinstance(gestures[0], Swipe)
    cfg = GestureConfig(long_press_min_ms=100, tap_max_distance_px=20)
    _, gestures = recognize(fixture("tap_event.txt"), config=cfg)
    assert isinstance(gestures[0], LongPress)


def test_coordinates_are_mapped_to_screen():
    mapper = AndroidCoordinateMapper(x_range=AxisRange(min=0, max=4095),
                                     y_range=AxisRange(min=0, max=4095),
                                     natural_width=1080, natural_height=2400)
    _, gestures = recognize(fixture("tap_event.txt"), mapper=mapper)
    tap = gestures[0]
    assert isinstance(tap, Tap)
    assert abs(tap.x - 135) <= 1 and abs(tap.y - 600) <= 1


def test_events_from_other_devices_are_ignored_for_touch():
    text = fixture("tap_event.txt").replace("/dev/input/event2", "/dev/input/event7")
    rec, gestures = recognize(text, touch_device=TS)
    assert gestures == []
    assert rec.ignored > 0


def test_flush_finishes_gesture_in_progress():
    lines = fixture("tap_event.txt").splitlines()[:7]  # down + first SYN only
    rec, gestures = recognize("\n".join(lines))
    assert gestures == [] and rec.in_gesture
    flushed = rec.flush()
    assert len(flushed) == 1 and flushed[0].metadata.get("incomplete") is True
    assert not rec.in_gesture


def test_sequential_taps_remain_separate():
    text = fixture("tap_event.txt") + "\n" + fixture("tap_event.txt").replace("10.", "11.")
    _, gestures = recognize(text)
    assert [g.kind for g in gestures] == [GestureKind.TAP, GestureKind.TAP]


def test_high_frequency_swipe_collapses_to_one_gesture():
    lines = ["[0.000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000001",
             "[0.000] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 00000000",
             "[0.000] /dev/input/event2: EV_ABS ABS_MT_POSITION_Y 00000000",
             "[0.000] /dev/input/event2: EV_SYN SYN_REPORT 00000000"]
    for i in range(1, 2000):
        t = i / 1000
        lines.append(f"[{t:.3f}] /dev/input/event2: EV_ABS ABS_MT_POSITION_X {i:08x}")
        lines.append(f"[{t:.3f}] /dev/input/event2: EV_SYN SYN_REPORT 00000000")
    lines += ["[2.000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID ffffffff",
              "[2.000] /dev/input/event2: EV_SYN SYN_REPORT 00000000"]
    _, gestures = recognize("\n".join(lines))
    assert len(gestures) == 1 and isinstance(gestures[0], Swipe)
    assert gestures[0].end_x == 1999 and len(gestures[0].path) == 16
