"""Engine extensions the CI layer relies on (retry override, cancellation, run dir)."""

import threading

import pytest
from tests.ci.conftest import passing_test

from argus.engine.filters import TestFilter
from argus.engine.runner import RetryOverride, RunOptions, TestRunner
from argus.models.results import RunStatus, TestStatus

pytestmark = pytest.mark.integration


def test_retry_override_records_every_attempt_and_flags_flaky(project):
    project.write_tests([passing_test("R-001")])
    project.write_config(
        devices={
            "fake_android": project.device("fake_android", "android", fail_first_screenshots=1)
        }
    )
    config = project.load()
    options = RunOptions(
        filters=TestFilter(),
        retry=RetryOverride(max_attempts=2, categories=frozenset({"screenshot"})),
        results_dir=project.root / "pinned",
        skip_preflight=True,  # preflight's screenshot probe would consume the injected failure
    )
    result = TestRunner(config).run(options)
    assert result.status == RunStatus.PASSED
    test = result.tests[0]
    assert test.status == TestStatus.PASSED
    assert test.attempts == 2 and test.flaky
    assert test.initial_failure == "screenshot"
    assert [a.status for a in test.attempt_history] == [TestStatus.FAILED, TestStatus.PASSED]
    # Attempt 1's evidence is kept in its own directory (never overwritten).
    first = test.attempt_history[0].artifact_dir
    assert first is not None and first.endswith("R-001_android")
    assert (
        test.attempt_history[1].artifact_dir is None
        or "attempt2" in test.attempt_history[1].artifact_dir
    )
    assert result.results_dir == str(project.root / "pinned")


def test_retry_override_does_not_retry_non_retryable_categories(project):
    project.write_tests([passing_test("R-002")])
    project.write_config(
        devices={
            "fake_android": project.device("fake_android", "android", fail_first_screenshots=1)
        }
    )
    options = RunOptions(
        retry=RetryOverride(max_attempts=3, categories=frozenset({"timeout"})),
        skip_preflight=True,
    )
    result = TestRunner(project.load()).run(options)
    test = result.tests[0]
    assert test.status == TestStatus.FAILED
    assert test.attempts == 1 and not test.flaky


def test_retry_exhausted_keeps_final_classification(project):
    project.write_tests([passing_test("R-003")])
    project.write_config(
        devices={
            "fake_android": project.device("fake_android", "android", fail_first_screenshots=5)
        }
    )
    options = RunOptions(
        retry=RetryOverride(max_attempts=2, categories=frozenset({"screenshot"})),
        skip_preflight=True,
    )
    result = TestRunner(project.load()).run(options)
    test = result.tests[0]
    assert test.status == TestStatus.FAILED and test.attempts == 2 and not test.flaky
    assert test.failure_category == "screenshot"
    assert len(test.attempt_history) == 2


def test_cancellation_token_stops_scheduling_and_marks_cancelled(project):
    project.write_tests([passing_test("C-001"), passing_test("C-002"), passing_test("C-003")])
    project.write_config()
    cancel = threading.Event()
    config = project.load()
    runner = TestRunner(config)
    # Cancel as soon as the first test completes.
    from argus.events.events import TestPassed

    runner.events.subscribe(lambda e: cancel.set(), TestPassed)
    result = runner.run(RunOptions(cancel=cancel))
    assert result.status == RunStatus.CANCELLED
    assert result.stopped_early and result.stop_reason == "cancelled"
    statuses = [t.status for t in result.tests]
    assert statuses == [TestStatus.PASSED, TestStatus.SKIPPED, TestStatus.SKIPPED]
    assert all(t.error == "cancelled" for t in result.tests[1:])


def test_skip_setup_option(project, tmp_path):
    marker = tmp_path / "setup-ran"
    project.write_tests([passing_test("S-001")])
    project.write_config(extra={"setup": [{"command": "touch", "args": [str(marker)]}]})
    TestRunner(project.load()).run(RunOptions(skip_setup=True))
    assert not marker.exists()
    TestRunner(project.load()).run(RunOptions())
    assert marker.exists()
