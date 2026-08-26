"""Failure classification, categories, and exit codes."""

from argus.ci.categories import (
    FailureCategory,
    canonical_retry_category,
    retry_categories_for_engine,
)
from argus.ci.classify import CancelledRun, ReportingError, classify_exception, classify_test
from argus.ci.exit_codes import ExitCode
from argus.exceptions import (
    BackendError,
    ConfigurationError,
    DeviceConnectionError,
    PreflightError,
    ScreenshotError,
    TestDefinitionError,
    UTFError,
)
from argus.models.results import StepResult, TestResult, TestStatus, VerificationResult


def _result(status, category=None, *, image=False):
    steps = []
    if status != TestStatus.PASSED:
        verification = (
            VerificationResult(passed=False, verifier="image_present", details={"image": "x.png"})
            if image
            else None
        )
        steps.append(
            StepResult(
                action="verify", passed=False, failure_category=category, verification=verification
            )
        )
    return TestResult(
        test_id="T", name="t", feature="F", status=status, failure_category=category, steps=steps
    )


def test_classify_passed_and_skipped():
    assert classify_test(_result(TestStatus.PASSED)) is None
    assert classify_test(_result(TestStatus.SKIPPED)) is None


def test_classify_assertion_vs_visual_regression():
    assert (
        classify_test(_result(TestStatus.FAILED, "assertion")) == FailureCategory.ASSERTION_FAILURE
    )
    assert (
        classify_test(_result(TestStatus.FAILED, "assertion", image=True))
        == FailureCategory.VISUAL_REGRESSION
    )


def test_classify_engine_categories():
    assert classify_test(_result(TestStatus.FAILED, "timeout")) == FailureCategory.TIMEOUT
    assert (
        classify_test(_result(TestStatus.ERROR, "device_connection"))
        == FailureCategory.DEVICE_ERROR
    )
    assert classify_test(_result(TestStatus.FAILED, "backend")) == FailureCategory.CONNECTION_ERROR
    assert (
        classify_test(_result(TestStatus.FAILED, "screenshot"))
        == FailureCategory.SCREENSHOT_CAPTURE_ERROR
    )
    assert classify_test(_result(TestStatus.ERROR, "error")) == FailureCategory.INTERNAL_ERROR
    assert (
        classify_test(_result(TestStatus.FAILED, "feature_setup")) == FailureCategory.TEST_FAILURE
    )
    assert classify_test(_result(TestStatus.FAILED, None)) == FailureCategory.TEST_FAILURE


def test_classify_exceptions_to_exit_codes():
    assert classify_exception(ConfigurationError("x")) == (
        FailureCategory.CONFIGURATION_ERROR,
        ExitCode.CONFIGURATION_ERROR,
    )
    assert classify_exception(TestDefinitionError("x"))[1] == ExitCode.TEST_DEFINITION_ERROR
    assert classify_exception(DeviceConnectionError("x"))[1] == ExitCode.ENVIRONMENT_ERROR
    assert classify_exception(PreflightError("x"))[1] == ExitCode.ENVIRONMENT_ERROR
    assert classify_exception(BackendError("x"))[1] == ExitCode.ENVIRONMENT_ERROR
    assert classify_exception(ScreenshotError("x"))[1] == ExitCode.ENVIRONMENT_ERROR
    assert classify_exception(ReportingError("x"))[1] == ExitCode.CI_ERROR
    assert classify_exception(UTFError("x"))[1] == ExitCode.CI_ERROR
    assert classify_exception(CancelledRun("x"))[1] == ExitCode.CANCELLED
    assert classify_exception(KeyboardInterrupt())[1] == ExitCode.CANCELLED
    assert classify_exception(RuntimeError("boom")) == (
        FailureCategory.INTERNAL_ERROR,
        ExitCode.INTERNAL_ERROR,
    )


def test_exit_code_contract_is_stable():
    assert [int(c) for c in ExitCode] == [0, 1, 2, 3, 4, 5, 6, 7, 8]
    assert ExitCode.POLICY_FAILURE.description == "quality-policy failure"


def test_retry_category_aliases_and_translation():
    assert canonical_retry_category("device_timeout") == "timeout"
    assert canonical_retry_category("device_disconnected") == "device_error"
    assert canonical_retry_category("assertion_failure") is None
    assert retry_categories_for_engine(["timeout", "device_error", "bogus"]) == frozenset(
        {"timeout", "device_connection"}
    )
