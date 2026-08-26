"""Pydantic models for YAML test definitions.

Test authors write declarative YAML; these models validate structure early so
authoring mistakes fail fast with clear messages, before any test runs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from argus.utilities.duration import parse_duration

# Failure categories eligible for retry. Assertion failures are deliberately
# not retryable by default — retrying them hides real product bugs.
RETRYABLE_CATEGORIES = {"timeout", "device_connection", "backend", "screenshot"}


class RetryPolicy(BaseModel):
    """Explicit, opt-in retry policy for a test."""

    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=0, ge=0, le=10)
    only: list[str] = Field(default_factory=lambda: ["timeout", "device_connection"])

    @field_validator("only")
    @classmethod
    def _known_categories(cls, value: list[str]) -> list[str]:
        unknown = set(value) - RETRYABLE_CATEGORIES
        if unknown:
            raise ValueError(
                f"Unknown retry categories: {sorted(unknown)}. "
                f"Allowed: {sorted(RETRYABLE_CATEGORIES)}"
            )
        return value


class ConditionSpec(BaseModel):
    """A condition reference in a test definition.

    Either a leaf condition (``type`` + parameters) or a composite
    (``all`` / ``any`` / ``not``).
    """

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    all: list[ConditionSpec] | None = None
    any: list[ConditionSpec] | None = None
    not_: ConditionSpec | None = Field(default=None, alias="not")

    @model_validator(mode="after")
    def _exactly_one_form(self) -> ConditionSpec:
        forms = [
            self.type is not None,
            self.all is not None,
            self.any is not None,
            self.not_ is not None,
        ]
        if sum(forms) != 1:
            raise ValueError(
                "A condition must have exactly one of: 'type', 'all', 'any', 'not'"
            )
        return self

    @property
    def params(self) -> dict[str, Any]:
        """Leaf condition parameters (everything except the structural fields)."""
        return {
            k: v
            for k, v in (self.model_extra or {}).items()
            if k not in {"type", "all", "any", "not"}
        }


class Step(BaseModel):
    """A single declarative test step.

    ``action`` names a registered action plugin; all other keys are passed to
    the action as parameters (after variable expansion).
    """

    model_config = ConfigDict(extra="allow")

    action: str
    name: str | None = None

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class FeatureDefinition(BaseModel):
    """Feature-level lifecycle: steps run once per feature (per platform)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    setup: list[Step] = Field(default_factory=list)
    teardown: list[Step] = Field(default_factory=list)

    # Populated by the loader; not authored in YAML.
    source_file: str | None = None


class TestDefinition(BaseModel):
    """A complete YAML test definition."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1)
    description: str = ""
    feature: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)

    # Optional fields
    priority: str | None = None
    timeout: str | float | None = None
    requires: dict[str, Any] = Field(default_factory=dict)
    setup: list[Step] = Field(default_factory=list)
    teardown: list[Step] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    # Populated by the loader; not authored in YAML.
    source_file: str | None = None

    @field_validator("timeout")
    @classmethod
    def _valid_timeout(cls, value: str | float | None) -> str | float | None:
        if value is not None:
            try:
                parse_duration(value)
            except Exception as exc:
                # Re-raise as ValueError so pydantic reports it as a field error.
                raise ValueError(str(exc)) from exc
        return value

    @property
    def timeout_seconds(self) -> float | None:
        return parse_duration(self.timeout) if self.timeout is not None else None

    @property
    def required_devices(self) -> list[str]:
        devices = self.requires.get("devices", [])
        return list(devices) if isinstance(devices, list) else [devices]
