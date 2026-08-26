"""CI run result model and its versioned JSON serialization.

The engine's :class:`~argus.models.results.RunResult` / ``TestResult`` stay
the source of truth; this module *extends* them with CI classification
(outcome, category, known-failure, flaky) and run-level metadata. Internal
classes are not the public API — ``report.json`` is produced by
:func:`ci_result_to_dict` and versioned by :data:`REPORT_SCHEMA_VERSION`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from argus import __version__
from argus.ci.categories import FailureCategory
from argus.ci.context import CIContext
from argus.models.results import AttemptRecord, PreflightResult, RunResult, TestStatus

#: Bump only for backward-incompatible changes to ``report.json``.
REPORT_SCHEMA_VERSION = 1


class TestOutcome(StrEnum):
    """CI-level outcome (richer than the engine's four statuses)."""

    PASSED = "passed"
    FLAKY_PASSED = "flaky_passed"
    FAILED = "failed"
    ERROR = "error"
    KNOWN_FAILURE = "known_failure"
    SKIPPED = "skipped"
    NOT_RUN = "not_run"


class CIRunStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    CANCELLED = "cancelled"
    NOT_RUN = "not_run"


class CITestResult(BaseModel):
    """One executed (or planned) test as seen by CI."""

    test_id: str
    name: str
    feature: str
    platform: str | None = None
    status: TestStatus
    outcome: TestOutcome
    duration: float = 0.0
    attempts: int = 1
    flaky: bool = False
    initial_failure: str | None = None
    failure_category: FailureCategory | None = None
    failure_message: str | None = None
    known_failure_reason: str | None = None
    worker: int | None = None
    artifact_dir: str | None = None
    #: Artifact paths relative to the CI output directory.
    artifacts: list[str] = Field(default_factory=list)
    attempt_history: list[AttemptRecord] = Field(default_factory=list)

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.test_id, self.platform)

    @property
    def succeeded(self) -> bool:
        return self.outcome in (TestOutcome.PASSED, TestOutcome.FLAKY_PASSED)


class PolicyViolation(BaseModel):
    rule: str
    action: str  # "fail" | "warn"
    message: str
    tests: list[str] = Field(default_factory=list)


class PolicyResult(BaseModel):
    status: str = "passed"  # "passed" | "warned" | "failed"
    violations: list[PolicyViolation] = Field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def failing_rules(self) -> set[str]:
        return {v.rule for v in self.violations if v.action == "fail"}


class ArtifactEntry(BaseModel):
    path: str  # relative to the output directory (POSIX)
    kind: str
    size: int = 0
    test_id: str | None = None
    platform: str | None = None


class RetrySummary(BaseModel):
    enabled: bool = False
    max_attempts: int = 1
    on: list[str] = Field(default_factory=list)


class CIRunResult(BaseModel):
    """Everything ``argus ci run`` knows about one run."""

    run_id: str
    status: CIRunStatus
    provider: str
    suite: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    duration: float = 0.0
    workers: int = 1
    strategy: str = "sequential"
    retry: RetrySummary = Field(default_factory=RetrySummary)
    selection: dict[str, Any] = Field(default_factory=dict)
    tests: list[CITestResult] = Field(default_factory=list)
    policy: PolicyResult = Field(default_factory=PolicyResult)
    context: CIContext
    preflight: list[PreflightResult] = Field(default_factory=list)
    artifacts_dir: str | None = None
    artifacts: list[ArtifactEntry] = Field(default_factory=list)
    error: str | None = None
    error_category: FailureCategory | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    #: Merged engine result (JUnit/HTML writers consume it); not serialized here.
    engine_result: RunResult | None = Field(default=None, exclude=True)

    # -- counts (computed once per access; cheap for 10k tests) -------------------

    def _count(self, *outcomes: TestOutcome) -> int:
        return sum(1 for t in self.tests if t.outcome in outcomes)

    @property
    def total(self) -> int:
        return len(self.tests)

    @property
    def passed_count(self) -> int:
        return self._count(TestOutcome.PASSED, TestOutcome.FLAKY_PASSED)

    @property
    def failed_count(self) -> int:
        return self._count(TestOutcome.FAILED)

    @property
    def errored_count(self) -> int:
        return self._count(TestOutcome.ERROR)

    @property
    def skipped_count(self) -> int:
        return self._count(TestOutcome.SKIPPED)

    @property
    def not_run_count(self) -> int:
        return self._count(TestOutcome.NOT_RUN)

    @property
    def flaky_count(self) -> int:
        return sum(1 for t in self.tests if t.flaky)

    @property
    def known_failure_count(self) -> int:
        return self._count(TestOutcome.KNOWN_FAILURE)

    @property
    def visual_regressions(self) -> list[CITestResult]:
        return [
            t
            for t in self.tests
            if t.failure_category == FailureCategory.VISUAL_REGRESSION
            and t.outcome in (TestOutcome.FAILED, TestOutcome.ERROR)
        ]

    def summary_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "policy_status": self.policy.status,
            "total": self.total,
            "passed": self.passed_count,
            "failed": self.failed_count,
            "errored": self.errored_count,
            "skipped": self.skipped_count,
            "not_run": self.not_run_count,
            "flaky": self.flaky_count,
            "known_failures": self.known_failure_count,
            "visual_regressions": len(self.visual_regressions),
            "duration": round(self.duration, 3),
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ci_result_to_dict(result: CIRunResult) -> dict[str, Any]:
    """The canonical machine-readable report (``report.json``), schema v1."""
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report": "argus-ci",
        "argus_version": __version__,
        "run": {
            "run_id": result.run_id,
            "status": result.status.value,
            "suite": result.suite,
            "provider": result.provider,
            "started_at": _iso(result.started_at),
            "finished_at": _iso(result.finished_at),
            "duration": round(result.duration, 3),
            "workers": result.workers,
            "strategy": result.strategy,
            "retry": result.retry.model_dump(mode="json"),
            "selection": result.selection,
            "error": result.error,
            "error_category": result.error_category.value if result.error_category else None,
            "timings": {k: round(v, 3) for k, v in result.timings.items()},
        },
        "ci": result.context.to_dict(),
        "summary": result.summary_dict(),
        "tests": [
            {
                "test_id": t.test_id,
                "name": t.name,
                "feature": t.feature,
                "platform": t.platform,
                "status": t.status.value,
                "outcome": t.outcome.value,
                "duration": round(t.duration, 3),
                "attempts": t.attempts,
                "flaky": t.flaky,
                "initial_failure": t.initial_failure,
                "failure_category": t.failure_category.value if t.failure_category else None,
                "failure_message": t.failure_message,
                "known_failure_reason": t.known_failure_reason,
                "worker": t.worker,
                "artifacts": list(t.artifacts),
                "attempt_history": [a.model_dump(mode="json") for a in t.attempt_history],
            }
            for t in result.tests
        ],
        "artifacts": [a.model_dump(mode="json") for a in result.artifacts],
        "policy": result.policy.model_dump(mode="json"),
        "preflight": [
            {
                "name": p.name,
                "passed": p.passed,
                "required": p.required,
                "error": p.error,
                "remediation": p.remediation,
            }
            for p in result.preflight
        ],
    }
