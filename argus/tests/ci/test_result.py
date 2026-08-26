"""report.json serialization contract."""

import json

from argus import __version__
from argus.ci.context import CIContext
from argus.ci.result import (
    REPORT_SCHEMA_VERSION,
    CIRunResult,
    CIRunStatus,
    CITestResult,
    TestOutcome,
    ci_result_to_dict,
)
from argus.models.results import AttemptRecord, TestStatus


def test_report_schema():
    result = CIRunResult(
        run_id="r1",
        status=CIRunStatus.PASSED,
        provider="local",
        context=CIContext(provider="local", display_name="Local"),
        tests=[
            CITestResult(
                test_id="T1",
                name="t",
                feature="F",
                status=TestStatus.PASSED,
                outcome=TestOutcome.FLAKY_PASSED,
                flaky=True,
                attempts=2,
                initial_failure="timeout",
                attempt_history=[
                    AttemptRecord(attempt=1, status=TestStatus.FAILED, failure_category="timeout"),
                    AttemptRecord(attempt=2, status=TestStatus.PASSED),
                ],
            )
        ],
        timings={"total": 1.23456},
    )
    data = ci_result_to_dict(result)
    json.dumps(data)  # serializable
    assert data["schema_version"] == REPORT_SCHEMA_VERSION == 1
    assert data["report"] == "argus-ci"
    assert data["argus_version"] == __version__
    assert set(data) == {
        "schema_version",
        "report",
        "argus_version",
        "run",
        "ci",
        "summary",
        "tests",
        "artifacts",
        "policy",
        "preflight",
    }
    assert data["run"]["started_at"].endswith("Z")
    assert data["run"]["timings"]["total"] == 1.235
    assert data["summary"]["flaky"] == 1 and data["summary"]["passed"] == 1
    test = data["tests"][0]
    assert test["outcome"] == "flaky_passed" and test["initial_failure"] == "timeout"
    assert [a["attempt"] for a in test["attempt_history"]] == [1, 2]
    assert data["policy"] == {"status": "passed", "violations": []}


def test_counts():
    def t(outcome, flaky=False):
        return CITestResult(
            test_id="x",
            name="x",
            feature="F",
            status=TestStatus.PASSED,
            outcome=outcome,
            flaky=flaky,
        )

    result = CIRunResult(
        run_id="r",
        status=CIRunStatus.FAILED,
        provider="local",
        context=CIContext(provider="local", display_name="Local"),
        tests=[
            t(TestOutcome.PASSED),
            t(TestOutcome.FLAKY_PASSED, True),
            t(TestOutcome.FAILED),
            t(TestOutcome.ERROR),
            t(TestOutcome.KNOWN_FAILURE),
            t(TestOutcome.SKIPPED),
            t(TestOutcome.NOT_RUN),
        ],
    )
    assert (result.total, result.passed_count, result.failed_count, result.errored_count) == (
        7,
        2,
        1,
        1,
    )
    assert (result.skipped_count, result.not_run_count, result.flaky_count) == (1, 1, 1)
    assert result.known_failure_count == 1
