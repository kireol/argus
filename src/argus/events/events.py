"""Event model published during a test run.

A future GUI subscribes to these events for real-time progress; the console
and JSON reporters are the first consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from argus.models.results import (
    PreflightResult,
    RunResult,
    StepResult,
    TestResult,
    VerificationResult,
)


@dataclass(frozen=True)
class Event:
    """Base event."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC), init=False)


@dataclass(frozen=True)
class TestRunStarted(Event):
    total_tests: int
    filters: dict[str, Any] = field(default_factory=dict)
    #: 1-based index of the first test that will run (for ``--skip-to``).
    start_index: int = 1


@dataclass(frozen=True)
class PreflightStarted(Event):
    total_checks: int


@dataclass(frozen=True)
class PreflightCheckCompleted(Event):
    result: PreflightResult


@dataclass(frozen=True)
class PreflightCompleted(Event):
    results: list[PreflightResult]
    passed: bool


@dataclass(frozen=True)
class TestStarted(Event):
    test_id: str
    name: str
    feature: str
    platform: str | None = None


@dataclass(frozen=True)
class ActionStarted(Event):
    test_id: str
    action: str
    step_index: int


@dataclass(frozen=True)
class ActionCompleted(Event):
    test_id: str
    action: str
    step_index: int
    result: StepResult


@dataclass(frozen=True)
class VerificationStarted(Event):
    test_id: str
    verifier: str


@dataclass(frozen=True)
class VerificationCompleted(Event):
    test_id: str
    verifier: str
    result: VerificationResult


@dataclass(frozen=True)
class TestPassed(Event):
    result: TestResult


@dataclass(frozen=True)
class TestFailed(Event):
    result: TestResult


@dataclass(frozen=True)
class TestSkipped(Event):
    result: TestResult


@dataclass(frozen=True)
class TestRunCompleted(Event):
    result: RunResult
