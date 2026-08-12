"""Human-readable console reporter (event-driven)."""

from __future__ import annotations

from rich.console import Console

from utf.events.bus import EventBus
from utf.events.events import (
    Event,
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
from utf.models.results import RunStatus, TestResult
from utf.utilities.duration import format_duration

_RULE = "─" * 40


class ConsoleReporter:
    """Subscribes to the event bus and renders progress for humans."""

    def __init__(self, console: Console | None = None, *, quiet: bool = False) -> None:
        self.console = console or Console(highlight=False)
        self.quiet = quiet
        self._current_feature: str | None = None

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(self._on_event)

    # -- dispatch -------------------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        handler = getattr(self, f"_on_{type(event).__name__}", None)
        if handler is not None:
            handler(event)

    # -- run ---------------------------------------------------------------------------

    def _on_TestRunStarted(self, event: TestRunStarted) -> None:  # noqa: N802
        if self.quiet:
            return
        self.console.print(f"Found [bold]{event.total_tests}[/bold] tests.")
        if event.filters:
            filters = ", ".join(f"{k}={v}" for k, v in event.filters.items())
            self.console.print(f"Filters: {filters}")

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

        self.console.print(f"\n[bold red]PRE-FLIGHT FAILED[/bold red]\n")
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

    def _on_TestStarted(self, event: TestStarted) -> None:  # noqa: N802
        if event.feature != self._current_feature:
            self._current_feature = event.feature
            if not self.quiet:
                self.console.print(f"\n[bold]{event.feature}[/bold]\n")

    def _test_line(self, result: TestResult, symbol: str, style: str) -> str:
        platform = f" ({result.platform})" if result.platform else ""
        return (
            f"[{style}]{symbol}[/{style}] {result.test_id:<10} "
            f"{result.name:<40}{platform}  {format_duration(result.duration)}"
        )

    def _on_TestPassed(self, event: TestPassed) -> None:  # noqa: N802
        if not self.quiet:
            self.console.print(self._test_line(event.result, "✓", "green"))

    def _on_TestSkipped(self, event: TestSkipped) -> None:  # noqa: N802
        if not self.quiet:
            self.console.print(self._test_line(event.result, "-", "dim"))

    def _on_TestFailed(self, event: TestFailed) -> None:  # noqa: N802
        result = event.result
        self.console.print(self._test_line(result, "✗", "red"))
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
        self.console.print()
        if result.stopped_early:
            self.console.print(f"[bold yellow]TEST RUN STOPPED[/bold yellow] ({result.stop_reason})")
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
