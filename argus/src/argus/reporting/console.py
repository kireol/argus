"""Human-readable console reporter (event-driven)."""

from __future__ import annotations

import logging

from rich.console import Console

from argus.events.bus import EventBus
from argus.events.events import (
    Event,
    FeatureSetupCompleted,
    FeatureSetupStarted,
    FeatureTeardownCompleted,
    FeatureTeardownStarted,
    PreflightCheckCompleted,
    PreflightCompleted,
    PreflightStarted,
    TestFailed,
    TestPassed,
    TestRunCompleted,
    TestRunStarted,
    TestSkipped,
    TestStarted,
)
from argus.models.results import RunStatus, TestResult
from argus.utilities.duration import format_duration

_RULE = "─" * 40


class ConsoleReporter:
    """Subscribes to the event bus and renders progress for humans."""

    def __init__(self, console: Console | None = None, *, quiet: bool = False) -> None:
        self.console = console or Console(highlight=False)
        self.quiet = quiet
        self._current_feature: str | None = None
        self._total_tests: int = 0
        self._test_index: int = 0
        self._current_progress: str | None = None
        # When True, the next status/result line replaces the previous test line.
        self._replaceable: bool = False

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(self._on_event)

    # -- dispatch -------------------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        handler = getattr(self, f"_on_{type(event).__name__}", None)
        if handler is not None:
            handler(event)

    # -- run ---------------------------------------------------------------------------

    def _on_TestRunStarted(self, event: TestRunStarted) -> None:  # noqa: N802
        self._total_tests = event.total_tests
        # Preserve full-suite i/N numbering when resuming via --skip-to.
        self._test_index = max(0, event.start_index - 1)
        self._current_progress = None
        self._replaceable = False
        if self.quiet:
            return
        self.console.print(f"Found [bold]{event.total_tests}[/bold] tests.")
        if event.filters:
            filters = ", ".join(f"{k}={v}" for k, v in event.filters.items())
            self.console.print(f"Filters: {filters}")
        if event.start_index > 1:
            self.console.print(
                f"Starting at test [bold]{event.start_index}/{event.total_tests}[/bold]."
            )

    def _on_PreflightStarted(self, event: PreflightStarted) -> None:  # noqa: N802
        if self.quiet:
            return
        self.console.print(f"\n[bold]PRE-FLIGHT[/bold]\n{_RULE}\n")

    def _on_PreflightCheckCompleted(self, event: PreflightCheckCompleted) -> None:  # noqa: N802
        result = event.result
        if result.passed:
            if not self.quiet:
                self.console.print(f"[green]✓[/green] {result.name}")
        elif not result.required:
            self.console.print(f"[yellow]⚠[/yellow] {result.name} — {result.error}")
        else:
            self.console.print(f"[red]✗[/red] {result.name}")

    def _on_PreflightCompleted(self, event: PreflightCompleted) -> None:  # noqa: N802
        passed = len([r for r in event.results if r.passed])
        failed = [r for r in event.results if not r.passed and r.required]
        if event.passed:
            if not self.quiet:
                self.console.print(f"\nPre-flight complete: {passed} passed\n")
            return

        self.console.print("\n[bold red]PRE-FLIGHT FAILED[/bold red]\n")
        for result in failed:
            self.console.print(f"[red]✗ {result.name}[/red]\n")
            if result.target:
                self.console.print(f"Target:\n    {result.target}\n")
            self.console.print(f"Problem:\n    {result.error}\n")
            if result.causes:
                self.console.print("Possible causes:")
                for cause in result.causes:
                    self.console.print(f"    - {cause}")
                self.console.print()
            if result.remediation:
                self.console.print(f"Remediation:\n    {result.remediation}\n")
        self.console.print("[bold]Functional tests were NOT executed.[/bold]")

    # -- tests --------------------------------------------------------------------------

    def _can_overwrite(self) -> bool:
        """In-place updates need a TTY and no INFO lines between start and finish."""
        if not self._replaceable or not self.console.is_terminal:
            return False
        # Timestamped step logs (stderr) would sit between → and ✓ and break
        # cursor-up overwrite; that mode is for --no-logs / WARNING+.
        return logging.getLogger("argus").level >= logging.WARNING

    def _emit_test_line(self, markup: str, *, replace: bool = False) -> None:
        """Print a test status line, optionally overwriting the previous one."""
        if replace and self._can_overwrite():
            # Move to previous line and clear it, then rewrite in place.
            self.console.file.write("\r\x1b[1A\x1b[2K")
            self.console.file.flush()
        self.console.print(markup)

    def _feature_header(self, feature: str) -> None:
        if feature != self._current_feature:
            self._current_feature = feature
            self._replaceable = False
            if not self.quiet:
                self.console.print(f"\n[bold]{feature}[/bold]\n")

    def _feature_phase_line(
        self, phase: str, platform: str | None, passed: bool, error: str | None
    ) -> None:
        self._replaceable = False
        if self.quiet and passed:
            return
        target = f" ({platform})" if platform else ""
        mark, colour = ("✓", "green") if passed else ("✗", "red")
        self.console.print(f"  [{colour}]{mark}[/{colour}] {phase}{target}")
        if error:
            self.console.print(f"    {error}")

    def _on_FeatureSetupStarted(self, event: FeatureSetupStarted) -> None:  # noqa: N802
        self._feature_header(event.feature)

    def _on_FeatureSetupCompleted(self, event: FeatureSetupCompleted) -> None:  # noqa: N802
        self._feature_phase_line("setup", event.platform, event.passed, event.error)

    def _on_FeatureTeardownStarted(self, event: FeatureTeardownStarted) -> None:  # noqa: N802
        self._feature_header(event.feature)

    def _on_FeatureTeardownCompleted(self, event: FeatureTeardownCompleted) -> None:  # noqa: N802
        self._feature_phase_line("teardown", event.platform, event.passed, event.error)

    def _on_TestStarted(self, event: TestStarted) -> None:  # noqa: N802
        self._feature_header(event.feature)
        progress = self._assign_progress()
        if self.quiet:
            return
        platform = f" ({event.platform})" if event.platform else ""
        line = (
            f"[cyan]→[/cyan] {progress} - {event.test_id:<10} "
            f"{event.name:<40}{platform}"
        )
        # Retries refresh the same running line instead of stacking another →.
        self._emit_test_line(line, replace=self._replaceable)
        self._replaceable = True

    def _assign_progress(self) -> str:
        """Assign (or reuse) a 1-based ``i/N`` label for the in-flight test.

        Reuses the existing label on retries so a second ``TestStarted`` does
        not advance the counter before completion.
        """
        if self._current_progress is None:
            self._test_index += 1
            if self._total_tests > 0:
                self._current_progress = f"{self._test_index}/{self._total_tests}"
            else:
                self._current_progress = str(self._test_index)
        return self._current_progress

    def _consume_progress(self) -> str:
        """Return progress for the completing test; assign if start was skipped."""
        label = self._assign_progress()
        self._current_progress = None
        return label

    def _test_line(self, result: TestResult, symbol: str, style: str) -> str:
        platform = f" ({result.platform})" if result.platform else ""
        progress = self._consume_progress()
        return (
            f"[{style}]{symbol}[/{style}] {progress} - {result.test_id:<10} "
            f"{result.name:<40}{platform}  {format_duration(result.duration)}"
        )

    def _finish_test_line(self, markup: str) -> None:
        self._emit_test_line(markup, replace=True)
        self._replaceable = False

    def _on_TestPassed(self, event: TestPassed) -> None:  # noqa: N802
        line = self._test_line(event.result, "✓", "green")
        if not self.quiet:
            self._finish_test_line(line)

    def _on_TestSkipped(self, event: TestSkipped) -> None:  # noqa: N802
        line = self._test_line(event.result, "-", "dim")
        if not self.quiet:
            self._finish_test_line(line)

    def _on_TestFailed(self, event: TestFailed) -> None:  # noqa: N802
        result = event.result
        self._finish_test_line(self._test_line(result, "✗", "red"))
        failed_step = next((s for s in result.steps if not s.passed), None)
        if failed_step is not None:
            self.console.print(f"\n    Failed step: {failed_step.action}")
            self.console.print(f"    {failed_step.message}")
            verification = failed_step.verification
            if verification is not None:
                if verification.confidence is not None:
                    self.console.print(f"    Confidence: {verification.confidence:.3f}")
                if verification.location is not None:
                    loc = verification.location
                    self.console.print(
                        f"    Location: x={loc.x} y={loc.y} {loc.width}x{loc.height}"
                    )
        if result.instrumentation_state:
            self.console.print("\n    Instrumentation:")
            for key, value in sorted(result.instrumentation_state.items()):
                if key != "capabilities":
                    self.console.print(f"      {key}: {value}")
        if result.artifact_dir:
            self.console.print(f"\n    Artifacts:\n      {result.artifact_dir}\n")

    # -- summary ------------------------------------------------------------------------------

    def _on_TestRunCompleted(self, event: TestRunCompleted) -> None:  # noqa: N802
        result = event.result
        if result.status == RunStatus.PREFLIGHT_FAILED:
            return
        if result.status == RunStatus.SETUP_FAILED:
            self.console.print()
            self.console.print("[bold red]SETUP FAILED[/bold red]")
            if result.stop_reason:
                self.console.print(result.stop_reason)
            return
        self.console.print()
        if result.status == RunStatus.CANCELLED:
            self.console.print(
                f"[bold yellow]TEST RUN CANCELLED[/bold yellow] ({result.stop_reason})"
            )
        elif result.stopped_early:
            self.console.print(
                f"[bold yellow]TEST RUN STOPPED[/bold yellow] ({result.stop_reason})"
            )
        elif result.status == RunStatus.PASSED:
            self.console.print("[bold green]TEST RUN PASSED[/bold green]")
        else:
            self.console.print("[bold red]TEST RUN FAILED[/bold red]")
        self.console.print()
        self.console.print(f"Executed: {result.executed}")
        self.console.print(f"Passed:   {result.passed_count}")
        self.console.print(f"Failed:   {result.failed_count}")
        if result.skipped_count:
            self.console.print(f"Skipped:  {result.skipped_count}")
        self.console.print(f"Duration: {format_duration(result.duration)}")
        if result.results_dir:
            self.console.print(f"Results:  {result.results_dir}")
