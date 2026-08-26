"""Typed models shared by every stress component.

Actions and mutations are immutable *descriptions*; execution lives in the
registered action/mutation types. Trace events are the source of truth for
reproduction; failures are structured so reports, minimization and future AI
consumers all read the same thing.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# -- targets & entities -------------------------------------------------------------------


class TargetKind(StrEnum):
    TEXT = "text"  # OCR-derived word/phrase on screen
    CONFIGURED = "configured"  # named region from the scenario
    COORDINATE = "coordinate"  # random fallback
    ENTITY = "entity"  # a visible backend entity's on-screen label


class Target(BaseModel):
    """Where an action points on screen."""

    model_config = ConfigDict(frozen=True)

    x: int
    y: int
    kind: TargetKind = TargetKind.COORDINATE
    label: str | None = None
    width: int | None = None
    height: int | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def describe(self) -> str:
        return self.label or f"({self.x}, {self.y})"


class EntityRef(BaseModel):
    """A backend entity believed relevant to the current screen."""

    model_config = ConfigDict(frozen=True)

    entity_type: str
    entity_id: str
    label: str | None = None
    #: Where the belief comes from: ocr, state, configured, backend, ...
    source: str = "unknown"
    confidence: float = 1.0
    data: dict[str, Any] = Field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.entity_type}/{self.entity_id}" + (f" ({self.label})" if self.label else "")


# -- actions ------------------------------------------------------------------------------


class StressAction(BaseModel):
    """An immutable UI action description (what, where, with which parameters)."""

    model_config = ConfigDict(frozen=True)

    action_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    target: Target | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def describe(self) -> str:
        parts = [self.action_type.upper()]
        if self.target is not None:
            parts.append(self.target.describe())
        elif self.parameters:
            parts.append(", ".join(f"{k}={_short(v)}" for k, v in self.parameters.items()))
        return " ".join(parts)


class ActionOutcome(BaseModel):
    """What happened when an action executed."""

    passed: bool = True
    message: str = ""
    duration: float = 0.0
    #: ``application`` | ``infrastructure`` | ``unsupported`` — never conflated.
    error_kind: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# -- mutations ----------------------------------------------------------------------------


class MutationTiming(StrEnum):
    BEFORE_ACTION = "before_action"
    AFTER_ACTION = "after_action"
    DURING_WAIT = "during_wait"
    DELAYED = "delayed"
    ON_CONTEXT = "on_context"
    SCHEDULED = "scheduled"


class Mutation(BaseModel):
    """An immutable backend mutation description."""

    model_config = ConfigDict(frozen=True)

    mutation_type: str  # create | update | delete | duplicate | disable | archive | ...
    entity_type: str
    entity_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    #: Data-mutation strategies applied to produce ``parameters`` (for the trace).
    strategies: tuple[str, ...] = ()
    timing: MutationTiming = MutationTiming.AFTER_ACTION
    delay: float = 0.0
    #: True when the mutation was chosen because the entity is on screen.
    contextual: bool = False
    destructive: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def describe(self) -> str:
        head = f"{self.mutation_type.upper()} {self.entity_type}"
        if self.entity_id is not None:
            head += f"/{self.entity_id}"
        if self.strategies:
            head += f" [{', '.join(self.strategies)}]"
        return head


class MutationOutcome(BaseModel):
    applied: bool
    blocked: bool = False
    reason: str = ""
    duration: float = 0.0
    error_kind: str | None = None  # backend | unsafe | unsupported | infrastructure
    #: Entity id resolved by a create/duplicate.
    entity_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# -- faults --------------------------------------------------------------------------------


class Fault(BaseModel):
    model_config = ConfigDict(frozen=True)

    fault_type: str  # latency | timeout | disconnect | http_error | malformed | empty | duplicate
    parameters: dict[str, Any] = Field(default_factory=dict)
    duration: float | None = None

    def describe(self) -> str:
        extra = ", ".join(f"{k}={_short(v)}" for k, v in self.parameters.items())
        return f"FAULT {self.fault_type}" + (f" ({extra})" if extra else "")


# -- failures ------------------------------------------------------------------------------


class FailureSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "error": 2, "critical": 3}[self.value]


class FailureCategory(StrEnum):
    APPLICATION = "application"  # the app misbehaved
    CRASH = "crash"
    HANG = "hang"
    VISUAL = "visual"
    STALE_STATE = "stale_state"
    RACE_CONDITION = "race_condition"
    BACKEND_CONSISTENCY = "backend_consistency"
    UNEXPECTED_SUCCESS = "unexpected_success"
    INFRASTRUCTURE = "infrastructure"  # Argus / device / harness problem
    UNSUPPORTED = "unsupported"
    UNSAFE = "unsafe"
    BACKEND = "backend"
    DEVICE = "device"
    CONFIGURATION = "configuration"

    @property
    def is_application(self) -> bool:
        return self not in (
            FailureCategory.INFRASTRUCTURE, FailureCategory.UNSUPPORTED,
            FailureCategory.UNSAFE, FailureCategory.BACKEND, FailureCategory.DEVICE,
            FailureCategory.CONFIGURATION,
        )


class Failure(BaseModel):
    failure_id: str
    category: FailureCategory
    severity: FailureSeverity
    message: str
    detector: str
    step: int
    timestamp: str
    #: Seconds since the run started (monotonic).
    elapsed: float = 0.0
    action: StressAction | None = None
    mutation: Mutation | None = None
    confidence: float = 1.0
    evidence: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    #: Trace sequence numbers of the recent history leading here.
    recent_sequence: tuple[int, ...] = ()

    @property
    def signature(self) -> str:
        """Category + detector: the identity used by replay/minimize predicates."""
        return f"{self.category.value}:{self.detector}"


# -- trace ---------------------------------------------------------------------------------


class TraceEventType(StrEnum):
    RUN_STARTED = "run_started"
    ACTION = "action"
    MUTATION = "backend_mutation"
    FAULT = "fault"
    FAULT_CLEARED = "fault_cleared"
    OBSERVATION = "observation"
    FAILURE = "failure"
    WAIT = "wait"
    NOTE = "note"
    RUN_FINISHED = "run_finished"


class TraceEvent(BaseModel):
    """One append-only trace record (source of truth for reproduction)."""

    model_config = ConfigDict(frozen=True)

    sequence: int
    #: Monotonic seconds since run start (deterministic under FakeClock).
    elapsed: float
    timestamp: str
    event_type: TraceEventType
    action: StressAction | None = None
    action_outcome: ActionOutcome | None = None
    mutation: Mutation | None = None
    mutation_outcome: MutationOutcome | None = None
    fault: Fault | None = None
    failure_id: str | None = None
    #: Planned delay before the next action (seconds) — replays honour it.
    delay: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def describe(self) -> str:
        match self.event_type:
            case TraceEventType.ACTION:
                assert self.action is not None
                return self.action.describe()
            case TraceEventType.MUTATION:
                assert self.mutation is not None
                text = f"MUTATION   {self.mutation.describe()}"
                if self.mutation_outcome is not None and self.mutation_outcome.blocked:
                    text += f"  [BLOCKED: {self.mutation_outcome.reason}]"
                return text
            case TraceEventType.FAULT:
                assert self.fault is not None
                return self.fault.describe()
            case TraceEventType.FAILURE:
                return f"FAILURE    {self.failure_id}"
            case _:
                return self.event_type.value.upper()


# -- run records ----------------------------------------------------------------------------


class StressSummary(BaseModel):
    actions: int = 0
    mutations: int = 0
    mutations_blocked: int = 0
    faults: int = 0
    observations: int = 0
    failures_by_severity: dict[str, int] = Field(default_factory=dict)
    failures_by_category: dict[str, int] = Field(default_factory=dict)
    reproducible_failures: int = 0
    duration: float = 0.0
    stop_reason: str = ""
    dropped_history: int = 0


class StressRunRecord(BaseModel):
    """Everything needed to reproduce a run — persisted as ``run.json``."""

    run_id: str
    seed: int
    argus_version: str
    scenario_name: str
    scenario: dict[str, Any]
    device: str | None = None
    device_type: str | None = None
    device_capabilities: dict[str, bool] = Field(default_factory=dict)
    backend_id: str | None = None
    started_at: str
    finished_at: str | None = None
    status: str = "running"  # running | completed | cancelled | errored
    dry_run: bool = False
    replay_of: str | None = None
    minimized_from: str | None = None
    trace_path: str | None = None
    summary: StressSummary = Field(default_factory=StressSummary)
    failures: list[Failure] = Field(default_factory=list)
    artifacts_dir: str | None = None
    infrastructure_errors: list[str] = Field(default_factory=list)

    @property
    def replay_command(self) -> str:
        return f"argus stress replay {self.run_id}"

    @property
    def seed_command(self) -> str:
        return f"argus stress --seed {self.seed} --scenario {self.scenario_name}"


def _short(value: Any, limit: int = 40) -> str:
    text = repr(value) if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "ActionOutcome", "EntityRef", "Failure", "FailureCategory", "FailureSeverity", "Fault",
    "Mutation", "MutationOutcome", "MutationTiming", "StressAction", "StressRunRecord",
    "StressSummary", "Target", "TargetKind", "TraceEvent", "TraceEventType",
]
