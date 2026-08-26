"""Reversible document commands (see core.commands)."""

from __future__ import annotations

from typing import Any

from argus_test_creator.core.commands import Command
from argus_test_creator.core.ids import new_id
from argus_test_creator.models.authoring import (
    AssetReference,
    AuthoringDocument,
    ConditionDraft,
    StepDraft,
    TestMetadata,
)


class DocumentCommand(Command[AuthoringDocument]):
    """Base class; subclasses touch ``updated_at``."""

    def apply(self, target: AuthoringDocument) -> None:
        self.do(target)
        target.touch()

    def revert(self, target: AuthoringDocument) -> None:
        self.undo(target)
        target.touch()

    def do(self, doc: AuthoringDocument) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def undo(self, doc: AuthoringDocument) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class AddStepCommand(DocumentCommand):
    def __init__(self, step: StepDraft, index: int | None = None) -> None:
        self.step = step
        self.index = index
        self.label = f"Add step {step.display_name()!r}"
        self._inserted_at: int | None = None

    def do(self, doc: AuthoringDocument) -> None:
        index = len(doc.steps) if self.index is None else max(0, min(self.index, len(doc.steps)))
        doc.steps.insert(index, self.step)
        self._inserted_at = index

    def undo(self, doc: AuthoringDocument) -> None:
        assert self._inserted_at is not None
        del doc.steps[self._inserted_at]


class AddStepsCommand(DocumentCommand):
    """Insert several steps at once (e.g. a whole recording) as one undo entry."""

    def __init__(self, steps: list[StepDraft], index: int | None = None) -> None:
        self.steps = steps
        self.index = index
        self.label = f"Add {len(steps)} steps"
        self._inserted_at = 0

    def do(self, doc: AuthoringDocument) -> None:
        index = len(doc.steps) if self.index is None else max(0, min(self.index, len(doc.steps)))
        doc.steps[index:index] = self.steps
        self._inserted_at = index

    def undo(self, doc: AuthoringDocument) -> None:
        del doc.steps[self._inserted_at : self._inserted_at + len(self.steps)]


class DeleteStepCommand(DocumentCommand):
    def __init__(self, step_id: str) -> None:
        self.step_id = step_id
        self.label = "Delete step"
        self._removed: tuple[int, StepDraft] | None = None

    def do(self, doc: AuthoringDocument) -> None:
        index = doc.step_index(self.step_id)
        self._removed = (index, doc.steps.pop(index))
        self.label = f"Delete step {self._removed[1].display_name()!r}"

    def undo(self, doc: AuthoringDocument) -> None:
        assert self._removed is not None
        index, step = self._removed
        doc.steps.insert(index, step)


class MoveStepCommand(DocumentCommand):
    def __init__(self, step_id: str, new_index: int) -> None:
        self.step_id = step_id
        self.new_index = new_index
        self.label = "Move step"
        self._old_index = 0

    def do(self, doc: AuthoringDocument) -> None:
        self._old_index = doc.step_index(self.step_id)
        step = doc.steps.pop(self._old_index)
        target = max(0, min(self.new_index, len(doc.steps)))
        doc.steps.insert(target, step)
        self.new_index = target

    def undo(self, doc: AuthoringDocument) -> None:
        step = doc.steps.pop(self.new_index)
        doc.steps.insert(self._old_index, step)


class EditStepCommand(DocumentCommand):
    """Replace mutable fields of a step (action, name, params, condition, notes, enabled)."""

    def __init__(self, step_id: str, **changes: Any) -> None:
        self.step_id = step_id
        self.changes = changes
        self.label = "Edit step"
        self._before: dict[str, Any] = {}

    def do(self, doc: AuthoringDocument) -> None:
        step = doc.find_step(self.step_id)
        self._before = {key: _clone(getattr(step, key)) for key in self.changes}
        for key, value in self.changes.items():
            setattr(step, key, _clone(value))
        self.label = f"Edit step {step.display_name()!r}"

    def undo(self, doc: AuthoringDocument) -> None:
        step = doc.find_step(self.step_id)
        for key, value in self._before.items():
            setattr(step, key, _clone(value))


class DuplicateStepCommand(DocumentCommand):
    def __init__(self, step_id: str) -> None:
        self.step_id = step_id
        self.label = "Duplicate step"
        self._copy: StepDraft | None = None
        self._index = 0

    def do(self, doc: AuthoringDocument) -> None:
        index = doc.step_index(self.step_id)
        original = doc.steps[index]
        if self._copy is None:
            self._copy = original.model_copy(deep=True, update={"id": new_id("step")})
        self._index = index + 1
        doc.steps.insert(self._index, self._copy)

    def undo(self, doc: AuthoringDocument) -> None:
        del doc.steps[self._index]


class SetConditionCommand(DocumentCommand):
    def __init__(self, step_id: str, condition: ConditionDraft | None) -> None:
        self.step_id = step_id
        self.condition = condition
        self.label = "Change condition"
        self._before: ConditionDraft | None = None

    def do(self, doc: AuthoringDocument) -> None:
        step = doc.find_step(self.step_id)
        self._before = step.condition
        step.condition = self.condition

    def undo(self, doc: AuthoringDocument) -> None:
        doc.find_step(self.step_id).condition = self._before


class SetMetadataCommand(DocumentCommand):
    def __init__(self, **changes: Any) -> None:
        self.changes = changes
        self.label = "Edit test details"
        self._before: dict[str, Any] = {}

    def do(self, doc: AuthoringDocument) -> None:
        self._before = {key: _clone(getattr(doc.metadata, key)) for key in self.changes}
        update = {k: _clone(v) for k, v in self.changes.items()}
        doc.metadata = doc.metadata.model_copy(update=update)

    def undo(self, doc: AuthoringDocument) -> None:
        doc.metadata = doc.metadata.model_copy(update=self._before)

    def merge_with(self, previous: Command[AuthoringDocument]) -> bool:
        # Consecutive edits of the same metadata field collapse into one undo entry.
        if isinstance(previous, SetMetadataCommand) and set(previous.changes) == set(self.changes):
            previous.changes = self.changes
            return True
        return False


class ReplaceMetadataCommand(DocumentCommand):
    def __init__(self, metadata: TestMetadata) -> None:
        self.metadata = metadata
        self.label = "Edit test details"
        self._before: TestMetadata | None = None

    def do(self, doc: AuthoringDocument) -> None:
        self._before = doc.metadata
        doc.metadata = self.metadata

    def undo(self, doc: AuthoringDocument) -> None:
        assert self._before is not None
        doc.metadata = self._before


class AddAssetCommand(DocumentCommand):
    def __init__(self, asset: AssetReference) -> None:
        self.asset = asset
        self.label = f"Add asset {asset.relative_path}"

    def do(self, doc: AuthoringDocument) -> None:
        if doc.asset_by_path(self.asset.relative_path) is None:
            doc.assets.append(self.asset)

    def undo(self, doc: AuthoringDocument) -> None:
        doc.assets = [a for a in doc.assets if a.id != self.asset.id]


class SetLifecycleStepsCommand(DocumentCommand):
    """Replace ``setup`` or ``teardown`` wholesale (they are small lists)."""

    def __init__(self, section: str, steps: list[StepDraft]) -> None:
        assert section in ("setup", "teardown")
        self.section = section
        self.steps = steps
        self.label = f"Edit {section}"
        self._before: list[StepDraft] = []

    def do(self, doc: AuthoringDocument) -> None:
        self._before = list(getattr(doc, self.section))
        setattr(doc, self.section, list(self.steps))

    def undo(self, doc: AuthoringDocument) -> None:
        setattr(doc, self.section, self._before)


def _clone(value: Any) -> Any:
    if hasattr(value, "model_copy"):
        return value.model_copy(deep=True)
    if isinstance(value, dict):
        return {k: _clone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clone(v) for v in value]
    return value
