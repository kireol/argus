"""End-to-end with the fake ADB: device → session → actions → steps → Argus YAML."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from argus_test_creator.adapters.android import AndroidRecorder, FakeAdbClient, FakeDevice
from argus_test_creator.app import CreatorApp
from argus_test_creator.core.errors import RecordingError, TargetConnectionError
from argus_test_creator.core.events import EventBus
from argus_test_creator.core.workers import WorkerPool
from argus_test_creator.models import RecordingMode
from argus_test_creator.observation import FakeOCRProvider
from argus_test_creator.project import CreatorProject
from argus_test_creator.recording import (
    RecorderRegistry,
    RecordingSession,
    RecordingSessionState,
    TargetLost,
    TargetRestored,
)
from argus_test_creator.recording.session import ActionObserved, RecordingPaused
from argus_test_creator.serialization import load_document
from argus_test_creator.targets import builtin_targets

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "android"
ALL_FIXTURES = ("tap_event.txt", "swipe_event.txt", "long_press_event.txt",
                "multitouch_event.txt", "key_event.txt")


def android_target():
    return next(t for t in builtin_targets() if t.adapter == "android")


def wait_for(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


def test_fake_device_recording_produces_semantic_steps(tmp_path):
    fake = FakeAdbClient([FakeDevice("ABC123", width=1080, height=2400)])
    for name in ALL_FIXTURES:
        fake.script_fixture("ABC123", FIXTURES / name)
    recorder = AndroidRecorder(android_target(), adb=fake)
    events = EventBus()
    observed = []
    events.subscribe(ActionObserved, observed.append)
    project = CreatorProject.create(tmp_path / "proj", name="android")
    with WorkerPool(max_workers=2) as workers:
        session = RecordingSession(adapter=recorder, directory=project.sessions_dir / "s1",
                                   events=events, workers=workers, ocr=FakeOCRProvider(),
                                   mode=RecordingMode.SMART, settle_ms=0)
        session.start()
        wait_for(lambda: len(session.actions) >= 6)
        actions = session.stop()
        session.flush()
    kinds = [a.kind.value for a in actions]
    assert kinds == ["tap", "swipe", "long_press", "multi_touch", "key", "key"]
    assert len(observed) == 6
    # after-screenshots are associated with semantic actions, not raw events
    assert all(a.capture_after for a in actions)
    assert 0 <= actions[0].position.x < 1080 and 0 <= actions[0].position.y < 2400
    # raw journal contains only semantic events (no EV_* lines)
    journal_text = (project.sessions_dir / "s1" / "events.jsonl").read_text()
    assert "EV_ABS" not in journal_text and "SYN_REPORT" not in journal_text
    assert journal_text.count("\n") == 6


def test_app_flow_android_record_edit_save_yaml(tmp_path):
    fake = FakeAdbClient([FakeDevice("ABC123", width=1080, height=2400)])
    for name in ("tap_event.txt", "swipe_event.txt", "key_event.txt"):
        fake.script_fixture("ABC123", FIXTURES / name)
    registry = RecorderRegistry()
    registry.register("android", lambda target, options: AndroidRecorder(target, options,
                                                                          adb=fake))
    app = CreatorApp(registry=registry, ocr=FakeOCRProvider())
    app.config.recording.settle_ms = 0
    app.create_project(tmp_path / "proj", name="android")
    app.select_target("android")
    app.new_test(test_id="AND-001", name="Android flow", feature="android")
    app.connect_target()
    assert app.recorder is not None and app.recorder.connected
    assert app.recorder.target.argus_device_options.get("serial") in (None, "")
    session = app.start_recording()
    wait_for(lambda: len(session.actions) >= 4)
    steps = app.stop_recording()
    assert [s.action for s in steps] == ["device.tap", "device.swipe", "device.key",
                                         "device.key"]
    # edit a step like a user would, then save and reload the YAML
    app.authoring.rename_step(steps[0].id, "Tap Movies")
    path = app.save_test()
    text = path.read_text()
    for forbidden in ("getevent", "EV_", "ABS_MT", "/dev/input"):
        assert forbidden not in text
    assert "device.swipe" in text and "BACK" in text
    document = load_document(path)
    assert document.steps[0].name == "Tap Movies"
    assert app.authoring.document.target is not None
    assert app.authoring.document.target.adapter == "android"
    argus_config = (tmp_path / "proj" / "argus.yaml").read_text()
    assert "android" in argus_config
    app.shutdown()


def test_multiple_devices_require_explicit_selection_and_serial_is_used(tmp_path):
    fake = FakeAdbClient([FakeDevice("A1"), FakeDevice("B2", model="Galaxy S24")])
    registry = RecorderRegistry()
    registry.register("android", lambda t, o: AndroidRecorder(t, o, adb=fake))
    app = CreatorApp(registry=registry, ocr=FakeOCRProvider())
    app.create_project(tmp_path / "proj", name="android")
    app.select_target("android")
    with pytest.raises(TargetConnectionError):
        app.connect_target()
    devices = app.list_target_devices()
    assert [d.serial for d in devices] == ["A1", "B2"]
    app.select_target("android", {"serial": "B2"})
    app.connect_target()
    assert app.recorder.target.argus_device_options["serial"] == "B2"
    fake.calls.clear()
    app.recorder.screenshot()
    assert all(call[0] == "B2" for call in fake.calls)
    app.shutdown()


def test_disconnect_pauses_session_and_reconnect_resumes(tmp_path):
    fake = FakeAdbClient([FakeDevice("ABC123", width=4096, height=4096)])
    fake.script_fixture("ABC123", FIXTURES / "tap_event.txt")
    recorder = AndroidRecorder(android_target(), adb=fake)
    events = EventBus()
    seen: list = []
    for kind in (TargetLost, TargetRestored, RecordingPaused):
        events.subscribe(kind, seen.append)
    project = CreatorProject.create(tmp_path / "proj", name="android")
    with WorkerPool(max_workers=2) as workers:
        session = RecordingSession(adapter=recorder, directory=project.sessions_dir / "s1",
                                   events=events, workers=workers, settle_ms=0, suggest=False)
        session.start()
        wait_for(lambda: len(session.actions) == 1)
        fake.disconnect("ABC123")
        wait_for(lambda: session.state == RecordingSessionState.PAUSED)
        assert any(isinstance(e, TargetLost) for e in seen)
        assert session.target_lost
        with pytest.raises(RecordingError):
            session.resume()  # cannot resume a lost target by hand
        assert len(session.actions) == 1  # preserved
        fake.reconnect("ABC123")
        fake.script_fixture("ABC123", FIXTURES / "key_event.txt")
        recorder.reconnect()
        wait_for(lambda: session.state == RecordingSessionState.RECORDING)
        wait_for(lambda: len(session.actions) == 3)
        actions = session.stop()
    assert [a.kind.value for a in actions] == ["tap", "key", "key"]
    assert any(isinstance(e, TargetRestored) for e in seen)
    snapshot = project.sessions_dir / "s1" / "session.json"
    assert snapshot.is_file()


def test_rotation_change_mid_recording_is_picked_up(tmp_path):
    device = FakeDevice("ABC123", width=1080, height=2400, rotation=0)
    fake = FakeAdbClient([device])
    fake.script_fixture("ABC123", FIXTURES / "tap_event.txt")
    recorder = AndroidRecorder(android_target(), adb=fake)
    recorder.connect()
    from argus_test_creator.recording.adapter import EventSink

    sink = EventSink()
    recorder.start_recording(sink)
    first = sink.pop(timeout=5)
    assert first is not None and first.position is not None
    assert (first.position.x, first.position.y) == (135, 600)
    device.rotation = 1  # user turned the phone
    time.sleep(2.6)  # > ROTATION_POLL_S
    fake.script_fixture("ABC123", FIXTURES / "tap_event.txt")
    second = sink.pop(timeout=5)
    recorder.stop_recording()
    assert second is not None and second.position is not None
    assert recorder.screen_size() == (2400, 1080)
    assert (second.position.x, second.position.y) == (600, 1079 - 135)
