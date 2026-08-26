from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from argus_test_creator.app.config import load_config
from argus_test_creator.core.errors import ProjectError
from argus_test_creator.models import ConditionDraft, StepDraft
from argus_test_creator.models.authoring import AuthoringDocument, Provenance, TestMetadata
from argus_test_creator.project import CreatorProject
from argus_test_creator.recording.journal import SessionJournal
from argus_test_creator.targets import builtin_targets


def _doc() -> AuthoringDocument:
    doc = AuthoringDocument(metadata=TestMetadata(id="T-1", name="Name", feature="F"))
    doc.steps.append(StepDraft(action="verify", provenance=Provenance(source="recording",
                                                                      event_ids=("e1",)),
                               condition=ConditionDraft(type="text_present",
                                                        params={"text": "x"})))
    doc.target = builtin_targets()[0]
    return doc


def test_project_create_open_save_load(tmp_path):
    project = CreatorProject.create(tmp_path / "p", name="demo")
    assert project.config_path.is_file() and project.info().name == "demo"
    with pytest.raises(ProjectError):
        CreatorProject.create(tmp_path / "p")
    with pytest.raises(ProjectError):
        CreatorProject.open(tmp_path / "nope")
    path = project.save_document(_doc())
    assert path == project.tests_dir / "T-1.yaml" and project.list_test_ids() == ["T-1"]
    reopened = CreatorProject.open(tmp_path / "p")
    doc = reopened.load_document("T-1")
    assert doc.steps[0].provenance.event_ids == ("e1",)  # provenance survives
    assert doc.target is not None and doc.target.id == "fake-movies"
    config = yaml.safe_load(project.config_path.read_text())
    assert config["devices"]["demo"]["type"] == "fake"
    assert config["test_paths"] == ["tests"] and config["asset_paths"] == ["assets/images"]


def test_external_yaml_edit_wins_over_stale_document(tmp_path):
    project = CreatorProject.create(tmp_path / "p")
    project.save_document(_doc())
    yaml_path = project.test_path("T-1")
    text = yaml_path.read_text().replace("name: Name", "name: Edited outside")
    yaml_path.write_text(text)
    future = time.time() + 10
    os.utime(yaml_path, (future, future))
    doc = project.load_document("T-1")
    assert doc.metadata.name == "Edited outside" and doc.target is not None


def test_project_requires_id_and_preserves_devices(tmp_path):
    project = CreatorProject.create(tmp_path / "p")
    doc = _doc()
    doc.metadata.id = ""
    with pytest.raises(ProjectError):
        project.save_document(doc)
    project.config_path.write_text("devices:\n  mine:\n    type: android\n    serial: X\n")
    project.write_argus_config(builtin_targets()[1])
    config = yaml.safe_load(project.config_path.read_text())
    assert set(config["devices"]) == {"mine", "web"}


def test_cleanup_sessions(tmp_path):
    project = CreatorProject.create(tmp_path / "p")
    old = project.sessions_dir / "old"
    old.mkdir()
    (old / "session.json").write_text("{}")
    stamp = time.time() - 30 * 86400
    os.utime(old, (stamp, stamp))
    new = project.sessions_dir / "new"
    new.mkdir()
    assert project.cleanup_sessions() == 1 and new.exists() and not old.exists()


def test_journal_append_recover_torn_line(tmp_path):
    from argus_test_creator.models import RecordingEvent, RecordingEventType

    journal = SessionJournal(tmp_path / "s")
    journal.open()
    for i in range(30):
        journal.append_event(RecordingEvent(event_type=RecordingEventType.CLICK, sequence=i))
    journal.write_snapshot({"id": "s", "state": "recording"})
    journal.close()
    with (tmp_path / "s" / "events.jsonl").open("a") as fh:
        fh.write('{"event_type": "cli')  # torn write
    events = journal.read_events()
    assert len(events) == 30 and events[-1].sequence == 29
    assert SessionJournal.recoverable(tmp_path) == [tmp_path / "s"]
    journal.write_snapshot({"id": "s", "state": "stopped"})
    assert SessionJournal.recoverable(tmp_path) == []


def test_config_hierarchy(tmp_path):
    user = tmp_path / "user.yaml"
    user.write_text("ocr:\n  provider: fake\nrecording:\n  settle_ms: 10\n")
    project = tmp_path / "proj"
    (project / ".argus-creator").mkdir(parents=True)
    (project / ".argus-creator" / "config.yaml").write_text("recording:\n  settle_ms: 20\n")
    env = {"ARGUS_CREATOR_RECORDING__MODE": "exact", "ARGUS_EXECUTABLE": "/x/argus",
           "ARGUS_CREATOR_DIAGNOSTIC": "true"}
    config = load_config(project_root=project, user_path=user, env=env,
                         overrides={"workers": 2})
    assert config.ocr.provider == "fake" and config.recording.settle_ms == 20
    assert config.recording.mode == "exact" and config.argus.executable == "/x/argus"
    assert config.diagnostic is True and config.workers == 2
    assert config.sources[0] == "defaults" and "environment" in config.sources


def test_config_rejects_unknown_keys(tmp_path):
    user = tmp_path / "user.yaml"
    user.write_text("bogus: 1\n")
    with pytest.raises(ValidationError):
        load_config(user_path=user, env={})


def test_document_json_is_stable(tmp_path):
    project = CreatorProject.create(tmp_path / "p")
    project.save_document(_doc())
    data = json.loads(project.document_path("T-1").read_text())
    assert data["schema_version"] == 1 and data["metadata"]["id"] == "T-1"
    assert Path(data["source_path"]).name == "T-1.yaml"
