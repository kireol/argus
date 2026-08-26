"""Structured failure classification for CI (no error-string matching).

Both functions rely on the engine's structured data: ``failure_category``
on results and the exception hierarchy in :mod:`argus.exceptions`.
"""

from __future__ import annotations

from argus.ci.categories import ENGINE_TO_CI, FailureCategory
from argus.ci.exit_codes import ExitCode
from argus.exceptions import (
    BackendError,
    ConfigurationError,
    DeviceConnectionError,
    PreflightError,
    ScreenshotError,
    TestDefinitionError,
    TimeoutExceededError,
    UTFError,
)
from argus.models.results import TestResult, TestStatus


def classify_test(result: TestResult) -> FailureCategory | None:
    """CI category for a finished test (``None`` for passed/skipped)."""
    if result.status not in (TestStatus.FAILED, TestStatus.ERROR):
        return None
    category = ENGINE_TO_CI.get(result.failure_category or "", FailureCategory.TEST_FAILURE)
    if category == FailureCategory.ASSERTION_FAILURE and _compared_images(result):
        return FailureCategory.VISUAL_REGRESSION
    return category


def _compared_images(result: TestResult) -> bool:
    failed = next((s for s in result.steps if not s.passed), None)
    if failed is None or failed.verification is None:
        return False
    return bool(failed.verification.details.get("image"))


def classify_exception(exc: BaseException) -> tuple[FailureCategory, ExitCode]:
    """Map an exception escaping the run to (category, exit code)."""
    if isinstance(exc, ConfigurationError):
        return FailureCategory.CONFIGURATION_ERROR, ExitCode.CONFIGURATION_ERROR
    if isinstance(exc, TestDefinitionError):
        return FailureCategory.TEST_DEFINITION_ERROR, ExitCode.TEST_DEFINITION_ERROR
    if isinstance(exc, (DeviceConnectionError, PreflightError, TimeoutExceededError)):
        return FailureCategory.DEVICE_ERROR, ExitCode.ENVIRONMENT_ERROR
    if isinstance(exc, BackendError):
        return FailureCategory.CONNECTION_ERROR, ExitCode.ENVIRONMENT_ERROR
    if isinstance(exc, ScreenshotError):
        return FailureCategory.SCREENSHOT_CAPTURE_ERROR, ExitCode.ENVIRONMENT_ERROR
    if isinstance(exc, (KeyboardInterrupt, CancelledRun)):
        return FailureCategory.INFRASTRUCTURE_ERROR, ExitCode.CANCELLED
    if isinstance(exc, UTFError):
        return FailureCategory.INFRASTRUCTURE_ERROR, ExitCode.CI_ERROR
    return FailureCategory.INTERNAL_ERROR, ExitCode.INTERNAL_ERROR


class CancelledRun(UTFError):
    """Raised internally when cancellation interrupts a phase before tests ran."""


class ReportingError(UTFError):
    """Publishing artifacts or provider reports failed (exit code 5)."""
