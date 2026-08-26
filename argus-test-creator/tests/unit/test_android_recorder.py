from __future__ import annotations

import time
from pathlib import Path

import pytest

from argus_test_creator.adapters.android.fake_adb import FakeAdbClient, FakeDevice
from argus_test_creator.adapters.android.getevent_parser import parse_input_devices
from argus_test_creator.adapters.android.recorder import AndroidRecorder, select_touchscreen
from argus_test_creator.core.errors import RecordingError, TargetConnectionError
from argus_test_creator.models.recording import RecordingEvent, RecordingEventType
from argus_test_creator.recording.adapter import EventSink
from argus_test_creator.targets import builtin_targets

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "android"


def android_target(**settings):
    target = next(t for t in builtin_targets() if t.adapter == "android")
    return target.model_copy(update={"settings": {**target.settings, **settings}})


def drain(sink: EventSink, *, expected: int, timeout: float = 5.0) -> list[RecordingEvent]:
    out: list[RecordingEvent] = []
    deadline = time.monotonic() + timeout
    while len(out) < expected and time.monotonic() < deadline:
        event = sink.pop(timeout=0.1)
        if event is not None:
            out.append(event)
            sink.task_done()
    return out


def test_connect_requires_explicit_choice_with_several_devices():
    fake = FakeAdbClient([FakeDevice("A1"), FakeDevice("B2")])
    recorder = AndroidRecorder(android_target(), adb=fake)
    with pytest.raises(TargetConnectionError) as info:
        recorder.connect()
    assert "choose one" in info.value.message
    assert "A1" in (info.value.remediation or "") and "B2" in (info.value.remediation or "")
    recorder.select_device("B2")
    recorder.connect()
    assert recorder.serial == "B2"
    # every subsequent adb call carries the chosen serial
    assert all(call[0] in ("devices", "B2") for call in fake.calls)


def test_connect_reports_unauthorized_and_missing_devices():
    fake = FakeAdbClient([FakeDevice("A1", state="unauthorized")])
    with pytest.raises(TargetConnectionError) as info:
        AndroidRecorder(android_target(), adb=fake).connect()
    assert "unauthorized" in info.value.message
    assert "Allow USB debugging" in (info.value.remediation or "")
    with pytest.raises(TargetConnectionError):
        AndroidRecorder(android_target(serial="ZZZ"), adb=fake).connect()
    with pytest.raises(TargetConnectionError) as info:
        AndroidRecorder(android_target(), adb=FakeAdbClient()).connect()
    assert "No Android device" in info.value.message


def test_connect_discovers_touchscreen_and_capabilities():
    fake = FakeAdbClient([FakeDevice("A1", width=1080, height=2400)])
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    assert recorder.connected
    assert recorder.touchscreen is not None
    assert recorder.touchscreen.path == "/dev/input/event2"  # discovered, not hardcoded
    assert recorder.screen_size() == (1080, 2400)
    caps = recorder.capabilities
    assert caps.supports_input_recording and caps.supports_touch and caps.supports_multi_touch
    assert caps.supports_hardware_keys  # gpio-keys has KEY_BACK / volume
    assert caps.supports_screenshot and caps.supports_live_screen
    snap = recorder.diagnostics.snapshot()
    assert snap.touchscreen and snap.input_device == "/dev/input/event2"
    assert snap.model == "Pixel 8" and snap.android_version == "14"


def test_select_touchscreen_prefers_direct_mt_and_keeps_candidates():
    listing = (FIXTURES / "getevent_lp.txt").read_text() + """add device 4: /dev/input/event8
  name:     "stylus_pad"
  events:
    KEY (0001): BTN_TOUCH
    ABS (0003): ABS_X                 : value 0, min 0, max 500, fuzz 0, flat 0, resolution 0
                ABS_Y                 : value 0, min 0, max 500, fuzz 0, flat 0, resolution 0
  input props:
    <none>
"""
    devices = parse_input_devices(listing)
    chosen, candidates = select_touchscreen(devices)
    assert chosen is not None and chosen.name == "sec_touchscreen"
    assert [c.name for c in candidates] == ["sec_touchscreen", "stylus_pad"]
    override, _ = select_touchscreen(devices, override="stylus_pad")
    assert override is not None and override.path == "/dev/input/event8"
    with pytest.raises(TargetConnectionError):
        select_touchscreen(devices, override="/dev/input/event99")
    assert select_touchscreen([devices[0]]) == (None, [])


def test_recording_turns_fixture_streams_into_semantic_events():
    fake = FakeAdbClient([FakeDevice("A1", width=4096, height=4096)])
    for name in ("tap_event.txt", "swipe_event.txt", "long_press_event.txt",
                 "multitouch_event.txt", "key_event.txt"):
        fake.script_fixture("A1", FIXTURES / name)
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    sink = EventSink()
    recorder.start_recording(sink)
    events = drain(sink, expected=6)
    recorder.stop_recording()
    kinds = [(e.event_type, e.metadata.get("gesture"), e.key) for e in events]
    assert kinds == [
        (RecordingEventType.GESTURE, "tap", None),
        (RecordingEventType.GESTURE, "swipe", None),
        (RecordingEventType.GESTURE, "long_press", None),
        (RecordingEventType.GESTURE, "multi_touch", None),
        (RecordingEventType.KEY_PRESS, None, "BACK"),
        (RecordingEventType.KEY_PRESS, None, "VOLUME_UP"),
    ]
    tap = events[0]
    assert tap.position is not None and (tap.position.x, tap.position.y) == (0x200, 0x400)
    swipe = events[1]
    assert swipe.position_end is not None and swipe.position_end.y == 0x400
    assert events[3].metadata["fingers"] and len(events[3].metadata["fingers"]) == 2
    assert events[4].metadata["linux_key"] == "KEY_BACK" and events[4].metadata["mapped"]
    assert events[0].timestamp < events[5].timestamp
    snap = recorder.diagnostics.snapshot()
    assert snap.raw_events > 50 and snap.recognized == 6
    assert not snap.stream_alive
    assert fake.devices["A1"].stop_calls >= 1  # no orphaned getevent
    assert not recorder.recording


def test_coordinates_are_scaled_to_the_device_screen():
    fake = FakeAdbClient([FakeDevice("A1", width=1080, height=2400)])
    fake.script_fixture("A1", FIXTURES / "tap_event.txt")
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    sink = EventSink()
    recorder.start_recording(sink)
    (tap,) = drain(sink, expected=1)
    recorder.stop_recording()
    assert tap.position is not None
    assert abs(tap.position.x - 135) <= 1 and abs(tap.position.y - 600) <= 1


def test_landscape_device_maps_into_rotated_screen():
    fake = FakeAdbClient([FakeDevice("A1", width=1080, height=2400, rotation=1)])
    fake.script_fixture("A1", FIXTURES / "tap_event.txt")
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    assert recorder.screen_size() == (2400, 1080)
    sink = EventSink()
    recorder.start_recording(sink)
    (tap,) = drain(sink, expected=1)
    recorder.stop_recording()
    assert tap.position is not None
    assert 0 <= tap.position.x < 2400 and 0 <= tap.position.y < 1080
    assert abs(tap.position.x - 600) <= 1 and abs(tap.position.y - (1079 - 135)) <= 1


def test_inverted_axes_setting():
    fake = FakeAdbClient([FakeDevice("A1", width=4096, height=4096)])
    fake.script_fixture("A1", FIXTURES / "tap_event.txt")
    recorder = AndroidRecorder(android_target(invert_x=True, invert_y=True), adb=fake)
    recorder.connect()
    sink = EventSink()
    recorder.start_recording(sink)
    (tap,) = drain(sink, expected=1)
    recorder.stop_recording()
    assert tap.position is not None
    assert (tap.position.x, tap.position.y) == (4095 - 0x200, 4095 - 0x400)


def test_disconnect_mid_recording_emits_connection_lost_and_reconnect_restores():
    fake = FakeAdbClient([FakeDevice("A1", width=4096, height=4096)])
    fake.script_fixture("A1", FIXTURES / "tap_event.txt")
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    sink = EventSink()
    recorder.start_recording(sink)
    (tap,) = drain(sink, expected=1)
    fake.disconnect("A1")
    (lost,) = drain(sink, expected=1)
    assert lost.event_type == RecordingEventType.CONNECTION_LOST
    assert "disconnected" in lost.metadata["error"]
    assert recorder.target_lost and not recorder.connected
    with pytest.raises(TargetConnectionError):
        recorder.reconnect()
    fake.reconnect("A1")
    fake.script_fixture("A1", FIXTURES / "key_event.txt")
    recorder.reconnect()
    restored, back, _vol = drain(sink, expected=3)
    assert restored.event_type == RecordingEventType.CONNECTION_RESTORED
    assert back.key == "BACK"
    assert recorder.connected and not recorder.target_lost
    recorder.stop_recording()
    assert fake.devices["A1"].streams_started == 2


def test_stream_that_exits_while_device_present_is_restarted_then_reported():
    device = FakeDevice("A1", width=4096, height=4096)
    device.end_after_script = True
    fake = FakeAdbClient([device])
    fake.script_fixture("A1", FIXTURES / "tap_event.txt")
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    sink = EventSink()
    recorder.start_recording(sink)
    events = drain(sink, expected=2, timeout=6)
    assert events[0].metadata.get("gesture") == "tap"
    assert events[1].event_type == RecordingEventType.CONNECTION_LOST
    assert "getevent stopped" in events[1].metadata["error"]
    assert device.streams_started == 3  # original + 2 restart attempts
    recorder.stop_recording()


def test_start_recording_requires_connection_and_rejects_double_start():
    fake = FakeAdbClient([FakeDevice("A1")])
    recorder = AndroidRecorder(android_target(), adb=fake)
    with pytest.raises(RecordingError):
        recorder.start_recording(EventSink())
    recorder.connect()
    sink = EventSink()
    recorder.start_recording(sink)
    with pytest.raises(RecordingError):
        recorder.start_recording(sink)
    recorder.disconnect()
    assert not recorder.recording and not recorder.connected


def test_stop_mid_gesture_flushes_incomplete_gesture():
    fake = FakeAdbClient([FakeDevice("A1", width=4096, height=4096)])
    lines = (FIXTURES / "tap_event.txt").read_text().splitlines(keepends=True)[:7]
    fake.script_lines("A1", lines)
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    sink = EventSink()
    recorder.start_recording(sink)
    time.sleep(0.3)
    recorder.stop_recording()
    (tap,) = drain(sink, expected=1, timeout=1)
    assert tap.metadata.get("gesture") == "tap" and tap.metadata.get("incomplete") is True


def test_controlled_input_still_works_and_uses_serial():
    fake = FakeAdbClient([FakeDevice("A1")])
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    sink = EventSink()
    recorder.start_recording(sink)
    recorder.send_tap(10, 20)
    recorder.send_key("BACK")
    recorder.send_text("hi")
    events = drain(sink, expected=4)
    recorder.stop_recording()
    assert [e.event_type for e in events] == [
        RecordingEventType.CLICK, RecordingEventType.KEY_PRESS, RecordingEventType.KEY_PRESS,
        RecordingEventType.KEY_PRESS,
    ]
    assert ("A1", "shell", "input", "tap", "10", "20") in fake.calls
    assert ("A1", "shell", "input", "keyevent", "KEYCODE_BACK") in fake.calls


def test_no_touchscreen_limits_capabilities_but_still_records_keys():
    listing = """add device 1: /dev/input/event5
  name:     "gpio-keys"
  events:
    KEY (0001): KEY_VOLUMEDOWN        KEY_VOLUMEUP          KEY_BACK
  input props:
    <none>
"""
    fake = FakeAdbClient([FakeDevice("A1", listing=listing)])
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    assert recorder.touchscreen is None
    caps = recorder.capabilities
    assert not caps.supports_touch and caps.supports_hardware_keys and caps.supports_input_recording
    assert any("No touchscreen" in note for note in recorder.describe_limitations())
