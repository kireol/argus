"""AuthoringService — the use-case layer the UI and CLI call.

Holds one document plus its command stack and publishes document events.
UI code never mutates the document directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from argus_test_creator.authoring.commands import (
    AddAssetCommand,
    AddStepCommand,
    AddStepsCommand,
    DeleteStepCommand,
    DocumentCommand,
    DuplicateStepCommand,
    EditStepCommand,
    MoveStepCommand,
    SetConditionCommand,
    SetLifecycleStepsCommand,
    SetMetadataCommand,
)
from argus_test_creator.core.commands import CommandStack
from argus_test_creator.core.events import Event, EventBus
from argus_test_creator.models.authoring import (
    AssetReference,
    AuthoringDocument,
    ConditionDraft,
    Provenance,
    StepDraft,
)


@dataclass(frozen=True, kw_only=True)
class DocumentChanged(Event):
    document_id: str
    revision: int
    label: str


@dataclass(frozen=True, kw_only=True)
class StepAdded(DocumentChanged):
    step_id: str


@dataclass(frozen=True, kw_only=True)
class StepRemoved(DocumentChanged):
    step_id: str


@dataclass(frozen=True, kw_only=True)
class StepChanged(DocumentChanged):
    step_id: str


@dataclass(frozen=True, kw_only=True)
class MetadataChanged(DocumentChanged):
    pass


@dataclass(frozen=True, kw_only=True)
class AssertionAdded(StepAdded):
    pass


@dataclass(frozen=True, kw_only=True)
class DocumentReplaced(Event):
    document_id: str


class AuthoringService:
    def __init__(self, events: EventBus, document: AuthoringDocument | None = None) -> None:
        self._events = events
        self._stack = CommandStack(document or AuthoringDocument())

    # -- document ---------------------------------------------------------------

    @property
    def document(self) -> AuthoringDocument:
        return self._stack.target

    @property
    def revision(self) -> int:
        return self._stack.revision

    @property
    def dirty(self) -> bool:
        return self._stack.dirty

    def mark_clean(self) -> None:
        self._stack.mark_clean()

    def replace_document(self, document: AuthoringDocument) -> None:
        self._stack = CommandStack(document)
        self._events.publish(DocumentReplaced(document_id=document.id))

    # -- undo / redo --------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return self._stack.can_undo

    @property
    def can_redo(self) -> bool:
        return self._stack.can_redo

    @property
    def undo_label(self) -> str | None:
        return self._stack.undo_label

    @property
    def redo_label(self) -> str | None:
        return self._stack.redo_label

    def undo(self) -> bool:
        command = self._stack.undo()
        if command is None:
            return False
        self._publish(DocumentChanged, label=f"Undo {command.label}")
        return True

    def redo(self) -> bool:
        command = self._stack.redo()
        if command is None:
            return False
        self._publish(DocumentChanged, label=f"Redo {command.label}")
        return True

    # -- steps ----------------------------------------------------------------------

    def add_step(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        name: str | None = None,
        condition: ConditionDraft | None = None,
        index: int | None = None,
        provenance: Provenance | None = None,
        notes: str = "",
    ) -> StepDraft:
        step = StepDraft(
            action=action,
            params=dict(params or {}),
            name=name,
            condition=condition,
            provenance=provenance or Provenance(),
            notes=notes,
        )
        self._run(AddStepCommand(step, index))
        event_cls = AssertionAdded if step.is_assertion else StepAdded
        self._publish(event_cls, label="Add step", step_id=step.id)
        return step

    def add_steps(self, steps: list[StepDraft], index: int | None = None) -> None:
        if not steps:
            return
        self._run(AddStepsCommand(steps, index))
        for step in steps:
            self._publish(StepAdded, label="Add steps", step_id=step.id)

    def add_verification(
        self,
        condition: ConditionDraft,
        *,
        wait: bool = False,
        timeout: str | None = None,
        index: int | None = None,
        name: str | None = None,
        provenance: Provenance | None = None,
    ) -> StepDraft:
        params: dict[str, Any] = {}
        if wait and timeout:
            params["timeout"] = timeout
        return self.add_step(
            "wait_until" if wait else "verify",
            params,
            condition=condition,
            index=index,
            name=name,
            provenance=provenance,
        )

    def delete_step(self, step_id: str) -> None:
        self._run(DeleteStepCommand(step_id))
        self._publish(StepRemoved, label="Delete step", step_id=step_id)

    def move_step(self, step_id: str, new_index: int) -> None:
        self._run(MoveStepCommand(step_id, new_index))
        self._publish(StepChanged, label="Move step", step_id=step_id)

    def move_step_by(self, step_id: str, delta: int) -> None:
        index = self.document.step_index(step_id)
        target = max(0, min(index + delta, len(self.document.steps) - 1))
        if target != index:
            self.move_step(step_id, target)

    def edit_step(self, step_id: str, **changes: Any) -> None:
        self._run(EditStepCommand(step_id, **changes))
        self._publish(StepChanged, label="Edit step", step_id=step_id)

    def rename_step(self, step_id: str, name: str | None) -> None:
        self.edit_step(step_id, name=name or None)

    def set_step_enabled(self, step_id: str, enabled: bool) -> None:
        self.edit_step(step_id, enabled=enabled)

    def set_step_notes(self, step_id: str, notes: str) -> None:
        self.edit_step(step_id, notes=notes)

    def set_params(self, step_id: str, params: dict[str, Any]) -> None:
        self.edit_step(step_id, params=dict(params))

    def change_action(
        self, step_id: str, action: str, params: dict[str, Any] | None = None
    ) -> None:
        step = self.document.find_step(step_id)
        new_params = dict(params) if params is not None else dict(step.params)
        condition = step.condition if action in ("verify", "wait_until") else None
        self.edit_step(step_id, action=action, params=new_params, condition=condition,
                       custom=False)

    def set_condition(self, step_id: str, condition: ConditionDraft | None) -> None:
        self._run(SetConditionCommand(step_id, condition))
        self._publish(StepChanged, label="Change condition", step_id=step_id)

    def duplicate_step(self, step_id: str) -> StepDraft:
        command = DuplicateStepCommand(step_id)
        self._run(command)
        assert command._copy is not None
        self._publish(StepAdded, label="Duplicate step", step_id=command._copy.id)
        return command._copy

    def convert_to_wait_until(self, step_id: str, timeout: str = "10s") -> None:
        """verify → wait_until (adds synchronization) or the reverse."""
        step = self.document.find_step(step_id)
        if step.action == "verify":
            params = {**step.params, "timeout": timeout}
            self.edit_step(step_id, action="wait_until", params=params)
        elif step.action == "wait_until":
            params = {k: v for k, v in step.params.items() if k not in ("timeout", "poll_interval")}
            self.edit_step(step_id, action="verify", params=params)

    # -- metadata / assets / lifecycle -----------------------------------------------

    def set_metadata(self, **changes: Any) -> None:
        self._run(SetMetadataCommand(**changes))
        self._publish(MetadataChanged, label="Edit test details")

    def add_asset(self, asset: AssetReference) -> None:
        self._run(AddAssetCommand(asset))
        self._publish(DocumentChanged, label="Add asset")

    def set_lifecycle_steps(self, section: str, steps: list[StepDraft]) -> None:
        self._run(SetLifecycleStepsCommand(section, steps))
        self._publish(DocumentChanged, label=f"Edit {section}")

    # -- internals --------------------------------------------------------------------

    def _run(self, command: DocumentCommand) -> None:
        self._stack.execute(command)

    def _publish(self, event_cls: type[DocumentChanged], **fields: Any) -> None:
        self._events.publish(
            event_cls(document_id=self.document.id, revision=self.revision, **fields)
        )
