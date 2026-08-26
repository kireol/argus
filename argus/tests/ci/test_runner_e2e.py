"""End-to-end ``CIRunner`` behavior against the fake adapters."""

import io
import json
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from rich.console import Console
from tests.ci.conftest import assertion_failing_test, passing_test, visual_failing_test

from argus.ci.exit_codes import ExitCode
from argus.ci.result import CIRunStatus, TestOutcome
from argus.ci.runner import CIRunner, CIRunRequest, new_run_id
from argus.engine.filters import build_filter

pytestmark = pytest.mark.integration

GITHUB_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "kireol/argus",
    "GITHUB_REF": "refs/pull/7/merge",
    "GITHUB_HEAD_REF": "feature/ci",
    "GITHUB_SHA": "abc1234567",
    "GITHUB_TOKEN": "ghs_supersecret",
}


def _runner(project, env=None, **kwargs) -> CIRunner:
    """A CIRunner whose console output is captured in ``runner.console.file``."""
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    return CIRunner(project.load(), environment=env or {}, console=console, **kwargs)


def _report(project) -> dict:
    return json.loads((project.output / "report.json").read_text())


def test_run_id_shape():
    run_id = new_run_id()
    parts = run_id.split("-")
    assert len(parts) == 3 and len(parts[2]) == 6
    assert new_run_id() != run_id


def test_successful_run_writes_all_artifacts(project):
    project.write_tests([passing_test("P-001"), passing_test("P-002", tags=["visual"])])
    project.write_config(ci={"suites": {"pr": {"tags": ["smoke"]}}})
    outcome = _runner(project).run(CIRunRequest(suite="pr"))
    assert outcome.exit_code == ExitCode.SUCCESS
    result = outcome.result
    assert result.status == CIRunStatus.PASSED
    assert result.provider == "local"
    assert [t.test_id for t in result.tests] == ["P-001"]
    assert result.tests[0].outcome == TestOutcome.PASSED
    out = project.output
    for name in (
        "report.json",
        "junit.xml",
        "report.html",
        "metadata/ci.json",
        "metadata/environment.json",
        "metadata/preflight.json",
        "logs/argus/argus.log",
    ):
        assert (out / name).exists(), name
    report = _report(project)
    assert report["schema_version"] == 1
    assert report["summary"]["status"] == "passed"
    assert report["run"]["suite"] == "pr"
    assert report["run"]["timings"]["total"] > 0
    assert {a["path"] for a in report["artifacts"]} >= {
        "junit.xml",
        "report.html",
        "metadata/ci.json",
    }
    assert "P-001" in (out / "report.html").read_text()


def test_failure_classification_reports_and_exit_code(project):
    project.write_tests(
        [passing_test("P-001"), visual_failing_test("V-001"), assertion_failing_test("A-001")]
    )
    project.write_config()
    outcome = _runner(project).run(CIRunRequest())
    assert outcome.exit_code == ExitCode.TEST_FAILURE
    by_id = {t.test_id: t for t in outcome.result.tests}
    assert by_id["V-001"].outcome == TestOutcome.FAILED
    assert by_id["V-001"].failure_category.value == "visual_regression"
    assert by_id["A-001"].failure_category.value == "assertion_failure"
    assert by_id["P-001"].outcome == TestOutcome.PASSED  # fail-fast is off by default
    # Evidence for the visual failure lives under the CI output directory.
    assert any(p.startswith("tests/V-001_android/") for p in by_id["V-001"].artifacts)
    assert (project.output / "tests" / "V-001_android" / "actual.png").exists()
    assert (project.output / "tests" / "V-001_android" / "diff.png").exists()
    # JUnit: failure element + CI properties.
    root = ET.parse(project.output / "junit.xml").getroot()
    cases = {c.get("name"): c for c in root.iter("testcase")}
    visual = next(c for n, c in cases.items() if n.startswith("V-001"))
    assert visual.find("failure") is not None
    props = {p.get("name"): p.get("value") for p in visual.iter("property")}
    assert props["failure_category"] == "visual_regression" and props["outcome"] == "failed"
    policy = outcome.result.policy
    assert policy.failed and policy.failing_rules == {"failures", "visual_regression"}
    html = (project.output / "report.html").read_text()
    assert "visual regression" in html and "policy visual_regression" in html


def test_fail_fast_stops_and_marks_not_run(project):
    project.write_tests(
        [assertion_failing_test("A-001"), passing_test("P-001"), passing_test("P-002")]
    )
    project.write_config()
    outcome = _runner(project).run(CIRunRequest(fail_fast=True))
    assert outcome.exit_code == ExitCode.TEST_FAILURE
    outcomes = [t.outcome for t in outcome.result.tests]
    assert outcomes == [TestOutcome.FAILED, TestOutcome.NOT_RUN, TestOutcome.NOT_RUN]
    assert outcome.result.not_run_count == 2


def test_policy_warn_allows_failures_through(project):
    project.write_tests([assertion_failing_test("A-001")])
    project.write_config(ci={"policy": {"failures": {"action": "warn"}}})
    outcome = _runner(project).run(CIRunRequest())
    assert outcome.exit_code == ExitCode.SUCCESS
    assert outcome.result.status == CIRunStatus.FAILED
    assert outcome.result.policy.status == "warned"


def test_policy_failure_independent_of_raw_results(project):
    project.write_tests([passing_test("P-001")])
    project.write_config(
        devices={
            "fake_android": project.device("fake_android", "android", fail_first_screenshots=1)
        },
        ci={"retry": {"enabled": True, "max_attempts": 2}, "policy": {"flaky": {"action": "fail"}}},
    )
    outcome = _runner(project).run(CIRunRequest(skip_preflight=True))
    assert outcome.result.tests[0].outcome == TestOutcome.FLAKY_PASSED
    assert outcome.result.flaky_count == 1
    assert outcome.result.policy.failing_rules == {"flaky"}
    assert outcome.exit_code == ExitCode.POLICY_FAILURE


def test_retry_produces_flaky_pass_with_attempt_history(project):
    project.write_tests([passing_test("P-001")])
    project.write_config(
        devices={
            "fake_android": project.device("fake_android", "android", fail_first_screenshots=1)
        },
        ci={"retry": {"enabled": True, "max_attempts": 2, "on": ["screenshot_capture_error"]}},
    )
    # Preflight's screenshot probe would consume the injected transient failure.
    outcome = _runner(project).run(CIRunRequest(skip_preflight=True))
    assert outcome.exit_code == ExitCode.SUCCESS
    test = outcome.result.tests[0]
    assert test.flaky and test.attempts == 2 and test.initial_failure == "screenshot"
    report = _report(project)
    entry = report["tests"][0]
    assert entry["flaky"] is True and entry["outcome"] == "flaky_passed"
    assert [a["attempt"] for a in entry["attempt_history"]] == [1, 2]
    assert report["summary"]["flaky"] == 1
    assert outcome.result.policy.status == "warned"
    # Attempt-1 evidence survives the retry.
    assert (project.output / "tests" / "P-001_android" / "actual.png").exists()


def test_retry_disabled_by_default_and_cli_override(project):
    project.write_tests([passing_test("P-001")])
    project.write_config(
        devices={
            "fake_android": project.device("fake_android", "android", fail_first_screenshots=1)
        }
    )
    outcome = _runner(project).run(CIRunRequest(skip_preflight=True))
    assert outcome.result.tests[0].outcome == TestOutcome.FAILED
    outcome = _runner(project).run(CIRunRequest(retry=2, skip_preflight=True))
    assert outcome.result.tests[0].outcome == TestOutcome.FLAKY_PASSED


def test_known_failure_is_marked_and_warns(project):
    project.write_tests([assertion_failing_test("A-001")])
    project.write_config(ci={"known_failures": [{"test": "A-001", "reason": "tracked in ARGUS-1"}]})
    outcome = _runner(project).run(CIRunRequest())
    assert outcome.exit_code == ExitCode.SUCCESS
    test = outcome.result.tests[0]
    assert test.outcome == TestOutcome.KNOWN_FAILURE
    assert test.known_failure_reason == "tracked in ARGUS-1"
    assert outcome.result.policy.status == "warned"
    report = _report(project)
    assert report["tests"][0]["outcome"] == "known_failure"
    assert "known failure" in (project.output / "report.html").read_text()


def test_required_suite_failure_is_policy_failure(project):
    project.write_tests([passing_test("P-001"), assertion_failing_test("A-001", tags=["smoke"])])
    project.write_config(
        ci={
            "suites": {"smoke": {"tags": ["smoke"]}},
            "policy": {"required": ["smoke"], "failures": {"action": "warn"}},
        }
    )
    outcome = _runner(project).run(CIRunRequest())
    assert outcome.result.policy.failing_rules == {"required"}
    assert outcome.exit_code == ExitCode.POLICY_FAILURE


def test_unknown_suite_is_configuration_error(project):
    project.write_tests([passing_test()])
    project.write_config()
    outcome = _runner(project).run(CIRunRequest(suite="nightly"))
    assert outcome.exit_code == ExitCode.CONFIGURATION_ERROR
    assert outcome.result.status == CIRunStatus.ERROR
    assert "Unknown suite 'nightly'" in outcome.result.error


def test_no_matching_tests_is_configuration_error(project):
    project.write_tests([passing_test()])
    project.write_config()
    outcome = _runner(project).run(CIRunRequest(filters=build_filter(tags=["nonexistent"])))
    assert outcome.exit_code == ExitCode.CONFIGURATION_ERROR
    assert outcome.result.status == CIRunStatus.NOT_RUN
    assert (project.output / "report.json").exists()


def test_invalid_test_definition_exit_code(project):
    (project.suites / "bad.yaml").write_text("tests:\n  - id: BAD\n    name: x\n")
    project.write_config()
    outcome = _runner(project).run(CIRunRequest())
    assert outcome.exit_code == ExitCode.TEST_DEFINITION_ERROR
    assert outcome.result.error_category.value == "test_definition_error"


def test_environment_failure_preserves_metadata_and_marks_not_run(project):
    project.write_tests(
        [
            passing_test(
                "P-001",
                steps=[
                    {
                        "action": "verify",
                        "condition": {"type": "image_present", "image": "missing.png"},
                    },
                ],
            )
        ]
    )
    project.write_config(
        ci={"suites": {"smoke": {"tags": ["smoke"]}}, "policy": {"required": ["smoke"]}}
    )
    outcome = _runner(project).run(CIRunRequest())
    assert outcome.exit_code == ExitCode.ENVIRONMENT_ERROR
    result = outcome.result
    assert result.status == CIRunStatus.ERROR
    assert result.tests[0].outcome == TestOutcome.NOT_RUN
    assert result.policy.failing_rules == {"required"}
    assert (project.output / "metadata" / "preflight.json").exists()
    assert (project.output / "metadata" / "ci.json").exists()
    report = _report(project)
    assert report["summary"]["not_run"] == 1 and report["summary"]["status"] == "error"
    assert any(not p["passed"] for p in report["preflight"])


def test_cancellation_before_execution(project):
    project.write_tests([passing_test("P-001"), passing_test("P-002")])
    project.write_config()
    cancel = threading.Event()
    cancel.set()
    outcome = _runner(project, cancel=cancel).run(CIRunRequest())
    assert outcome.exit_code == ExitCode.CANCELLED
    assert outcome.result.status == CIRunStatus.CANCELLED
    assert all(t.outcome == TestOutcome.NOT_RUN for t in outcome.result.tests)
    assert _report(project)["run"]["status"] == "cancelled"


def test_cancellation_during_execution(project):
    project.write_tests([passing_test(f"P-00{i}") for i in range(1, 5)])
    project.write_config()
    cancel = threading.Event()
    runner = _runner(project, cancel=cancel)
    original = runner._run_workers

    def cancelling(plan, request):
        # Simulate SIGTERM arriving while the first test runs.
        timer = threading.Timer(0.05, cancel.set)
        timer.start()
        try:
            return original(plan, request)
        finally:
            timer.cancel()

    runner._run_workers = cancelling
    outcome = runner.run(CIRunRequest())
    assert outcome.exit_code == ExitCode.CANCELLED
    assert outcome.result.status == CIRunStatus.CANCELLED
    assert outcome.result.not_run_count >= 1
    assert outcome.result.passed_count + outcome.result.not_run_count == 4


def test_parallel_workers_partition_devices(project):
    project.write_tests(
        [
            passing_test("P-001", feature="A", platforms=["android", "yocto"]),
            passing_test("P-002", feature="B", platforms=["android", "yocto"]),
            visual_failing_test("V-001", feature="C", platforms=["yocto"]),
        ]
    )
    project.write_config(
        devices={
            "fake_android": project.device("fake_android", "android"),
            "fake_yocto": project.device("fake_yocto", "yocto"),
        },
        ci={"execution": {"workers": 2, "strategy": "balanced"}},
    )
    outcome = _runner(project).run(CIRunRequest())
    result = outcome.result
    assert result.workers == 2 and result.strategy == "balanced"
    assert outcome.exit_code == ExitCode.TEST_FAILURE
    assert len(result.tests) == 5
    workers = {
        t.platform: {t.worker for t in result.tests if t.platform == t.platform}
        for t in result.tests
    }
    android_workers = {t.worker for t in result.tests if t.platform == "android"}
    yocto_workers = {t.worker for t in result.tests if t.platform == "yocto"}
    assert len(android_workers) == 1 and len(yocto_workers) == 1
    assert android_workers != yocto_workers
    assert workers  # sanity
    # Per-test artifact directories are unique across workers.
    dirs = [t.artifact_dir for t in result.tests if t.artifact_dir]
    assert len(dirs) == len(set(dirs))
    assert (project.output / "tests" / "V-001_yocto" / "actual.png").exists()


def test_github_publishing_and_no_secret_leakage(project, tmp_path):
    summary = tmp_path / "summary.md"
    summary.touch()
    project.write_tests([passing_test("P-001"), visual_failing_test("V-001")])
    project.write_config()
    env = {**GITHUB_ENV, "GITHUB_STEP_SUMMARY": str(summary)}
    stream = io.StringIO()
    from argus.ci.reporters import GitHubReporter, ReporterRegistry
    from argus.ci.reporters.generic import GenericReporter

    reporters = ReporterRegistry(GenericReporter())
    reporters.register("github", GitHubReporter(stream))
    outcome = _runner(project, env=env, reporters=reporters).run(CIRunRequest())
    assert outcome.result.provider == "github"
    assert outcome.result.context.pull_request == "7"
    text = summary.read_text()
    assert "# Argus Test Results" in text and "V-001" in text and "PR: #7" in text
    assert "::error title=Argus test failed%3A V-001 [android]::" in stream.getvalue()
    # No secret anywhere in the artifacts.
    for path in project.output.rglob("*"):
        if path.is_file():
            assert "ghs_supersecret" not in path.read_text(errors="ignore"), path
    assert "ghs_supersecret" not in text


def test_no_report_and_no_artifacts(project):
    project.write_tests([passing_test("P-001")])
    project.write_config()
    outcome = _runner(project).run(CIRunRequest(publish=False, artifacts=False))
    assert outcome.exit_code == ExitCode.SUCCESS
    assert outcome.result.artifacts_dir is None
    assert not project.output.exists()


def test_dry_run_executes_nothing(project):
    project.write_tests([passing_test("P-001"), passing_test("P-002", tags=["other"])])
    project.write_config(ci={"suites": {"pr": {"tags": ["smoke"]}}, "retry": {"enabled": True}})
    runner = _runner(project)
    outcome = runner.dry_run(CIRunRequest(suite="pr", dry_run=True))
    assert outcome.exit_code == ExitCode.SUCCESS
    assert outcome.result.status == CIRunStatus.NOT_RUN
    text = runner.console.file.getvalue()
    assert "Argus CI Dry Run" in text and "P-001" in text and "P-002" not in text
    assert "No tests were executed." in text
    assert "Retries:   2 attempt" in text
    assert not project.output.exists()
    assert not (project.root / "results").exists()


def test_previous_results_are_cleaned_but_foreign_files_kept(project):
    project.write_tests([passing_test("P-001")])
    project.write_config()
    project.output.mkdir()
    (project.output / "report.json").write_text("stale")
    (project.output / "notes.txt").write_text("mine")
    _runner(project).run(CIRunRequest())
    assert "stale" not in (project.output / "report.json").read_text()
    assert (project.output / "notes.txt").read_text() == "mine"


def test_artifact_directory_outside_project_is_rejected(project):
    project.write_tests([passing_test("P-001")])
    project.write_config(ci={"artifacts": {"directory": str(Path(project.root).parent)}})
    outcome = _runner(project).run(CIRunRequest())
    assert outcome.exit_code == ExitCode.CONFIGURATION_ERROR
