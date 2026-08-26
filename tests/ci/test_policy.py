"""Quality policy engine."""

from argus.ci.categories import FailureCategory
from argus.ci.policy import evaluate_policy
from argus.ci.result import CITestResult, TestOutcome
from argus.config.models import CIPolicyConfig, CIPolicyRule
from argus.models.results import TestStatus


def _t(test_id, outcome, *, category=None, flaky=False, platform=None):
    status = {
        TestOutcome.PASSED: TestStatus.PASSED,
        TestOutcome.FLAKY_PASSED: TestStatus.PASSED,
        TestOutcome.FAILED: TestStatus.FAILED,
        TestOutcome.ERROR: TestStatus.ERROR,
        TestOutcome.KNOWN_FAILURE: TestStatus.FAILED,
        TestOutcome.SKIPPED: TestStatus.SKIPPED,
        TestOutcome.NOT_RUN: TestStatus.SKIPPED,
    }[outcome]
    return CITestResult(
        test_id=test_id,
        name=test_id,
        feature="F",
        platform=platform,
        status=status,
        outcome=outcome,
        failure_category=category,
        flaky=flaky,
        attempts=2 if flaky else 1,
    )


def test_all_passed():
    result = evaluate_policy(CIPolicyConfig(), [_t("A", TestOutcome.PASSED)])
    assert result.status == "passed" and not result.violations


def test_failure_fails_by_default_and_can_warn():
    tests = [_t("A", TestOutcome.FAILED, category=FailureCategory.ASSERTION_FAILURE)]
    failed = evaluate_policy(CIPolicyConfig(), tests)
    assert failed.status == "failed"
    assert failed.failing_rules == {"failures"}
    warned = evaluate_policy(CIPolicyConfig(failures=CIPolicyRule(action="warn")), tests)
    assert warned.status == "warned"
    ignored = evaluate_policy(CIPolicyConfig(failures=CIPolicyRule(action="ignore")), tests)
    assert ignored.status == "passed"


def test_visual_regression_is_its_own_rule():
    tests = [_t("V", TestOutcome.FAILED, category=FailureCategory.VISUAL_REGRESSION)]
    result = evaluate_policy(CIPolicyConfig(failures=CIPolicyRule(action="ignore")), tests)
    assert result.failing_rules == {"visual_regression"}
    result = evaluate_policy(CIPolicyConfig(visual_regression=CIPolicyRule(action="warn")), tests)
    assert result.status == "warned"


def test_known_failure_warns_by_default_and_never_disappears():
    tests = [_t("K", TestOutcome.KNOWN_FAILURE, category=FailureCategory.ASSERTION_FAILURE)]
    result = evaluate_policy(CIPolicyConfig(), tests)
    assert result.status == "warned"
    assert result.violations[0].rule == "known_failure"
    assert result.violations[0].tests == ["K"]
    strict = evaluate_policy(CIPolicyConfig(known_failure=CIPolicyRule(action="fail")), tests)
    assert strict.status == "failed"


def test_flaky_rule():
    tests = [_t("F", TestOutcome.FLAKY_PASSED, flaky=True)]
    assert evaluate_policy(CIPolicyConfig(), tests).status == "warned"
    strict = evaluate_policy(CIPolicyConfig(flaky=CIPolicyRule(action="fail")), tests)
    assert strict.failing_rules == {"flaky"}


def test_required_suite_passes_and_fails():
    policy = CIPolicyConfig(required=["smoke"])
    ok = evaluate_policy(
        policy,
        [_t("S1", TestOutcome.PASSED), _t("S2", TestOutcome.PASSED)],
        required_selection={"smoke": {"S1", "S2"}},
    )
    assert ok.status == "passed"
    bad = evaluate_policy(
        policy,
        [_t("S1", TestOutcome.PASSED), _t("S2", TestOutcome.NOT_RUN)],
        required_selection={"smoke": {"S1", "S2"}},
    )
    assert bad.failing_rules == {"required"}
    assert "S2" in bad.violations[0].tests


def test_required_suite_not_selected_is_not_a_violation():
    result = evaluate_policy(
        CIPolicyConfig(required=["smoke"]),
        [_t("OTHER", TestOutcome.PASSED)],
        required_selection={"smoke": {"S1"}},
    )
    assert result.status == "passed"


def test_required_suite_fails_when_run_could_not_execute():
    result = evaluate_policy(
        CIPolicyConfig(required=["smoke"]),
        [_t("S1", TestOutcome.NOT_RUN)],
        required_selection={"smoke": {"S1"}},
        run_failed="pre-flight checks failed",
    )
    assert result.status == "failed"
    assert "did not execute" in result.violations[0].message
