"""Provider reporters (mocked environments; no API access)."""

import io

from argus.ci.categories import FailureCategory
from argus.ci.context import CIContext
from argus.ci.reporters import GenericReporter, GitHubReporter, default_reporter_registry
from argus.ci.reporters.github import render_annotations, render_job_summary
from argus.ci.result import (
    CIRunResult,
    CIRunStatus,
    CITestResult,
    PolicyResult,
    PolicyViolation,
    TestOutcome,
)
from argus.models.results import TestStatus


def _result(status=CIRunStatus.FAILED):
    tests = [
        CITestResult(
            test_id="OK-1",
            name="ok",
            feature="F",
            status=TestStatus.PASSED,
            outcome=TestOutcome.PASSED,
        ),
        CITestResult(
            test_id="FL-1",
            name="flaky",
            feature="F",
            status=TestStatus.PASSED,
            outcome=TestOutcome.FLAKY_PASSED,
            flaky=True,
            attempts=2,
            initial_failure="timeout",
        ),
        CITestResult(
            test_id="VR-1",
            name="player-controls",
            feature="F",
            platform="android",
            status=TestStatus.FAILED,
            outcome=TestOutcome.FAILED,
            failure_category=FailureCategory.VISUAL_REGRESSION,
            failure_message="image not found\nline two: 100%",
        ),
        CITestResult(
            test_id="KN-1",
            name="known",
            feature="F",
            status=TestStatus.FAILED,
            outcome=TestOutcome.KNOWN_FAILURE,
            known_failure_reason="ARGUS-1",
        ),
    ]
    return CIRunResult(
        run_id="20260826-000000-abc123",
        status=status,
        provider="github",
        suite="pr",
        tests=tests,
        policy=PolicyResult(
            status="failed",
            violations=[
                PolicyViolation(
                    rule="visual_regression", action="fail", message="1 visual regression(s)"
                )
            ],
        ),
        context=CIContext(
            provider="github",
            display_name="GitHub Actions",
            branch="main",
            commit_sha="abcdef0123",
            pull_request="42",
        ),
    )


def test_job_summary_contents():
    text = render_job_summary(_result())
    assert text.startswith("# Argus Test Results")
    assert "❌ 1 failed / 4 tests" in text
    assert "| Passed | 2 |" in text and "| Flaky | 1 |" in text
    assert "## Failed Tests" in text and "VR-1 [android]" in text
    assert "## Visual Regressions" in text
    assert "## Known Failures" in text and "ARGUS-1" in text
    assert "## Flaky Tests" in text and "after timeout" in text
    assert "## Policy" in text and "visual_regression" in text
    assert "- Branch: main" in text and "- Commit: abcdef0" in text and "- PR: #42" in text


def test_annotations_are_escaped_and_bounded():
    commands = render_annotations(_result(), limit=20)
    error = commands[0]
    assert error.startswith("::error title=Argus test failed%3A VR-1 [android]::")
    assert "%0A" in error and "%25" in error  # newline and percent escaped
    assert any(c.startswith("::error title=Argus policy%3A visual_regression::") for c in commands)
    limited = render_annotations(_result(), limit=0)
    assert limited[0].startswith("::warning title=Argus annotations truncated::")


def test_github_reporter_writes_summary_and_annotations(tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("previous step\n")
    stream = io.StringIO()
    notes = GitHubReporter(stream).publish(
        _result(), None, {"GITHUB_STEP_SUMMARY": str(summary)}, max_annotations=5
    )
    assert "previous step" in summary.read_text()  # appended, not overwritten
    assert "# Argus Test Results" in summary.read_text()
    assert "::error" in stream.getvalue()
    assert any("job summary written" in n for n in notes)


def test_github_reporter_without_summary_file_degrades():
    stream = io.StringIO()
    notes = GitHubReporter(stream).publish(_result(CIRunStatus.PASSED), None, {}, annotations=False)
    assert any("skipped" in n for n in notes)
    assert stream.getvalue() == ""


def test_registry_falls_back_to_generic():
    registry = default_reporter_registry()
    assert isinstance(registry.for_provider("github"), GitHubReporter)
    assert isinstance(registry.for_provider("jenkins"), GenericReporter)
    assert isinstance(registry.for_provider("local"), GenericReporter)
