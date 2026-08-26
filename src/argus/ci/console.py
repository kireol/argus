"""Plain, thread-safe console output for CI runs (no cursor control).

Subscribes to each worker's event bus and prints one line per test.
Readable without a TTY; colors follow Rich's own terminal/NO_COLOR detection.
"""

from __future__ import annotations

import threading

from rich.console import Console
from rich.markup import escape

from argus.events.bus import EventBus
from argus.events.events import (
    Event,
    FeatureSetupCompleted,
    FeatureTeardownCompleted,
    TestFailed,
    TestPassed,
    TestSkipped,
    TestStarted,
)
from argus.utilities.duration import format_duration


class CIConsoleReporter:
    def __init__(self, console: Console, *, total: int, workers: int, quiet: bool = False) -> None:
        self.console = console
        self.total = total
        self.workers = workers
        self.quiet = quiet
        self._lock = threading.Lock()
        self._done = 0
        # (test_id, platform) -> attempt number seen so far
        self._attempts: dict[tuple[str, str | None], int] = {}

    def attach(self, bus: EventBus, worker: int) -> None:
        bus.subscribe(lambda event: self._on_event(event, worker))

    def _prefix(self, worker: int) -> str:
        return f"[dim]w{worker}[/dim] " if self.workers > 1 else ""

    def _on_event(self, event: Event, worker: int) -> None:
        if isinstance(event, TestStarted):
            key = (event.test_id, event.platform)
            with self._lock:
                self._attempts[key] = self._attempts.get(key, 0) + 1
                attempt = self._attempts[key]
            if attempt > 1 and not self.quiet:
                self.console.print(
                    f"{self._prefix(worker)}[yellow]↻[/yellow] {escape(event.test_id)} "
                    f"retry (attempt {attempt})"
                )
            return
        if isinstance(event, (TestPassed, TestFailed, TestSkipped)):
            result = event.result
            with self._lock:
                self._done += 1
                index = self._done
            platform = f" ({escape(result.platform)})" if result.platform else ""
            duration = format_duration(result.duration)
            progress = f"{index}/{self.total}" if self.total else str(index)
            if isinstance(event, TestPassed):
                mark = "[green]✓[/green]"
                if result.flaky:
                    mark = "[yellow]✓[/yellow]"
                    platform += " [yellow]flaky[/yellow]"
                if self.quiet:
                    return
            elif isinstance(event, TestFailed):
                mark = "[red]✗[/red]"
            else:
                mark = "[dim]-[/dim]"
                if self.quiet:
                    return
            self.console.print(
                f"{self._prefix(worker)}{mark} {progress} {escape(result.test_id):<10} "
                f"{escape(result.name)}{platform}  {duration}"
            )
            if isinstance(event, TestFailed) and result.error:
                self.console.print(f"    {escape(result.error.splitlines()[0][:300])}")
            return
        if (
            isinstance(event, (FeatureSetupCompleted, FeatureTeardownCompleted))
            and not event.passed
        ):
            phase = "setup" if isinstance(event, FeatureSetupCompleted) else "teardown"
            self.console.print(
                f"{self._prefix(worker)}[red]✗[/red] feature {escape(event.feature)} {phase}: "
                f"{escape(event.error or 'failed')}"
            )
