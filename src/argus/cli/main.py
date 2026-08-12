"""argus — command-line interface.

The CLI is a thin client of the engine's service API (TestRunner); a future
GUI uses the same services and events, never this module.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from argus import __version__
from argus.config import default_user_config_path, load_config
from argus.config.models import AppConfig
from argus.engine.filters import TestFilter
from argus.engine.runner import FailurePolicy, RunOptions, TestRunner
from argus.events.bus import EventBus
from argus.exceptions import UTFError
from argus.logging import configure_logging
from argus.models.results import RunStatus
from argus.reporting import (
    ConsoleReporter,
    write_html_report,
    write_json_report,
    write_junit_report,
)
from argus.reporting.alerts import Alert, AlertSeverity, ConsoleAlertProvider

app = typer.Typer(
    name="argus",
    help="Argus — Universal Cross-Platform Functional & Visual Testing Framework.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
console = Console(highlight=False)


class _CLIState:
    config_file: Path | None = None
    log_level: str = "INFO"
    verbose: bool = False
    quiet: bool = False


state = _CLIState()


def _load_config() -> AppConfig:
    try:
        return load_config(state.config_file)
    except UTFError as exc:
        console.print(f"[bold red]CONFIGURATION ERROR[/bold red]\n{exc}")
        raise typer.Exit(2) from exc


def _configure_logging(config: AppConfig) -> None:
    level = state.log_level
    if state.verbose:
        level = "DEBUG"
    elif state.quiet:
        level = "ERROR"
    elif state.log_level == "INFO":
        level = config.logging.level
    configure_logging(level, config.logging.format, config.logging.file)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Configuration file.")
    ] = None,
    log_level: Annotated[
        str, typer.Option("--log-level", help="DEBUG, INFO, WARNING, ERROR.")
    ] = "INFO",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate environment and tests without executing."),
    ] = False,
    version: Annotated[bool, typer.Option("--version", help="Show version.")] = False,
) -> None:
    state.config_file = config
    state.log_level = log_level
    state.verbose = verbose
    state.quiet = quiet
    if version:
        console.print(f"argus {__version__}")
        raise typer.Exit(0)
    if dry_run:
        _dry_run()
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(0)


# -- run ---------------------------------------------------------------------------------


@app.command()
def run(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Configuration file.")
    ] = None,
    test: Annotated[
        list[str] | None, typer.Option("--test", "-t", help="Run specific test ID(s).")
    ] = None,
    feature: Annotated[
        list[str] | None, typer.Option("--feature", "-f", help="Filter by feature.")
    ] = None,
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help='Filter by tag (or expression: "smoke and movies").'),
    ] = None,
    platform: Annotated[
        list[str] | None, typer.Option("--platform", "-p", help="Filter by platform.")
    ] = None,
    all_tests: Annotated[bool, typer.Option("--all", help="Run every test.")] = False,
    stop_on_failure: Annotated[
        bool,
        typer.Option(
            "--stop-on-failure/--continue-on-failure",
            help="Stop at the first failure (default) or keep going.",
        ),
    ] = True,
    max_failures: Annotated[
        int | None, typer.Option("--max-failures", help="Stop after N failures.")
    ] = None,
    skip_preflight: Annotated[
        bool, typer.Option("--skip-preflight", help="Skip pre-flight checks (not recommended).")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Run tests (optionally filtered by feature, tag, platform, or ID)."""
    if config is not None:
        state.config_file = config
    if dry_run:
        _dry_run()
        raise typer.Exit(0)

    app_config = _load_config()
    _configure_logging(app_config)

    filters = _build_filter(test, feature, tag, platform)

    console.print(f"\n[bold]Universal Test Framework[/bold]\nVersion {__version__}\n")
    console.print("Loading configuration...")
    console.print("Loading tests...")

    events = EventBus()
    ConsoleReporter(console, quiet=state.quiet).attach(events)
    runner = TestRunner(app_config, events)

    try:
        selected = runner.select(filters)
    except UTFError as exc:
        console.print(f"[bold red]TEST DEFINITION ERROR[/bold red]\n{exc}")
        raise typer.Exit(2) from exc
    if not selected:
        console.print("[yellow]No tests match the given filters.[/yellow]")
        raise typer.Exit(1)

    options = RunOptions(
        filters=filters,
        failure_policy=FailurePolicy(
            stop_on_failure=stop_on_failure and max_failures is None,
            max_failures=max_failures,
        ),
        skip_preflight=skip_preflight,
    )
    result = runner.run(options)

    if result.status == RunStatus.PREFLIGHT_FAILED:
        ConsoleAlertProvider().alert(
            Alert(
                title="PRE-FLIGHT FAILED",
                message="Functional tests were not executed. See details above.",
                severity=AlertSeverity.ERROR,
            )
        )
        _write_preflight_report(app_config, result)
        raise typer.Exit(3)

    if result.results_dir:
        results_dir = Path(result.results_dir)
        write_json_report(result, results_dir / "report.json")
        write_junit_report(result, results_dir / "junit.xml")
        write_html_report(result, results_dir / "report.html")

    raise typer.Exit(0 if result.status == RunStatus.PASSED else 1)


def _build_filter(
    test: list[str] | None,
    feature: list[str] | None,
    tag: list[str] | None,
    platform: list[str] | None,
) -> TestFilter:
    tags: list[str] = []
    tag_expression: str | None = None
    for value in tag or []:
        if any(f" {op} " in f" {value} " for op in ("and", "or", "not")):
            tag_expression = value
        else:
            tags.append(value)
    return TestFilter(
        test_ids=test or [],
        features=feature or [],
        tags=tags,
        platforms=platform or [],
        tag_expression=tag_expression,
    )


def _write_preflight_report(config: AppConfig, result) -> None:
    from argus.artifacts.manager import ArtifactManager

    manager = ArtifactManager(config.results, Path(config.root_dir or "."))
    path = manager.save_run_report(
        "preflight.json",
        __import__("json").dumps(
            [r.model_dump(mode="json") for r in result.preflight], indent=2, default=str
        ),
    )
    console.print(f"Preflight report saved: {path}")


# -- validate -----------------------------------------------------------------------------


@app.command()
def validate(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Configuration file.")
    ] = None,
    framework_only: Annotated[
        bool,
        typer.Option(
            "--framework-only",
            help="Validate the installation only (no devices or backend needed).",
        ),
    ] = False,
) -> None:
    """Validate the environment and report readiness."""
    from argus.cli.validate import print_report, validate_environment

    if config is not None:
        state.config_file = config
    app_config = _load_config()
    _configure_logging(app_config)
    report = validate_environment(app_config, framework_only=framework_only)
    print_report(report, console)
    raise typer.Exit(0 if report.ready else 3)


# -- dry run ---------------------------------------------------------------------------------


def _dry_run() -> None:
    """Validate everything a real run would use, executing nothing."""
    from argus.engine.session import RunSession
    from argus.preflight.checks import build_preflight_checks
    from argus.preflight.runner import run_preflight

    config = _load_config()
    _configure_logging(config)

    console.print(f"\n[bold]Universal Test Framework[/bold]\nVersion {__version__}\n")
    console.print("DRY RUN — no application or backend state will be modified.\n")

    runner = TestRunner(config)
    try:
        tests = runner.load()
    except UTFError as exc:
        console.print(f"[bold red]TEST DEFINITION ERROR[/bold red]\n{exc}")
        raise typer.Exit(2) from exc
    console.print(f"Loaded and validated [bold]{len(tests)}[/bold] test definitions.")

    events = EventBus()
    ConsoleReporter(console, quiet=state.quiet).attach(events)
    with RunSession(config, events) as session:
        device_names = [
            name for name, dev in sorted(config.devices.items()) if dev.configured
        ]
        checks = build_preflight_checks(session, tests, device_names)
        results, passed = run_preflight(checks, events)

    instrumentation = [r for r in results if r.name.startswith("Instrumentation")]
    other = [r for r in results if not r.name.startswith("Instrumentation")]
    console.print("\n[bold]DRY RUN COMPLETE[/bold]\n")
    console.print("Preflight:")
    console.print(f"    {len([r for r in other if r.passed])} passed")
    console.print(f"    {len([r for r in other if not r.passed])} failed\n")
    console.print("Instrumentation:")
    console.print(f"    {len([r for r in instrumentation if r.passed])} passed")
    console.print(f"    {len([r for r in instrumentation if not r.passed])} failed\n")
    console.print("Tests executed:\n    0\n")
    if not passed:
        raise typer.Exit(3)


# -- list ----------------------------------------------------------------------------------------


@app.command("list")
def list_tests(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Configuration file.")
    ] = None,
    feature: Annotated[
        list[str] | None, typer.Option("--feature", "-f")
    ] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    platform: Annotated[list[str] | None, typer.Option("--platform", "-p")] = None,
) -> None:
    """List tests grouped by feature."""
    if config is not None:
        state.config_file = config
    app_config = _load_config()
    _configure_logging(app_config)
    runner = TestRunner(app_config)
    try:
        tests = runner.select(_build_filter(None, feature, tag, platform))
    except UTFError as exc:
        console.print(f"[bold red]TEST DEFINITION ERROR[/bold red]\n{exc}")
        raise typer.Exit(2) from exc

    if not tests:
        console.print("No tests found.")
        raise typer.Exit(0)

    by_feature: dict[str, list] = {}
    for test in tests:
        by_feature.setdefault(test.feature, []).append(test)
    for feature_name in sorted(by_feature):
        console.print(f"\n[bold]{feature_name}[/bold]")
        for test in by_feature[feature_name]:
            platforms = f"  [dim]({', '.join(test.platforms)})[/dim]" if test.platforms else ""
            console.print(f"  {test.id:<10} {test.name}{platforms}")
    console.print(f"\n{len(tests)} tests.")


# -- misc ------------------------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Show the framework version."""
    console.print(f"argus {__version__}")


@app.command()
def init(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing configuration.")
    ] = False,
) -> None:
    """Create a user configuration file from a template."""
    from argus.cli.templates import CONFIG_TEMPLATE

    path = default_user_config_path()
    console.print("\nCreating Universal Test Framework configuration...\n")
    if path.exists() and not force:
        console.print(f"Configuration already exists:\n    {path}\n")
        console.print("Use --force to overwrite (your current settings would be lost).")
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    console.print(f"Configuration location:\n    {path}\n")
    console.print("Created.\n")
    console.print("Next steps:\n")
    console.print("1. Configure the backend URL (BACKEND_URL or edit the file).")
    console.print("2. Configure Android if required.")
    console.print("3. Configure Yocto if required.\n")
    console.print("Then run:\n\n    argus validate\n")


@app.command()
def update() -> None:
    """Update the installation after a git pull (reinstall dependencies + validate)."""
    repo_root = Path(__file__).resolve().parents[3]
    if not (repo_root / "pyproject.toml").exists():
        console.print(
            "[yellow]Cannot locate the repository root; update manually:\n"
            '    pip install -e ".[dev]"[/yellow]'
        )
        raise typer.Exit(1)
    console.print(f"Updating installation from {repo_root} ...")
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-e", str(repo_root)],
        check=False,
    )
    if completed.returncode != 0:
        console.print("[bold red]Update failed.[/bold red] See pip output above.")
        raise typer.Exit(1)
    console.print("[green]Dependencies up to date.[/green] Running framework validation...")
    from argus.cli.validate import print_report, validate_environment

    config = _load_config()
    report = validate_environment(config, framework_only=True)
    print_report(report, console)
    console.print("User configuration was not modified.")
    raise typer.Exit(0 if report.ready else 3)


if __name__ == "__main__":
    app()
