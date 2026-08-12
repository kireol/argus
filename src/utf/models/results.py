"""Result models for verifications, steps, tests, and whole runs."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from utf.models.common import Region


class TestStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    STOPPED = "stopped"
    PREFLIGHT_FAILED = "preflight_failed"


class VerificationResult(BaseModel):
    """Outcome of a single verifier evaluation."""

    passed: bool
    verifier: str = ""
    confidence: float | None = None
    location: Region | None = None
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class StepResult(BaseModel):
    """Outcome of a single test step."""

    action: str
    name: str | None = None
    passed: bool
    duration: float = 0.0
    message: str = ""
    error: str | None = None
    failure_category: str | None = None
    verification: VerificationResult | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class TestResult(BaseModel):
    """Outcome of a single test."""

    test_id: str
    name: str
    feature: str
    platform: str | None = None
    status: TestStatus
    duration: float = 0.0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    steps: list[StepResult] = Field(default_factory=list)
    error: str | None = None
    failure_category: str | None = None
    attempts: int = 1
    artifact_dir: str | None = None
    instrumentation_state: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.status == TestStatus.PASSED


class PreflightResult(BaseModel):
    """Outcome of a single pre-flight check."""

    name: str
    passed: bool
    required: bool = True
    skipped: bool = False
    duration: float = 0.0
    target: str | None = None
    error: str | None = None
    remediation: str | None = None
    causes: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    """Outcome of a complete test run."""

    status: RunStatus
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration: float = 0.0
    preflight: list[PreflightResult] = Field(default_factory=list)
    tests: list[TestResult] = Field(default_factory=list)
    results_dir: str | None = None
    stopped_early: bool = False
    stop_reason: str | None = None

    @property
    def executed(self) -> int:
        return len([t for t in self.tests if t.status != TestStatus.SKIPPED])

    @property
    def passed_count(self) -> int:
        return len([t for t in self.tests if t.status == TestStatus.PASSED])

    @property
    def failed_count(self) -> int:
        return len(
            [t for t in self.tests if t.status in (TestStatus.FAILED, TestStatus.ERROR)]
        )

    @property
    def skipped_count(self) -> int:
        return len([t for t in self.tests if t.status == TestStatus.SKIPPED])
