from __future__ import annotations

from pathlib import Path

from argus_test_creator.adapters.android.getevent_parser import GetEventParser, parse_input_devices
from argus_test_creator.adapters.android.models import EventType

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "android"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_timestamped_abs_line():
    parser = GetEventParser()
    event = parser.parse_line(
        "[   10.000000] /dev/input/event2: EV_ABS       ABS_MT_POSITION_X    00000200\n")
    assert event is not None
    assert event.timestamp == 10.0
    assert event.device == "/dev/input/event2"
    assert event.event_type == EventType.EV_ABS
    assert event.code == "ABS_MT_POSITION_X"
    assert event.value == 0x200
    assert event.is_known
    assert parser.parsed == 1 and parser.malformed == 0


def test_named_key_values_and_missing_timestamp():
    parser = GetEventParser()
    down = parser.parse_line("/dev/input/event0: EV_KEY       KEY_BACK             DOWN")
    up = parser.parse_line("/dev/input/event0: EV_KEY       KEY_BACK             UP")
    assert down is not None and up is not None
    assert down.timestamp is None
    assert (down.value, up.value) == (1, 0)


def test_tracking_id_ffffffff_is_minus_one():
    event = GetEventParser().parse_line(
        "[ 1.5 ] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID ffffffff")
    assert event is not None and event.value == -1


def test_syn_report_detection():
    event = GetEventParser().parse_line(
        "[   10.000000] /dev/input/event2: EV_SYN       SYN_REPORT           00000000")
    assert event is not None and event.is_syn_report


def test_malformed_lines_are_counted_not_raised():
    parser = GetEventParser()
    events = parser.parse_lines(fixture("malformed_event.txt"))
    assert parser.malformed == 4  # add device, name:, prose, bad hex value
    assert parser.last_malformed is not None
    codes = [e.code for e in events]
    assert "ABS_MT_POSITION_X" in codes and "SYN_REPORT" in codes
    assert "zz" not in codes


def test_unknown_types_and_codes_are_preserved():
    parser = GetEventParser()
    unnamed = parser.parse_line(
        "[   60.000000] /dev/input/event2: 0005         0042                 00000001")
    assert unnamed is not None
    assert unnamed.event_type == EventType.EV_SW  # numeric type resolved when known
    assert unnamed.code == "0042"
    assert unnamed.raw == {"type": "0005", "code": "0042"}
    assert not unnamed.is_known
    weird = parser.parse_line("[ 1 ] /dev/input/event9: 00ff 0001 00000001")
    assert weird is not None and weird.event_type == EventType.UNKNOWN
    assert parser.unknown == 2


def test_blank_lines_ignored():
    parser = GetEventParser()
    assert parser.parse_line("\n") is None
    assert parser.parsed == 0 and parser.malformed == 0


def test_fixture_streams_parse_completely():
    for name in ("tap_event.txt", "swipe_event.txt", "long_press_event.txt",
                 "multitouch_event.txt", "key_event.txt"):
        parser = GetEventParser()
        text = fixture(name)
        events = parser.parse_lines(text)
        assert len(events) == len([ln for ln in text.splitlines() if ln.strip()])
        assert parser.malformed == 0


def test_parse_input_devices_listing():
    devices = parse_input_devices(fixture("getevent_lp.txt"))
    assert [d.path for d in devices] == ["/dev/input/event5", "/dev/input/event2",
                                         "/dev/input/event3"]
    keys, touch, fp = devices
    assert keys.name == "gpio-keys" and not keys.is_touchscreen and keys.has_keys
    assert "KEY_VOLUMEUP" in keys.key_codes
    assert touch.name == "sec_touchscreen"
    assert touch.is_touchscreen and touch.uses_mt_protocol and touch.is_direct
    assert touch.x_range() is not None and touch.x_range().max == 4095
    assert touch.axis_ranges["ABS_MT_SLOT"].max == 9
    assert "BTN_TOUCH" in touch.key_codes
    assert not fp.is_touchscreen


def test_parse_input_devices_single_touch_protocol():
    text = """add device 1: /dev/input/event1
  name:     "resistive_ts"
  events:
    KEY (0001): BTN_TOUCH
    ABS (0003): ABS_X                 : value 0, min 0, max 1023, fuzz 0, flat 0, resolution 0
                ABS_Y                 : value 0, min 0, max 767, fuzz 0, flat 0, resolution 0
  input props:
    <none>
"""
    (device,) = parse_input_devices(text)
    assert device.is_touchscreen and not device.uses_mt_protocol
    assert device.y_range().max == 767
