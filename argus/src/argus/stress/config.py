"""``stress:`` configuration and scenario files.

A scenario is the ``stress:`` section of an Argus configuration, or a
stand-alone YAML file (``argus stress --scenario checkout-chaos.yaml``) with the
same keys at its root (an optional ``stress:`` wrapper is accepted). Scenario
files may also carry ``backend:``/``devices:`` overrides so an example is
self-contained.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from argus.exceptions import ConfigurationError
from argus.utilities.duration import parse_duration


class ActionWeight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight: float = Field(default=1.0, ge=0)
    enabled: bool = True
    #: Extra parameters forwarded to the action type's generator.
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _bare_number(cls, data: Any) -> Any:
        if isinstance(data, int | float) and not isinstance(data, bool):
            return {"weight": float(data)}
        if isinstance(data, bool):
            return {"enabled": data}
        return data


class DelayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: str | float = 0.05
    max: str | float = 0.75

    @property
    def min_seconds(self) -> float:
        return parse_duration(self.min)

    @property
    def max_seconds(self) -> float:
        return max(parse_duration(self.max), self.min_seconds)


class TargetRegion(BaseModel):
    """A named interaction region the monkey may aim at (scenario-provided)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(default=1, gt=0)
    height: int = Field(default=1, gt=0)
    weight: float = Field(default=1.0, ge=0)
    #: Actions allowed on this region (empty = any).
    actions: list[str] = Field(default_factory=list)


class TargetsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Prefer meaningful targets over random coordinates with this probability.
    prefer_known: float = Field(default=0.85, ge=0, le=1)
    use_ocr: bool = True
    #: Re-run OCR at most every N actions (OCR is expensive).
    ocr_refresh_every: int = Field(default=5, ge=1)
    #: Ignore OCR words shorter than this.
    min_word_length: int = Field(default=2, ge=1)
    #: Keep taps inside this inset from the screen edge (pixels).
    edge_margin: int = Field(default=8, ge=0)
    regions: list[TargetRegion] = Field(default_factory=list)
    #: Words that must never be tapped (e.g. "Delete account", "Sign out").
    avoid_words: list[str] = Field(default_factory=list)


class TypingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    words: list[str] = Field(default_factory=lambda: ["batman", "matrix", "test", "1234", "a"])
    max_length: int = Field(default=24, ge=1)
    allow_unicode: bool = True
    allow_special: bool = True


class MonkeyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    actions: dict[str, ActionWeight] = Field(default_factory=lambda: {
        "tap": ActionWeight(weight=40), "swipe": ActionWeight(weight=15),
        "scroll": ActionWeight(weight=15), "back": ActionWeight(weight=10),
        "type_text": ActionWeight(weight=10), "long_press": ActionWeight(weight=3),
        "double_tap": ActionWeight(weight=2), "wait": ActionWeight(weight=5),
    })
    delay: DelayConfig = Field(default_factory=DelayConfig)
    #: Burst: repeat the same action N times rapidly with this probability.
    burst_probability: float = Field(default=0.05, ge=0, le=1)
    burst_max: int = Field(default=5, ge=1)
    targets: TargetsConfig = Field(default_factory=TargetsConfig)
    typing: TypingConfig = Field(default_factory=TypingConfig)


class OperationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    weight: float = Field(default=1.0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _bare(cls, data: Any) -> Any:
        if isinstance(data, bool):
            return {"enabled": data}
        if isinstance(data, int | float):
            return {"weight": float(data)}
        return data


class EntityFieldConfig(BaseModel):
    """Explicit field schema when automatic discovery is unavailable."""

    model_config = ConfigDict(extra="forbid")

    type: str = "string"  # string | number | integer | boolean | enum | date | email | id
    values: list[Any] = Field(default_factory=list)  # enum values
    min: float | None = None
    max: float | None = None
    required: bool = False
    #: Field shown on screen (used by context extraction / stale-entity detection).
    display: bool = False


class EntityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[str] = Field(default_factory=lambda: ["create", "update", "delete"])
    fields: dict[str, EntityFieldConfig] = Field(default_factory=dict)
    id_field: str = "id"
    #: For state-style backends: the state key holding the entity collection.
    state_key: str | None = None
    #: For REST backends: collection path (defaults to ``/<entity>``).
    path: str | None = None
    #: State key naming the entity currently shown on screen (context extraction).
    current_key: str | None = None
    #: Field values used to "disable"/"archive" (``{"status": "disabled"}``).
    disable: dict[str, Any] = Field(default_factory=dict)
    archive: dict[str, Any] = Field(default_factory=dict)


class ScheduledMutation(BaseModel):
    """A targeted chaos step: after action N (or a relevant screen), do X."""

    model_config = ConfigDict(extra="forbid")

    mutation: str
    entity: str
    entity_id: str | None = None
    #: ``before_action`` | ``after_action`` | ``on_context`` | ``delayed``
    timing: str = "after_action"
    delay: str | float = 0.0
    after_action_index: int | None = None
    #: Fire only while this text is on screen (OCR, case-insensitive) — e.g. "Your cart".
    when_text: str | None = None
    #: Fire every time the trigger matches (default: once per run).
    repeat: bool = False
    max_times: int | None = Field(default=None, ge=1)
    data: dict[str, Any] = Field(default_factory=dict)
    strategies: list[str] = Field(default_factory=list)

    @field_validator("delay", mode="before")
    @classmethod
    def _numeric_delay(cls, value: Any) -> Any:
        return float(value) if isinstance(value, int) and not isinstance(value, bool) else value


class BackendMutationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    probability: float = Field(default=0.15, ge=0, le=1)
    #: ``auto`` | ``state`` (collections inside the state document) | ``rest`` (collections
    #: as REST resources). ``auto`` picks ``rest`` when a schema endpoint or entity paths exist.
    style: str = "auto"
    #: Prefer entities visible on screen with this probability when known.
    contextual_probability: float = Field(default=0.8, ge=0, le=1)
    operations: dict[str, OperationConfig] = Field(default_factory=lambda: {
        "create": OperationConfig(weight=10), "update": OperationConfig(weight=50),
        "delete": OperationConfig(weight=10, enabled=False),
        "duplicate": OperationConfig(weight=5), "disable": OperationConfig(weight=5),
        "archive": OperationConfig(weight=3),
    })
    #: Explicit entity configuration (used when discovery is unavailable/partial).
    entities: dict[str, EntityConfig] = Field(default_factory=dict)
    #: Timing weights for randomly placed mutations.
    timing: dict[str, float] = Field(default_factory=lambda: {
        "before_action": 1.0, "after_action": 2.0, "delayed": 1.0,
    })
    delayed_max: str | float = "1s"
    #: Wait for the UI to reconcile before judging staleness.
    reconcile_timeout: str | float = "3s"
    scheduled: list[ScheduledMutation] = Field(default_factory=list)
    #: Discovery endpoint for REST backends (``GET`` returns the schema contract).
    schema_endpoint: str | None = None
    max_mutations: int | None = Field(default=None, ge=0)

    @property
    def delayed_max_seconds(self) -> float:
        return parse_duration(self.delayed_max)

    @property
    def reconcile_timeout_seconds(self) -> float:
        return parse_duration(self.reconcile_timeout)


class DataMutationsConfig(BaseModel):
    """Which data-mutation strategies may be applied (name → enabled)."""

    model_config = ConfigDict(extra="allow")

    #: Probability that an update/create applies at least one strategy.
    probability: float = Field(default=0.5, ge=0, le=1)
    max_per_mutation: int = Field(default=2, ge=1)

    @property
    def enabled(self) -> set[str]:
        return {
            name for name, value in (self.model_extra or {}).items()
            if value is True or (isinstance(value, dict) and value.get("enabled", True))
        }


class FaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    probability: float = Field(default=0.05, ge=0, le=1)
    injector: str = "backend"
    types: dict[str, float] = Field(default_factory=lambda: {
        "latency": 3.0, "timeout": 1.0, "http_error": 2.0, "disconnect": 1.0,
        "empty_response": 1.0, "malformed_response": 1.0,
    })
    latency_max: str | float = "2s"
    duration_max: str | float = "5s"
    http_statuses: list[int] = Field(default_factory=lambda: [400, 401, 403, 404, 409, 429, 500])

    @property
    def latency_max_seconds(self) -> float:
        return parse_duration(self.latency_max)

    @property
    def duration_max_seconds(self) -> float:
        return parse_duration(self.duration_max)


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration: str | float | None = "2m"
    max_actions: int | None = Field(default=500, ge=1)
    max_mutations: int | None = Field(default=None, ge=0)
    max_consecutive_actions: int = Field(default=50, ge=1)
    cooldown: str | float = "0s"
    max_runtime: str | float | None = None  # hard cap including shutdown/evidence
    #: Evidence: take an observation every N actions (1 = every action).
    observe_every: int = Field(default=1, ge=1)

    @property
    def duration_seconds(self) -> float | None:
        return parse_duration(self.duration) if self.duration is not None else None

    @property
    def cooldown_seconds(self) -> float:
        return parse_duration(self.cooldown)

    @property
    def max_runtime_seconds(self) -> float | None:
        return parse_duration(self.max_runtime) if self.max_runtime is not None else None


class FailurePolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_on_first: bool = False
    max_failures: int | None = Field(default=None, ge=1)
    #: Failures below this severity never stop the run.
    stop_severity: str = "error"
    #: Detector names disabled for this scenario.
    disabled_detectors: list[str] = Field(default_factory=list)
    #: After a crash is recorded, relaunch the application (when the device can) so the
    #: rest of the run stays useful instead of hammering a dead app.
    restart_after_crash: bool = True
    #: Words that mark an error screen (OCR).
    error_words: list[str] = Field(default_factory=lambda: [
        "error", "exception", "crashed", "something went wrong", "unexpected",
        "not responding", "null", "undefined", "stack trace", "traceback",
    ])
    #: Phrases that mean "the operation succeeded" (OCR). Seen together with a
    #: deleted/disabled entity's label they signal an *unexpected success*.
    success_words: list[str] = Field(default_factory=lambda: [
        "order confirmed", "thank you", "success", "saved", "submitted", "completed",
        "confirmed",
    ])
    #: How many actions after a destructive mutation the UI is watched for the entity.
    stale_window_actions: int = Field(default=25, ge=1)
    #: Observation-diff threshold below which two screens count as "unchanged".
    unchanged_threshold: float = Field(default=0.002, ge=0, le=1)
    frozen_after_actions: int = Field(default=8, ge=2)


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_destructive_mutations: bool = False
    allowed_entities: list[str] = Field(default_factory=list)
    denied_entities: list[str] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)
    denied_operations: list[str] = Field(default_factory=list)
    #: The backend must identify as one of these; ``environment`` names ours.
    environment: str = ""
    allowed_environments: list[str] = Field(default_factory=lambda: ["test", "local", "fake"])
    #: Refuse mutations when the backend cannot confirm capabilities.
    require_capabilities: bool = True
    dry_run: bool = False


class EvidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Recent history kept in memory and written with each failure.
    history: int = Field(default=50, ge=1)
    #: Recent observations (images) retained for before/after evidence.
    observations: int = Field(default=3, ge=1)
    save_screenshots: bool = True
    save_logs: bool = True
    #: Also save a screenshot every N actions (0 = only on failures).
    sample_every: int = Field(default=0, ge=0)
    max_failures_with_evidence: int = Field(default=100, ge=1)


class StressConfig(BaseModel):
    """``stress:`` — see docs/stress-testing.md."""

    model_config = ConfigDict(extra="forbid")

    name: str = "stress"
    description: str = ""
    seed: int | None = Field(default=None, ge=1)
    device: str | None = None
    duration: str | float | None = None  # shorthand for limits.duration
    max_actions: int | None = None  # shorthand for limits.max_actions
    monkey: MonkeyConfig = Field(default_factory=MonkeyConfig)
    backend_mutations: BackendMutationsConfig = Field(default_factory=BackendMutationsConfig)
    data_mutations: DataMutationsConfig = Field(default_factory=DataMutationsConfig)
    faults: FaultConfig = Field(default_factory=FaultConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    failures: FailurePolicyConfig = Field(default_factory=FailurePolicyConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    #: Minimization settings.
    minimize_max_iterations: int = Field(default=200, ge=1)
    results_dir: str = "results/stress"

    @model_validator(mode="after")
    def _apply_shorthands(self) -> StressConfig:
        if self.duration is not None:
            self.limits.duration = self.duration
        if self.max_actions is not None:
            self.limits.max_actions = self.max_actions
        return self

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("stress.name must not be empty")
        return cleaned

    @property
    def destructive_operations(self) -> frozenset[str]:
        return frozenset({"delete", "archive", "disable"})


def load_scenario(path: str | Path) -> tuple[StressConfig, dict[str, Any]]:
    """Load a scenario file.

    Returns ``(stress_config, overrides)`` where ``overrides`` holds any top-level
    ``backend``/``devices``/``ocr``/``variables`` sections to layer over the main
    configuration.
    """
    file = Path(path)
    if not file.is_file():
        raise ConfigurationError(
            f"Scenario file not found: {file}",
            remediation="Pass an existing YAML file to --scenario.",
        )
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Scenario {file} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Scenario {file} must be a mapping.")
    overrides = {k: raw[k] for k in ("backend", "devices", "ocr", "variables", "regions")
                 if k in raw}
    body = raw.get("stress")
    if body is None:
        body = {k: v for k, v in raw.items() if k not in overrides}
    if not isinstance(body, dict):
        raise ConfigurationError(f"Scenario {file}: 'stress' must be a mapping.")
    body.setdefault("name", file.stem)
    try:
        config = StressConfig.model_validate(body)
    except Exception as exc:  # noqa: BLE001 - pydantic errors → configuration error
        raise ConfigurationError(
            f"Invalid scenario {file}: {exc}",
            remediation="See docs/stress-testing.md for the configuration reference.",
        ) from exc
    return config, overrides


__all__ = [
    "ActionWeight", "BackendMutationsConfig", "DataMutationsConfig", "DelayConfig",
    "EntityConfig", "EntityFieldConfig", "EvidenceConfig", "FailurePolicyConfig",
    "FaultConfig", "LimitsConfig", "MonkeyConfig", "OperationConfig", "SafetyConfig",
    "ScheduledMutation", "StressConfig", "TargetRegion", "TargetsConfig", "TypingConfig",
    "load_scenario",
]
