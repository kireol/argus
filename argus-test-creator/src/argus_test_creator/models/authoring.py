"""The authoring model — the Creator's internal contract.

An :class:`AuthoringDocument` is what the user edits. It is independent of
the Argus YAML format: the serializer turns it into YAML, the importer turns
YAML back into it, and unknown YAML content survives both directions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from argus_test_creator.core.ids import new_id
from argus_test_creator.models.capabilities import TargetProfile
from argus_test_creator.models.common import parse_duration

SCHEMA_VERSION = 1

# Actions that carry a condition (the assertion/synchronization steps).
CONDITION_ACTIONS = frozenset({"verify", "wait_until"})


class Provenance(BaseModel):
    """Where a step came from — invaluable when debugging the recorder."""

    model_config = ConfigDict(frozen=True)

    source: str = "manual"  # manual | recording | import | suggestion
    session_id: str | None = None
    event_ids: tuple[str, ...] = ()
    action_id: str | None = None
    capture_id: str | None = None
    note: str | None = None

    def describe(self) -> str:
        if self.source == "recording":
            ids = ", ".join(self.event_ids) if self.event_ids else self.action_id or "?"
            return f"generated from recording event(s) {ids}"
        if self.source == "import":
            return "imported from YAML" + (f" ({self.note})" if self.note else "")
        if self.source == "suggestion":
            return "accepted assertion suggestion" + (f" ({self.note})" if self.note else "")
        return "added manually" + (f" ({self.note})" if self.note else "")


class ConditionDraft(BaseModel):
    """A leaf (``type`` + params) or composite (``all``/``any``/``not``) condition.

    Mirrors Argus's ConditionSpec one-to-one so serialization is lossless.
    """

    type: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    all: list[ConditionDraft] | None = None
    any: list[ConditionDraft] | None = None
    not_: ConditionDraft | None = None

    @property
    def is_composite(self) -> bool:
        return self.type is None

    @property
    def form(self) -> str:
        if self.type is not None:
            return "type"
        if self.all is not None:
            return "all"
        if self.any is not None:
            return "any"
        if self.not_ is not None:
            return "not"
        return "empty"

    def leaves(self) -> list[ConditionDraft]:
        if self.type is not None:
            return [self]
        out: list[ConditionDraft] = []
        for child in (self.all or []) + (self.any or []) + ([self.not_] if self.not_ else []):
            out.extend(child.leaves())
        return out

    def to_argus(self) -> dict[str, Any]:
        if self.type is not None:
            return {"type": self.type, **self.params}
        if self.all is not None:
            return {"all": [c.to_argus() for c in self.all]}
        if self.any is not None:
            return {"any": [c.to_argus() for c in self.any]}
        if self.not_ is not None:
            return {"not": self.not_.to_argus()}
        return {}

    @classmethod
    def from_argus(cls, raw: Any) -> ConditionDraft:
        if not isinstance(raw, dict):
            raise ValueError(f"Condition must be a mapping, got {type(raw).__name__}")
        data = dict(raw)
        if "type" in data:
            ctype = data.pop("type")
            return cls(type=str(ctype), params=data)
        if "all" in data:
            return cls(all=[cls.from_argus(c) for c in data["all"] or []])
        if "any" in data:
            return cls(any=[cls.from_argus(c) for c in data["any"] or []])
        if "not" in data:
            return cls(not_=cls.from_argus(data["not"]))
        return cls(type=None, params=data)

    def describe(self) -> str:
        if self.type is not None:
            p = self.params
            match self.type:
                case "text_present":
                    return f'Text "{p.get("text", "")}" is visible'
                case "text_not_present":
                    return f'Text "{p.get("text", "")}" is not visible'
                case "image_present":
                    return f"Image {p.get('image', '?')} is visible"
                case "image_not_present":
                    return f"Image {p.get('image', '?')} is not visible"
                case "screenshot_matches":
                    return f"Screen matches {p.get('image', '?')}"
                case "pixel_matches":
                    return f"Pixel ({p.get('x')}, {p.get('y')}) is {p.get('color')}"
                case "log_contains":
                    return f"Log contains {p.get('text') or p.get('pattern')!r}"
                case "now_playing":
                    return f"Media is {p.get('state', 'playing')}"
                case _:
                    return f"{self.type} {p}" if p else self.type
        if self.all is not None:
            return " AND ".join(c.describe() for c in self.all)
        if self.any is not None:
            return " OR ".join(c.describe() for c in self.any)
        if self.not_ is not None:
            return f"NOT ({self.not_.describe()})"
        return "(empty condition)"


class StepKind(StrEnum):
    ACTION = "action"
    VERIFY = "verify"
    WAIT_UNTIL = "wait_until"
    CUSTOM = "custom"  # unknown/unsupported action preserved verbatim


class StepDraft(BaseModel):
    """One editable test step."""

    id: str = Field(default_factory=lambda: new_id("step"))
    action: str
    name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    condition: ConditionDraft | None = None
    enabled: bool = True
    notes: str = ""
    provenance: Provenance = Field(default_factory=Provenance)
    #: Set by the importer when the action is unknown to the catalog.
    custom: bool = False

    @property
    def kind(self) -> StepKind:
        if self.custom:
            return StepKind.CUSTOM
        if self.action == "verify":
            return StepKind.VERIFY
        if self.action == "wait_until":
            return StepKind.WAIT_UNTIL
        return StepKind.ACTION

    @property
    def is_assertion(self) -> bool:
        return self.action in CONDITION_ACTIONS

    def display_name(self) -> str:
        if self.name:
            return self.name
        return self.default_name()

    def default_name(self) -> str:
        p = self.params
        match self.action:
            case "device.tap":
                return f"Tap ({p.get('x')}, {p.get('y')})"
            case "device.long_press":
                return f"Long press ({p.get('x')}, {p.get('y')})"
            case "device.swipe":
                return (
                    f"Swipe ({p.get('from_x')}, {p.get('from_y')}) → "
                    f"({p.get('to_x')}, {p.get('to_y')})"
                )
            case "device.drag":
                return (
                    f"Drag ({p.get('from_x')}, {p.get('from_y')}) → "
                    f"({p.get('to_x')}, {p.get('to_y')})"
                )
            case "device.key":
                return f"Press {p.get('key')}"
            case "device.start":
                return "Start application"
            case "device.stop":
                return "Stop application"
            case "device.restart":
                return "Restart application"
            case "device.reset":
                return "Reset application"
            case "wait":
                return f"Wait {p.get('duration')}"
            case "wait_until":
                return "Wait until " + (self.condition.describe() if self.condition else "…")
            case "verify":
                return "Verify " + (self.condition.describe() if self.condition else "…")
            case "screenshot":
                return f"Screenshot {p.get('file', '')}".rstrip()
            case "log":
                return f"Log {p.get('message', '')!r}"
            case "backend.set":
                return f"Set backend state {p.get('data')}"
            case "shell.run":
                return f"Run {p.get('command')}"
        return self.action

    def to_argus(self) -> dict[str, Any]:
        """Argus step mapping (``action`` first, ``name`` second, then params)."""
        step: dict[str, Any] = {"action": self.action}
        if self.name:
            step["name"] = self.name
        if self.condition is not None:
            step["condition"] = self.condition.to_argus()
        for key, value in self.params.items():
            if key == "condition" and self.condition is not None:
                continue
            step[key] = value
        return step


class TestMetadata(BaseModel):
    """Everything in an Argus test that is not a step. Mirrors TestDefinition."""

    id: str = ""
    name: str = ""
    description: str = ""
    feature: str = ""
    tags: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    priority: str | None = None
    timeout: str | None = None
    requires: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    retry_only: list[str] = Field(default_factory=list)

    @field_validator("tags", "platforms", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    def timeout_seconds(self) -> float | None:
        if not self.timeout:
            return None
        return parse_duration(self.timeout)


class AssetReference(BaseModel):
    """An image asset the test references (relative to the Argus asset path)."""

    id: str = Field(default_factory=lambda: new_id("asset"))
    #: Path relative to the project's asset directory, e.g. ``batman_title.png``.
    relative_path: str
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    #: Where the crop came from.
    source_capture_id: str | None = None
    source_region: dict[str, int] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuthoringWarning(BaseModel):
    code: str
    message: str
    step_id: str | None = None


class ValidationIssue(BaseModel):
    """An actionable problem: what is wrong, where, and how to fix it."""

    severity: str = "error"  # error | warning | info
    code: str
    message: str
    step_id: str | None = None
    field: str | None = None
    fix: str | None = None
    source: str = "creator"  # creator | argus

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


class AuthoringDocument(BaseModel):
    """The editable test. Everything the UI shows comes from here."""

    schema_version: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: new_id("doc"))
    metadata: TestMetadata = Field(default_factory=TestMetadata)
    steps: list[StepDraft] = Field(default_factory=list)
    setup: list[StepDraft] = Field(default_factory=list)
    teardown: list[StepDraft] = Field(default_factory=list)
    assets: list[AssetReference] = Field(default_factory=list)
    target: TargetProfile | None = None
    warnings: list[AuthoringWarning] = Field(default_factory=list)
    annotations: dict[str, str] = Field(default_factory=dict)
    #: Recording sessions this document was built from.
    session_ids: list[str] = Field(default_factory=list)
    #: Top-level YAML keys the importer did not understand, preserved verbatim.
    unknown_fields: dict[str, Any] = Field(default_factory=dict)
    #: Path of the YAML file this document was imported from (if any).
    source_path: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def step_index(self, step_id: str) -> int:
        for index, step in enumerate(self.steps):
            if step.id == step_id:
                return index
        raise KeyError(step_id)

    def find_step(self, step_id: str) -> StepDraft:
        return self.steps[self.step_index(step_id)]

    def asset_by_path(self, relative_path: str) -> AssetReference | None:
        for asset in self.assets:
            if asset.relative_path == relative_path:
                return asset
        return None

    def referenced_images(self) -> set[str]:
        images: set[str] = set()
        for step in self.steps + self.setup + self.teardown:
            if step.condition is None:
                continue
            for leaf in step.condition.leaves():
                image = leaf.params.get("image")
                if isinstance(image, str):
                    images.add(image)
        return images

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)
