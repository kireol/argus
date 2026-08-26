"""GitHub Actions reporter: job summary + workflow annotations.

Uses only GitHub's environment mechanisms (``GITHUB_STEP_SUMMARY`` and
workflow commands on stdout). No API access, no tokens. Checks / PR comments
are a documented extension point (``ProviderCapabilities.supports_checks``).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import TextIO

from argus.ci.artifacts import CIArtifactLayout
from argus.ci.reporters.base import CIReporter
from argus.ci.result import CIRunResult, CIRunStatus, CITestResult, TestOutcome


def _escape_property(value: str) -> str:
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _label(test: CITestResult) -> str:
    return test.test_id if test.platform is None else f"{test.test_id} [{test.platform}]"


def render_job_summary(result: CIRunResult) -> str:
    """GitHub-flavoured Markdown for the job summary."""
    icon = {
        CIRunStatus.PASSED: "✅",
        CIRunStatus.FAILED: "❌",
        CIRunStatus.ERROR: "💥",
        CIRunStatus.CANCELLED: "⚠️",
        CIRunStatus.NOT_RUN: "⚠️",
    }[result.status]
    headline_failed = result.failed_count + result.errored_count
    lines = ["# Argus Test Results", ""]
    if result.status == CIRunStatus.PASSED:
        lines.append(f"{icon} {result.passed_count} passed / {result.total} tests")
    elif result.status in (CIRunStatus.FAILED,):
        lines.append(f"{icon} {headline_failed} failed / {result.total} tests")
    else:
        detail = f" — {result.error}" if result.error else ""
        lines.append(f"{icon} {result.status.value.replace('_', ' ')}{detail}")
    if result.policy.status != "passed":
        lines.append(f"Policy: **{result.policy.status}**")
    lines += [
        "",
        "| Status | Count |",
        "|---|---:|",
        f"| Passed | {result.passed_count} |",
        f"| Failed | {result.failed_count} |",
        f"| Errored | {result.errored_count} |",
        f"| Skipped | {result.skipped_count} |",
        f"| Not run | {result.not_run_count} |",
        f"| Flaky | {result.flaky_count} |",
        f"| Known failures | {result.known_failure_count} |",
    ]
    failed = [t for t in result.tests if t.outcome in (TestOutcome.FAILED, TestOutcome.ERROR)]
    if failed:
        lines += ["", "## Failed Tests", ""]
        lines += [
            f"- {_label(t)} — {t.failure_category.value if t.failure_category else 'failed'}"
            for t in failed[:50]
        ]
        if len(failed) > 50:
            lines.append(f"- … and {len(failed) - 50} more")
    regressions = result.visual_regressions
    if regressions:
        lines += ["", "## Visual Regressions", ""]
        lines += [f"- {_label(t)}" for t in regressions[:50]]
    known = [t for t in result.tests if t.outcome == TestOutcome.KNOWN_FAILURE]
    if known:
        lines += ["", "## Known Failures", ""]
        lines += [
            f"- {_label(t)} — {t.known_failure_reason or 'known failure'}" for t in known[:50]
        ]
    flaky = [t for t in result.tests if t.flaky]
    if flaky:
        lines += ["", "## Flaky Tests", ""]
        lines += [
            f"- {_label(t)} — passed on attempt {t.attempts} after {t.initial_failure}"
            for t in flaky[:50]
        ]
    if result.policy.violations:
        lines += ["", "## Policy", ""]
        lines += [f"- **{v.action}** `{v.rule}`: {v.message}" for v in result.policy.violations]
    ctx = result.context
    lines += ["", "## Environment", ""]
    platforms = sorted({t.platform for t in result.tests if t.platform})
    if platforms:
        lines.append(f"- Platforms: {', '.join(platforms)}")
    if result.suite:
        lines.append(f"- Suite: {result.suite}")
    if ctx.branch:
        lines.append(f"- Branch: {ctx.branch}")
    if ctx.short_commit:
        lines.append(f"- Commit: {ctx.short_commit}")
    if ctx.pull_request:
        lines.append(f"- PR: #{ctx.pull_request}")
    lines.append(f"- Workers: {result.workers} · Retry: {result.retry.max_attempts} attempt(s)")
    lines.append(f"- Run ID: {result.run_id}")
    return "\n".join(lines) + "\n"


def render_annotations(result: CIRunResult, limit: int) -> list[str]:
    """Workflow commands for failed tests (bounded; one per failing test)."""
    failed = [t for t in result.tests if t.outcome in (TestOutcome.FAILED, TestOutcome.ERROR)]
    commands: list[str] = []
    for test in failed[:limit]:
        message = f"{test.name}: {test.failure_message or 'failed'}"
        commands.append(
            f"::error title={_escape_property('Argus test failed: ' + _label(test))}::"
            f"{_escape_data(message)}"
        )
    if len(failed) > limit:
        commands.append(
            f"::warning title={_escape_property('Argus annotations truncated')}::"
            f"{len(failed) - limit} more failed test(s) not annotated; see the job summary."
        )
    for violation in result.policy.violations:
        level = "error" if violation.action == "fail" else "warning"
        commands.append(
            f"::{level} title={_escape_property('Argus policy: ' + violation.rule)}::"
            f"{_escape_data(violation.message)}"
        )
    if result.status in (CIRunStatus.ERROR, CIRunStatus.CANCELLED, CIRunStatus.NOT_RUN):
        commands.append(
            f"::error title={_escape_property('Argus run ' + result.status.value)}::"
            f"{_escape_data(result.error or result.status.value)}"
        )
    return commands


class GitHubReporter(CIReporter):
    name = "github"

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def publish(
        self,
        result: CIRunResult,
        layout: CIArtifactLayout | None,
        environment: Mapping[str, str],
        *,
        summary: bool = True,
        annotations: bool = True,
        max_annotations: int = 20,
    ) -> list[str]:
        notes: list[str] = []
        if summary:
            target = environment.get("GITHUB_STEP_SUMMARY")
            if target:
                with open(target, "a", encoding="utf-8") as fh:
                    fh.write(render_job_summary(result))
                notes.append("GitHub job summary written")
            else:
                notes.append("GITHUB_STEP_SUMMARY not set; job summary skipped")
        if annotations:
            stream = self._stream or sys.stdout
            commands = render_annotations(result, max_annotations)
            for command in commands:
                stream.write(command + "\n")
            stream.flush()
            if commands:
                notes.append(f"{len(commands)} GitHub annotation(s) emitted")
        return notes
