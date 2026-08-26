from __future__ import annotations

from argus_test_creator.authoring import AuthoringService
from argus_test_creator.authoring.service import (
    DocumentChanged,
    MetadataChanged,
    StepAdded,
    StepRemoved,
)
from argus_test_creator.core.events import EventBus
from argus_test_creator.models import AssetReference, ConditionDraft, StepDraft


def _svc() -> tuple[AuthoringService, list]:
    bus = EventBus()
    seen: list = []
    bus.subscribe(DocumentChanged, seen.append)
    return AuthoringService(bus), seen


def test_add_edit_move_delete_with_undo_redo():
    svc, seen = _svc()
    a = svc.add_step("device.tap", {"x": 1, "y": 2})
    b = svc.add_step("device.key", {"key": "ENTER"})
    c = svc.add_verification(ConditionDraft(type="text_present", params={"text": "Hi"}))
    assert [s.id for s in svc.document.steps] == [a.id, b.id, c.id]
    svc.move_step(c.id, 0)
    assert svc.document.steps[0].id == c.id
    svc.edit_step(a.id, params={"x": 9, "y": 9}, name="Tap thing")
    assert svc.document.find_step(a.id).params == {"x": 9, "y": 9}
    svc.delete_step(b.id)
    assert len(svc.document.steps) == 2
    # undo everything
    assert svc.undo()  # delete
    assert len(svc.document.steps) == 3 and svc.document.steps[2].id == b.id
    assert svc.undo()  # edit
    assert svc.document.find_step(a.id).params == {"x": 1, "y": 2}
    assert svc.document.find_step(a.id).name is None
    assert svc.undo()  # move
    assert svc.document.steps[0].id == a.id
    for _ in range(3):
        assert svc.undo()
    assert svc.document.steps == [] and not svc.can_undo
    for _ in range(6):
        assert svc.redo()
    assert len(svc.document.steps) == 2 and not svc.can_redo
    assert any(isinstance(e, StepAdded) for e in seen)
    assert any(isinstance(e, StepRemoved) for e in seen)


def test_duplicate_disable_notes_rename():
    svc, _ = _svc()
    a = svc.add_step("device.tap", {"x": 1, "y": 2})
    copy = svc.duplicate_step(a.id)
    assert copy.id != a.id and copy.params == a.params
    assert svc.document.steps[1].id == copy.id
    svc.set_step_enabled(a.id, False)
    svc.set_step_notes(a.id, "flaky")
    svc.rename_step(a.id, "First tap")
    step = svc.document.find_step(a.id)
    assert not step.enabled and step.notes == "flaky" and step.name == "First tap"
    svc.undo()
    assert svc.document.find_step(a.id).name is None
    for _ in range(3):
        svc.undo()
    assert len(svc.document.steps) == 1


def test_metadata_edits_merge_and_undo():
    svc, seen = _svc()
    svc.set_metadata(name="A")
    svc.set_metadata(name="AB")
    svc.set_metadata(name="ABC")
    assert svc.document.metadata.name == "ABC"
    assert svc.undo_label is not None
    svc.undo()
    assert svc.document.metadata.name == ""  # consecutive same-field edits merged
    assert any(isinstance(e, MetadataChanged) for e in seen)


def test_convert_verify_wait_until():
    svc, _ = _svc()
    s = svc.add_verification(ConditionDraft(type="text_present", params={"text": "x"}))
    svc.convert_to_wait_until(s.id, timeout="7s")
    step = svc.document.find_step(s.id)
    assert step.action == "wait_until" and step.params["timeout"] == "7s"
    svc.convert_to_wait_until(s.id)
    assert svc.document.find_step(s.id).action == "verify"
    assert "timeout" not in svc.document.find_step(s.id).params


def test_change_action_drops_condition_for_non_assertions():
    svc, _ = _svc()
    s = svc.add_verification(ConditionDraft(type="text_present", params={"text": "x"}))
    svc.change_action(s.id, "device.tap", {"x": 1, "y": 1})
    step = svc.document.find_step(s.id)
    assert step.condition is None and step.action == "device.tap"
    svc.undo()
    assert svc.document.find_step(s.id).condition is not None


def test_add_steps_bulk_is_single_undo():
    svc, _ = _svc()
    svc.add_steps([StepDraft(action="device.tap", params={"x": i, "y": i}) for i in range(5)])
    assert len(svc.document.steps) == 5
    svc.undo()
    assert svc.document.steps == []


def test_assets_and_dirty_state():
    svc, _ = _svc()
    assert not svc.dirty
    svc.add_asset(AssetReference(relative_path="a.png"))
    assert svc.dirty and svc.document.asset_by_path("a.png") is not None
    svc.mark_clean()
    assert not svc.dirty
    svc.undo()
    assert svc.document.assets == []


def test_replace_document_resets_history():
    svc, _ = _svc()
    svc.add_step("log", {"message": "x"})
    from argus_test_creator.models.authoring import AuthoringDocument

    svc.replace_document(AuthoringDocument())
    assert not svc.can_undo and svc.document.steps == []
