from __future__ import annotations

import pytest

from argus_test_creator.core.errors import RecordingError, TargetConnectionError
from argus_test_creator.models import NormalizedActionKind, RecordingMode
from argus_test_creator.recording import RecordingSession, actions_to_steps
from argus_test_creator.recording.session import (
    ActionObserved,
    ActionUpdated,
    AssertionSuggested,
    RecordingFailed,
    RecordingStarted,
    RecordingStopped,
    ScreenChanged,
    ScreenshotCaptured,
)
from tests.conftest import settle, wait_jobs

pytestmark = pytest.mark.integration


def test_full_fake_recording_flow(session, recorder, events, workers):
    seen: dict[str, list] = {k: [] for k in ("started", "observed", "updated", "shot", "changed",
                                              "suggested", "stopped", "failed")}
    events.subscribe(RecordingStarted, seen["started"].append)
    events.subscribe(ActionObserved, seen["observed"].append)
    events.subscribe(ActionUpdated, seen["updated"].append)
    events.subscribe(ScreenshotCaptured, seen["shot"].append)
    events.subscribe(ScreenChanged, seen["changed"].append)
    events.subscribe(AssertionSuggested, seen["suggested"].append)
    events.subscribe(RecordingStopped, seen["stopped"].append)
    events.subscribe(RecordingFailed, seen["failed"].append)

    session.start()
    recorder.send_tap(200, 250)   # Movies
    recorder.send_tap(200, 180)   # Search
    recorder.send_tap(300, 190)   # focus input
    recorder.send_text("Batman")
    recorder.send_key("ENTER")
    recorder.send_long_press(160, 200)
    recorder.send_drag(600, 500, 600, 200)
    actions = session.stop()
    wait_jobs(workers)

    kinds = [a.kind for a in actions]
    assert kinds == [NormalizedActionKind.TAP] * 3 + [
        NormalizedActionKind.TYPE_TEXT, NormalizedActionKind.KEY,
        NormalizedActionKind.LONG_PRESS, NormalizedActionKind.DRAG,
    ]
    assert actions[3].text == "Batman"
    assert len(seen["observed"]) == 7 and len(seen["updated"]) == 5  # typing extends, not dup
    assert seen["started"] and seen["stopped"][0].action_count == 7
    assert seen["failed"] == []
    # every gesture end has an after-capture, plus the start capture
    assert all(a.capture_after for a in actions)
    assert len(session.captures) >= 8
    # screen changes were detected and text suggested
    assert seen["changed"]
    suggested_text = {c.condition.params.get("text") for e in seen["suggested"]
                      for c in e.candidates}
    assert "Batman Begins" in suggested_text
    # journal on disk
    assert (session.directory / "events.jsonl").is_file()
    assert (session.directory / "session.json").is_file()
    assert session.event_count == len(session.raw_events) > 7

    steps, _ = actions_to_steps(actions, session_id=session.id)
    assert steps[0].to_argus() == {"action": "device.tap", "x": 200, "y": 250}
    assert all(s.provenance.session_id == session.id for s in steps)


def test_pause_resume_and_renormalize(session, recorder, workers):
    session.start()
    recorder.send_tap(200, 250)
    session.pause()
    recorder.send_tap(200, 180)  # dropped while paused
    session.resume()
    recorder.send_key("a")
    recorder.send_key("b")
    actions = session.stop()
    wait_jobs(workers)
    assert [a.kind for a in actions] == [NormalizedActionKind.TAP, NormalizedActionKind.TYPE_TEXT]
    exact = session.renormalize(RecordingMode.EXACT)
    assert [a.kind for a in exact] == [NormalizedActionKind.TAP, NormalizedActionKind.KEY,
                                       NormalizedActionKind.KEY]


def test_recovery_after_crash(session, recorder, events, workers, project):
    session.start()
    recorder.send_tap(200, 250)
    recorder.send_tap(200, 180)
    settle(session)
    # Simulate a crash: never call stop(); just abandon the object.
    session._journal.close()
    recovered = RecordingSession.recover(session.directory, adapter=recorder, events=events,
                                         workers=workers)
    assert recovered.id == session.id
    assert [a.kind for a in recovered.actions] == [NormalizedActionKind.TAP] * 2
    assert len(recovered.captures) >= 2
    assert recovered.state.value == "stopped"
    from argus_test_creator.recording import SessionJournal

    assert SessionJournal.recoverable(project.sessions_dir) == []


def test_screenshot_failure_is_reported_not_fatal(session, recorder, events, workers):
    failures = []
    events.subscribe(RecordingFailed, failures.append)
    session.start()
    recorder.fail_screenshot = True
    recorder.send_tap(200, 250)
    settle(session)
    recorder.fail_screenshot = False
    recorder.send_tap(200, 180)
    actions = session.stop()
    wait_jobs(workers)
    assert len(actions) == 2
    assert failures and failures[0].operation == "screenshot"
    assert failures[0].remediation == "Reconnect device and retry."


def test_start_requires_connection_and_double_start_rejected(fake_target, project, events,
                                                             workers):
    from argus_test_creator.adapters.fake import FakeRecorder

    rec = FakeRecorder(fake_target, {"available": False})
    sess = RecordingSession(adapter=rec, directory=project.sessions_dir / "x", events=events,
                            workers=workers, settle_ms=0)
    with pytest.raises(TargetConnectionError):
        sess.start()
    rec.available = True
    sess.start()
    with pytest.raises(RecordingError):
        sess.start()
    sess.stop()
    assert sess.stop() == []  # idempotent


def test_capture_now_and_manual_ocr(session, recorder, workers):
    session.start()
    recorder.send_tap(200, 250)
    capture = session.capture_now()
    obs = session.run_ocr(capture)
    assert obs is not None and "Search" in obs.lines()
    assert session.ocr_for(capture.id) is obs
    session.stop()
