from __future__ import annotations

from pathlib import Path

from argus_test_creator.adapters.android.getevent_parser import GetEventParser
from argus_test_creator.adapters.android.touch_state import TouchFrame, TouchState

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "android"


def frames_for(text: str) -> list[TouchFrame]:
    parser = GetEventParser()
    state = TouchState()
    out = []
    for event in parser.parse_lines(text):
        frame = state.feed(event)
        if frame is not None:
            out.append(frame)
    return out


def test_single_contact_lifecycle_from_tap_fixture():
    frames = frames_for((FIXTURES / "tap_event.txt").read_text())
    assert len(frames) == 3
    first, second, third = frames
    assert len(first.started) == 1 and first.started[0].slot_id == 0
    assert first.started[0].tracking_id == 0x2A
    assert (first.started[0].x, first.started[0].y) == (0x200, 0x400)
    assert len(first.active) == 1
    assert len(second.moved) == 1 and second.moved[0].x == 0x202
    assert len(third.ended) == 1 and third.active == ()
    assert third.ended[0].start_time == 10.0 and third.ended[0].time == 10.12


def test_position_only_reported_on_syn_report():
    state = TouchState()
    parser = GetEventParser()
    e1 = parser.parse_line("[1.0] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000001")
    e2 = parser.parse_line("[1.0] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 00000010")
    assert state.feed(e1) is None and state.feed(e2) is None
    # No Y yet → not a usable position, so the frame reports nothing started yet.
    syn = parser.parse_line("[1.0] /dev/input/event2: EV_SYN SYN_REPORT 00000000")
    frame = state.feed(syn)
    assert frame is not None and frame.started == () and frame.active == ()
    e3 = parser.parse_line("[1.1] /dev/input/event2: EV_ABS ABS_MT_POSITION_Y 00000020")
    state.feed(e3)
    frame = state.feed(parser.parse_line("[1.1] /dev/input/event2: EV_SYN SYN_REPORT 00000000"))
    assert frame is not None and len(frame.started) == 1
    assert frame.started[0].start_time == 1.0


def test_two_slots_tracked_independently():
    frames = frames_for((FIXTURES / "multitouch_event.txt").read_text())
    assert len(frames[0].started) == 1 and frames[0].started[0].slot_id == 0
    assert len(frames[1].started) == 1 and frames[1].started[0].slot_id == 1
    assert len(frames[1].active) == 2
    moved = frames[2].moved
    assert {m.slot_id for m in moved} == {0, 1}
    by_slot = {m.slot_id: m.x for m in moved}
    assert by_slot == {0: 0x180, 1: 0x680}
    last = frames[-1]
    assert {e.slot_id for e in last.ended} == {0, 1}
    assert last.active == ()
    # Trajectories are kept per slot.
    ended0 = next(e for e in last.ended if e.slot_id == 0)
    assert [s[0] for s in ended0.samples] == [0x200, 0x180, 0x100]


def test_events_do_not_all_go_to_slot_zero():
    parser = GetEventParser()
    state = TouchState()
    lines = [
        "[1.0] /dev/input/event2: EV_ABS ABS_MT_SLOT 00000003",
        "[1.0] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000007",
        "[1.0] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 00000005",
        "[1.0] /dev/input/event2: EV_ABS ABS_MT_POSITION_Y 00000006",
        "[1.0] /dev/input/event2: EV_SYN SYN_REPORT 00000000",
    ]
    frame = None
    for line in lines:
        frame = state.feed(parser.parse_line(line))
    assert frame is not None and frame.started[0].slot_id == 3
    assert state.slot(0).active is False


def test_legacy_single_touch_protocol_with_btn_touch():
    parser = GetEventParser()
    state = TouchState()
    lines = [
        "[1.0] /dev/input/event1: EV_KEY BTN_TOUCH DOWN",
        "[1.0] /dev/input/event1: EV_ABS ABS_X 00000064",
        "[1.0] /dev/input/event1: EV_ABS ABS_Y 000000c8",
        "[1.0] /dev/input/event1: EV_SYN SYN_REPORT 00000000",
        "[1.2] /dev/input/event1: EV_KEY BTN_TOUCH UP",
        "[1.2] /dev/input/event1: EV_SYN SYN_REPORT 00000000",
    ]
    frames = [f for f in (state.feed(parser.parse_line(ln)) for ln in lines) if f is not None]
    assert len(frames) == 2
    assert frames[0].started[0].x == 100 and frames[0].started[0].y == 200
    assert len(frames[1].ended) == 1 and frames[1].active == ()


def test_samples_are_bounded():
    parser = GetEventParser()
    state = TouchState(max_samples=10)
    state.feed(parser.parse_line("[0.0] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000001"))
    state.feed(parser.parse_line("[0.0] /dev/input/event2: EV_ABS ABS_MT_POSITION_Y 00000000"))
    for i in range(100):
        state.feed(parser.parse_line(f"[{i / 100:.2f}] /dev/input/event2: EV_ABS ABS_MT_POSITION_X {i:08x}"))  # noqa: E501
        frame = state.feed(parser.parse_line(f"[{i / 100:.2f}] /dev/input/event2: EV_SYN SYN_REPORT 00000000"))  # noqa: E501
    assert frame is not None
    samples = frame.active[0].samples
    assert len(samples) == 10
    assert samples[-1][0] == 99  # the latest sample always wins


def test_reset_clears_everything():
    state = TouchState()
    parser = GetEventParser()
    state.feed(parser.parse_line("[0.0] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000001"))
    state.feed(parser.parse_line("[0.0] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 00000001"))
    state.feed(parser.parse_line("[0.0] /dev/input/event2: EV_ABS ABS_MT_POSITION_Y 00000001"))
    state.feed(parser.parse_line("[0.0] /dev/input/event2: EV_SYN SYN_REPORT 00000000"))
    assert state.active_count == 1
    state.reset()
    assert state.active_count == 0
