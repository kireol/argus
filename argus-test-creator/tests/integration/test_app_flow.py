from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from argus_test_creator.app import CreatorApp
from argus_test_creator.app.context import TargetConnected, TargetDisconnected
from argus_test_creator.app.demo_flow import run_demo_flow
from argus_test_creator.core.errors import CreatorError, UnsupportedCapabilityError
from argus_test_creator.models import Rect
from argus_test_creator.observation import FakeOCRProvider
from argus_test_creator.recording.session import AssertionSuggested

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path):
    app = CreatorApp(ocr=FakeOCRProvider())
    app.config.recording.settle_ms = 0
    yield app
    app.shutdown()


def test_app_record_verify_save_reopen(app, tmp_path):
    project = app.create_project(tmp_path / "p")
    connected: list = []
    app.events.subscribe(TargetConnected, connected.append)
    suggestions: list = []
    app.events.subscribe(AssertionSuggested, suggestions.append)
    app.select_target("fake-movies")
    app.new_test(test_id="T-1", name="Search shows Batman", feature="Movies")
    recorder = app.connect_target()
    assert connected
    app.start_recording()
    recorder.send_tap(200, 250)
    recorder.send_tap(200, 180)
    recorder.send_tap(300, 190)
    recorder.send_text("Bat")
    recorder.send_key("ENTER")
    steps = app.stop_recording()
    for job in app.workers.active_jobs():
        job.result(30)
    assert len(steps) == 7
    assert suggestions
    candidate = next(c for e in suggestions for c in e.candidates
                     if c.condition.params.get("text") == "Batman Begins")
    accepted = app.accept_suggestion(candidate)
    assert accepted.action == "wait_until" and accepted.provenance.source == "suggestion"
    capture = app.capture_screen().result(10)
    ocr = app.run_ocr(capture).result(10)
    assert ocr is not None and "Batman Begins" in ocr.lines()
    img_step = app.add_image_verification(capture, Rect(x=120, y=160, width=700, height=72),
                                          label="Batman row")
    assert img_step.condition.params["image"].startswith("batman_row_")
    assert (project.assets_dir / img_step.condition.params["image"]).is_file()
    txt = app.add_text_verification("The Dark Knight", negated=True, capture=capture)
    assert txt.action == "verify" and txt.condition.type == "text_not_present"
    assert app.validate() == []
    report = app.quality()
    assert report.score >= 80
    path = app.save_test()
    assert path.is_file() and not app.authoring.dirty
    config = yaml.safe_load(project.config_path.read_text())
    assert config["devices"]["demo"]["screenshot_dir"] == "assets/frames"
    assert (project.root / "assets" / "frames" / "frame_001.png").is_file()
    # Reopen in a fresh app: identical YAML
    yaml_text = path.read_text()
    app2 = CreatorApp(ocr=FakeOCRProvider())
    try:
        app2.open_project(project.root)
        doc = app2.open_test("T-1")
        from argus_test_creator.serialization import document_to_yaml

        assert document_to_yaml(doc) == yaml_text
        assert doc.steps[0].provenance.source == "recording"
    finally:
        app2.shutdown()


def test_app_capability_and_validation_guards(app, tmp_path):
    app.create_project(tmp_path / "p")
    target = app.select_target("fake-movies")
    assert target.settings["scenario"] == "movies"
    with pytest.raises(CreatorError):
        app.run_with_argus()  # no id/name/feature/steps → validation errors
    app.recorder._capabilities = app.recorder.capabilities.model_copy(  # type: ignore[attr-defined]
        update={"supports_ocr": False})
    with pytest.raises(UnsupportedCapabilityError):
        app.add_text_verification("x")
    disconnected: list = []
    app.events.subscribe(TargetDisconnected, disconnected.append)
    app.connect_target()
    app.disconnect_target(reason="test")
    assert disconnected and disconnected[0].reason == "test"


def test_import_yaml_and_export(app, tmp_path):
    app.create_project(tmp_path / "p")
    source = tmp_path / "ext.yaml"
    source.write_text("id: EXT-1\nname: External test\nfeature: F\nsteps:\n"
                      "  - action: verify\n    condition: {type: image_present, image: a.png}\n"
                      "  - action: my.plugin\n    foo: 1\n")
    (app.project.assets_dir).mkdir(parents=True, exist_ok=True)
    (app.project.assets_dir / "a.png").write_bytes(b"x")
    doc = app.import_yaml(source)
    assert doc.steps[1].custom
    out = app.export_yaml(tmp_path / "out" / "EXT-1.yaml")
    assert out.is_file() and (tmp_path / "out" / "assets" / "images" / "a.png").is_file()
    assert "my.plugin" in out.read_text()


def test_demo_flow_without_argus(tmp_path):
    summary = run_demo_flow(tmp_path / "demo", run_with_argus=False, echo=lambda s: None)
    assert "Project ready" in summary
    assert (tmp_path / "demo" / "tests" / "DEMO-001.yaml").is_file()


def test_recoverable_sessions_listed(app, tmp_path):
    app.create_project(tmp_path / "p")
    app.select_target("fake-movies")
    app.new_test(test_id="T", name="n", feature="f")
    recorder = app.connect_target()
    session = app.start_recording()
    recorder.send_tap(200, 250)
    session.flush()
    session._journal.close()  # crash
    app.session = None
    dirs = app.recoverable_sessions()
    assert dirs == [session.directory]
    recovered = app.recover_session(dirs[0])
    steps = app.append_actions(recovered.actions)
    assert [s.action for s in steps] == ["device.tap"]
    assert app.append_actions(recovered.actions) == []  # idempotent
    assert Path(recovered.directory).name == session.directory.name
