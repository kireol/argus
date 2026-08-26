"""``argus ci`` — CI/CD-native execution (see docs/ci-cd.md).

A thin Typer layer over :class:`argus.ci.runner.CIRunner`; it owns signal
handling and process exit codes, nothing else.
"""

from __future__ import annotations

import contextlib
import signal
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape

from argus.ci.exit_codes import ExitCode
from argus.exceptions import UTFError

ci_app = typer.Typer(
    name="ci",
    help="CI/CD-native test execution: provider detection, suites, retries, "
    "quality gates, and machine-readable artifacts.",
    no_args_is_help=True,
    context_settings={"max_content_width": 100},
)

_SELECT = "Test selection"
_EXEC = "Execution"
_OUTPUT = "Output"


def _install_cancellation(cancel: threading.Event) -> Callable[[], None]:
    """Turn SIGINT/SIGTERM into cooperative cancellation; return a restorer."""
    previous: dict[int, Any] = {}

    def handler(signum: int, _frame: Any) -> None:
        if cancel.is_set():
            # Second signal: give up waiting for the in-flight test.
            raise KeyboardInterrupt
        cancel.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Not on the main thread / unsupported signal: run without cancellation.
        with contextlib.suppress(ValueError, OSError):
            previous[sig] = signal.signal(sig, handler)

    def restore() -> None:
        for sig, old in previous.items():
            signal.signal(sig, old)

    return restore


@ci_app.command("run")
def ci_run(
    suite: Annotated[
        str | None,
        typer.Option("--suite", "-s", help="Named suite from ci.suites.", rich_help_panel=_SELECT),
    ] = None,
    test: Annotated[
        list[str] | None,
        typer.Option("--test", "-t", help="Run specific test ID(s).", rich_help_panel=_SELECT),
    ] = None,
    feature: Annotated[
        list[str] | None,
        typer.Option("--feature", "-f", help="Filter by feature.", rich_help_panel=_SELECT),
    ] = None,
    tag: Annotated[
        list[str] | None,
        typer.Option(
            "--tag",
            help='Filter by tag (or expression: "smoke and movies").',
            rich_help_panel=_SELECT,
        ),
    ] = None,
    platform: Annotated[
        list[str] | None,
        typer.Option("--platform", "-p", help="Filter by platform.", rich_help_panel=_SELECT),
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Configuration file.")
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="CI provider (auto, github, gitlab, jenkins, azure, generic, local).",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Resolve suite/tests, validate the environment, execute nothing.",
            rich_help_panel=_EXEC,
        ),
    ] = False,
    fail_fast: Annotated[
        bool | None,
        typer.Option(
            "--fail-fast/--no-fail-fast",
            help="Stop at the first failure (default: run everything, ci.execution.fail_fast).",
            rich_help_panel=_EXEC,
        ),
    ] = None,
    retry: Annotated[
        int | None,
        typer.Option(
            "--retry",
            help="Total attempts per test for transient failures (1 = no retry).",
            min=1,
            max=10,
            rich_help_panel=_EXEC,
        ),
    ] = None,
    workers: Annotated[
        int | None,
        typer.Option(
            "--workers",
            "-w",
            help="Parallel workers (each owns distinct devices).",
            min=1,
            max=64,
            rich_help_panel=_EXEC,
        ),
    ] = None,
    strategy: Annotated[
        str | None,
        typer.Option(
            "--strategy",
            help="Scheduling strategy: sequential or balanced.",
            rich_help_panel=_EXEC,
        ),
    ] = None,
    skip_preflight: Annotated[
        bool,
        typer.Option(
            "--skip-preflight", help="Skip pre-flight checks (not recommended).",
            rich_help_panel=_EXEC,
        ),
    ] = False,
    output_dir: Annotated[
        str | None,
        typer.Option(
            "--output-dir", "-o",
            help="Artifact directory (default: ci.artifacts.directory = argus-results).",
            rich_help_panel=_OUTPUT,
        ),
    ] = None,
    no_report: Annotated[
        bool,
        typer.Option(
            "--no-report",
            help="Skip provider publishing (job summary, annotations).",
            rich_help_panel=_OUTPUT,
        ),
    ] = False,
    no_artifacts: Annotated[
        bool,
        typer.Option(
            "--no-artifacts",
            help="Write no artifact directory (console output and exit code only).",
            rich_help_panel=_OUTPUT,
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="DEBUG logging.", rich_help_panel=_OUTPUT)
    ] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Failures and summary only.",
                           rich_help_panel=_OUTPUT)
    ] = False,
) -> None:
    """Run tests the CI way: detect the provider, resolve a suite, retry transient
    failures, apply quality gates, write argus-results/, publish provider reports.

    Selection precedence: CLI selectors narrow the suite's selectors (AND);
    without --suite every test runs. Exit codes: 0 success · 1 test failure ·
    2 configuration · 3 environment · 4 test definition · 5 reporting ·
    6 policy · 7 internal · 8 cancelled.
    """
    from argus.ci.runner import CIRunner, CIRunRequest
    from argus.cli.main import _configure_logging, _load_config, state
    from argus.engine.filters import build_filter

    console = Console(highlight=False, soft_wrap=True)
    if config is not None:
        state.config_file = config
    if verbose:
        state.verbose = True
    if quiet:
        state.quiet = True
    if strategy is not None and strategy not in ("sequential", "balanced"):
        console.print(
            "[bold red]CONFIGURATION ERROR[/bold red]\n--strategy must be 'sequential' "
            "or 'balanced'."
        )
        raise typer.Exit(int(ExitCode.CONFIGURATION_ERROR))

    try:
        app_config = _load_config()
    except typer.Exit as exc:
        raise typer.Exit(int(ExitCode.CONFIGURATION_ERROR)) from exc
    if not app_config.ci.enabled:
        console.print(
            "[bold red]CONFIGURATION ERROR[/bold red]\nci.enabled is false in configuration."
        )
        raise typer.Exit(int(ExitCode.CONFIGURATION_ERROR))
    _configure_logging(app_config)

    request = CIRunRequest(
        suite=suite,
        filters=build_filter(test_ids=test, features=feature, tags=tag, platforms=platform),
        provider=provider,
        dry_run=dry_run,
        publish=not no_report,
        artifacts=not no_artifacts,
        fail_fast=fail_fast,
        retry=retry,
        workers=workers,
        strategy=strategy,
        output_dir=output_dir,
        skip_preflight=skip_preflight,
        quiet=quiet,
    )
    cancel = threading.Event()
    restore = _install_cancellation(cancel)
    runner = CIRunner(app_config, console=console, cancel=cancel)
    try:
        if dry_run:
            try:
                outcome = runner.dry_run(request)
            except UTFError as exc:
                from argus.ci.classify import classify_exception

                category, code = classify_exception(exc)
                console.print(
                    f"[bold red]{category.value.replace('_', ' ').upper()}[/bold red]\n"
                    f"{escape(str(exc))}"
                )
                raise typer.Exit(int(code)) from exc
        else:
            outcome = runner.run(request)
    finally:
        restore()
    raise typer.Exit(int(outcome.exit_code))
