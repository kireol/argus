"""``argus stress`` — monkey / stress / chaos testing (see docs/stress-testing.md).

A thin Typer layer over :class:`argus.stress.engine.StressEngine`: option
parsing, Ctrl+C → cooperative cancellation, exit codes, human-readable
summaries. ``argus stress`` with no sub-command runs a scenario.
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

from argus.config.models import AppConfig
from argus.exceptions import UTFError

stress_app = typer.Typer(
    name="stress",
    help="Monkey, stress and chaos testing: randomized UI actions, backend mutations and "
    "fault injection under a reproducible seed.",
    invoke_without_command=True,
    context_settings={"max_content_width": 100},
)
console = Console(highlight=False)
_errors = Console(stderr=True, highlight=False)

_RUN = "Run options"
_SAFETY = "Safety"


class ExitCode:
    OK = 0
    FAILURES = 1
    CONFIG = 2
    INFRASTRUCTURE = 3
    CANCELLED = 130


def _install_cancellation(cancel: threading.Event) -> Callable[[], None]:
    previous: dict[int, Any] = {}

    def handler(signum: int, _frame: Any) -> None:
        if cancel.is_set():
            raise KeyboardInterrupt
        cancel.set()
        _errors.print("\n[yellow]Stopping after the current step… (Ctrl+C again to abort)[/yellow]")

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, OSError):
            previous[sig] = signal.signal(sig, handler)

    def restore() -> None:
        for sig, old in previous.items():
            signal.signal(sig, old)

    return restore


def _load(config_file: Path | None, scenario_file: Path | None, *, verbosity: str) -> tuple[AppConfig, Any]:  # noqa: E501
    from argus.config import load_config
    from argus.logging import configure_logging
    from argus.stress.config import load_scenario

    try:
        app_config = load_config(config_file)
        scenario = app_config.stress
        if scenario_file is not None:
            scenario, overrides = load_scenario(scenario_file)
            if overrides:
                merged = app_config.model_dump(mode="python")
                merged.update(overrides)
                merged["stress"] = scenario.model_dump(mode="python")
                app_config = AppConfig.model_validate(merged)
                app_config.config_file = str(scenario_file)
                if app_config.root_dir is None:
                    app_config.root_dir = str(scenario_file.resolve().parent)
                scenario = app_config.stress
    except UTFError as exc:
        _errors.print(f"[bold red]CONFIGURATION ERROR[/bold red]\n{escape(str(exc))}")
        raise typer.Exit(ExitCode.CONFIG) from exc
    except Exception as exc:  # noqa: BLE001 - pydantic errors from overrides
        _errors.print(f"[bold red]CONFIGURATION ERROR[/bold red]\n{escape(str(exc))}")
        raise typer.Exit(ExitCode.CONFIG) from exc
    level = {"quiet": "ERROR", "normal": app_config.logging.level, "verbose": "INFO",
             "debug": "DEBUG", "trace": "DEBUG"}.get(verbosity, app_config.logging.level)
    configure_logging(level, app_config.logging.format, app_config.logging.file)
    return app_config, scenario


def _print_result(result: Any, *, quiet: bool) -> None:
    from argus.stress.report import render_failure, render_summary

    record = result.record
    console.print()
    console.print(escape(render_summary(record)))
    failures = [f for f in record.failures if f.category.is_application]
    if failures and not quiet:
        for index, failure in enumerate(failures, start=1):
            console.print()
            console.print(escape(render_failure(record, failure, result.events, index=index)))
    if failures:
        console.print(f"\n[bold red]Seed: {record.seed}[/bold red]  "
                      f"[dim]({record.replay_command})[/dim]")


def _exit_code(result: Any) -> int:
    record = result.record
    if record.status == "cancelled":
        return ExitCode.CANCELLED
    if record.status == "errored":
        return ExitCode.INFRASTRUCTURE
    if any(f.category.is_application and f.severity.value in ("error", "critical")
           for f in record.failures):
        return ExitCode.FAILURES
    return ExitCode.OK


@stress_app.callback()
def stress_main(
    ctx: typer.Context,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Configuration file.")] = None,  # noqa: E501
    scenario: Annotated[Path | None, typer.Option("--scenario", "-s", help="Scenario YAML (stress: section).", rich_help_panel=_RUN)] = None,  # noqa: E501
    seed: Annotated[int | None, typer.Option("--seed", help="Seed for deterministic runs.", min=1, rich_help_panel=_RUN)] = None,  # noqa: E501
    device: Annotated[str | None, typer.Option("--device", "-d", help="Configured device to drive.", rich_help_panel=_RUN)] = None,  # noqa: E501
    duration: Annotated[str | None, typer.Option("--duration", help="Override stress.limits.duration (e.g. 10m).", rich_help_panel=_RUN)] = None,  # noqa: E501
    max_actions: Annotated[int | None, typer.Option("--max-actions", help="Override stress.limits.max_actions.", min=1, rich_help_panel=_RUN)] = None,  # noqa: E501
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Plan and print what would happen; block every mutation.", rich_help_panel=_SAFETY)] = False,  # noqa: E501
    allow_destructive: Annotated[bool, typer.Option("--allow-destructive", help="Enable destructive mutations (delete/disable/archive) for this run.", rich_help_panel=_SAFETY)] = False,  # noqa: E501
    stop_on_first: Annotated[bool, typer.Option("--stop-on-first/--continue", help="Stop at the first application failure.", rich_help_panel=_RUN)] = False,  # noqa: E501
    verbosity: Annotated[str, typer.Option("--verbosity", help="quiet | normal | verbose | debug | trace")] = "normal",  # noqa: E501
    no_persist: Annotated[bool, typer.Option("--no-persist", help="Do not write results/stress/<run_id>.")] = False,  # noqa: E501
) -> None:
    """Run a stress scenario (default), or use replay / minimize / list."""
    ctx.obj = {"config": config, "scenario": scenario, "verbosity": verbosity,
               "no_persist": no_persist}
    if ctx.invoked_subcommand is not None:
        return
    run_scenario(config, scenario, seed=seed, device=device, duration=duration,
                 max_actions=max_actions, dry_run=dry_run, allow_destructive=allow_destructive,
                 stop_on_first=stop_on_first, verbosity=verbosity, persist=not no_persist)


def run_scenario(config_file: Path | None, scenario_file: Path | None, *, seed: int | None,
                 device: str | None, duration: str | None, max_actions: int | None,
                 dry_run: bool, allow_destructive: bool, stop_on_first: bool, verbosity: str,
                 persist: bool) -> None:
    from argus.stress.engine import StressEngine
    from argus.stress.report import render_dry_run

    app_config, scenario = _load(config_file, scenario_file, verbosity=verbosity)
    if duration is not None:
        scenario.limits.duration = duration
    if max_actions is not None:
        scenario.limits.max_actions = max_actions
    if allow_destructive:
        scenario.safety.allow_destructive_mutations = True
    if stop_on_first:
        scenario.failures.stop_on_first = True
    engine = StressEngine(app_config, persist=persist and not dry_run)
    cancel = threading.Event()
    restore = _install_cancellation(cancel)
    console.print(f"\n[bold]Argus Stress[/bold] — scenario [bold]{escape(scenario.name)}[/bold]"
                  + (" [yellow](DRY RUN)[/yellow]" if dry_run else ""))
    try:
        result = engine.run(scenario, seed=seed, dry_run=dry_run, device=device, cancel=cancel)
    except UTFError as exc:
        _errors.print(f"[bold red]STRESS RUN FAILED[/bold red]\n{escape(str(exc))}")
        raise typer.Exit(ExitCode.INFRASTRUCTURE) from exc
    finally:
        restore()
    if dry_run:
        console.print()
        console.print(escape(render_dry_run(result.events, seed=result.record.seed)))
    _print_result(result, quiet=verbosity == "quiet")
    raise typer.Exit(_exit_code(result))


@stress_app.command("run")
def stress_run(
    ctx: typer.Context,
    seed: Annotated[int | None, typer.Option("--seed", min=1)] = None,
    device: Annotated[str | None, typer.Option("--device", "-d")] = None,
    duration: Annotated[str | None, typer.Option("--duration")] = None,
    max_actions: Annotated[int | None, typer.Option("--max-actions", min=1)] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    allow_destructive: Annotated[bool, typer.Option("--allow-destructive")] = False,
    stop_on_first: Annotated[bool, typer.Option("--stop-on-first/--continue")] = False,
) -> None:
    """Run a scenario (same as ``argus stress`` without a sub-command)."""
    obj = ctx.obj or {}
    run_scenario(obj.get("config"), obj.get("scenario"), seed=seed, device=device,
                 duration=duration, max_actions=max_actions, dry_run=dry_run,
                 allow_destructive=allow_destructive, stop_on_first=stop_on_first,
                 verbosity=obj.get("verbosity", "normal"), persist=not obj.get("no_persist"))


def _engine_for(ctx: typer.Context, run_id: str) -> tuple[Any, Any, Any, Any]:
    from argus.stress.config import StressConfig
    from argus.stress.engine import StressEngine

    obj = ctx.obj or {}
    app_config, _scenario = _load(obj.get("config"), obj.get("scenario"),
                                  verbosity=obj.get("verbosity", "normal"))
    engine = StressEngine(app_config, persist=not obj.get("no_persist"))
    try:
        record = engine.store.load(run_id)
        events = engine.store.trace(record.run_id)
    except UTFError as exc:
        _errors.print(f"[bold red]RUN NOT FOUND[/bold red]\n{escape(str(exc))}")
        raise typer.Exit(ExitCode.CONFIG) from exc
    scenario = StressConfig.model_validate(record.scenario)
    return engine, record, events, scenario


@stress_app.command("replay")
def stress_replay(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id (or unique prefix, or 'latest').")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Replay a recorded run's logical sequence (actions, mutations, faults, delays)."""
    engine, record, events, scenario = _engine_for(ctx, run_id)
    if not events:
        _errors.print(f"[bold red]Run {record.run_id} has no trace to replay.[/bold red]")
        raise typer.Exit(ExitCode.CONFIG)
    console.print(f"\n[bold]Replaying[/bold] {record.run_id} (seed {record.seed}, "
                  f"{len(events)} events)")
    cancel = threading.Event()
    restore = _install_cancellation(cancel)
    try:
        result = engine.run(scenario, seed=record.seed, dry_run=dry_run, script=events,
                            replay_of=record.run_id, cancel=cancel)
    except UTFError as exc:
        _errors.print(f"[bold red]REPLAY FAILED[/bold red]\n{escape(str(exc))}")
        raise typer.Exit(ExitCode.INFRASTRUCTURE) from exc
    finally:
        restore()
    original = {f.signature for f in record.failures if f.category.is_application}
    reproduced = {f.signature for f in result.application_failures}
    _print_result(result, quiet=False)
    if original:
        matched = original & reproduced
        console.print(f"\nReproduced {len(matched)}/{len(original)} original failure "
                      f"signature(s): {', '.join(sorted(matched)) or '—'}")
    raise typer.Exit(_exit_code(result))


@stress_app.command("minimize")
def stress_minimize(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id (or unique prefix, or 'latest').")],
    failure: Annotated[str | None, typer.Option("--failure", help="Failure id or 'category:detector' signature to reproduce (default: first application failure).")] = None,  # noqa: E501
    max_iterations: Annotated[int | None, typer.Option("--max-iterations", min=1)] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Find a shorter sequence that still reproduces a run's failure (delta debugging)."""
    from argus.stress.minimize import Minimizer, same_signature

    engine, record, events, scenario = _engine_for(ctx, run_id)
    app_failures = [f for f in record.failures if f.category.is_application]
    if not app_failures:
        _errors.print(f"[bold red]Run {record.run_id} recorded no application failure.[/bold red]")
        raise typer.Exit(ExitCode.CONFIG)
    target = app_failures[0]
    if failure is not None:
        match = next((f for f in app_failures if f.failure_id == failure), None)
        signature = match.signature if match is not None else failure
    else:
        signature = target.signature
    console.print(f"\n[bold]Minimizing[/bold] {record.run_id}: failure signature "
                  f"[bold]{escape(signature)}[/bold] over {len(events)} events")

    def progress(iteration: int, size: int, ok: bool) -> None:
        console.print(f"  iteration {iteration}: {size} steps → "
                      f"{'reproduced' if ok else 'not reproduced'}")

    minimizer = Minimizer(engine, scenario, seed=record.seed, predicate=same_signature(signature),
                          max_iterations=max_iterations, source_run_id=record.run_id,
                          dry_run=dry_run)
    minimizer.on_progress = progress
    try:
        result = minimizer.minimize(events)
    except UTFError as exc:
        _errors.print(f"[bold red]MINIMIZE FAILED[/bold red]\n{escape(str(exc))}")
        raise typer.Exit(ExitCode.INFRASTRUCTURE) from exc
    if not result.reproduced:
        console.print("\n[yellow]The original sequence did not reproduce the failure; "
                      "nothing to minimize.[/yellow]")
        raise typer.Exit(ExitCode.FAILURES)
    console.print(f"\nReduced {result.original_steps} → {result.minimized_steps} steps "
                  f"({result.reduction:.0%}) in {result.iterations} iterations, "
                  f"{result.replays} replays.")
    console.print("\nMinimal sequence:")
    for index, plan in enumerate(result.plans, start=1):
        for mutation in plan.before:
            console.print(f"  {index:>3}  MUTATION   {escape(mutation.describe())}")
        if plan.action is not None:
            console.print(f"  {index:>3}  {escape(plan.action.describe())}")
        for mutation in plan.after:
            console.print(f"  {index:>3}  MUTATION   {escape(mutation.describe())}")
        for fault in plan.faults:
            console.print(f"  {index:>3}  {escape(fault.describe())}")
    if result.final_run is not None and result.final_run.run_dir is not None:
        console.print(f"\nMinimized run saved: {result.final_run.record.run_id} "
                      f"({result.final_run.run_dir})")
        console.print(f"Replay: argus stress replay {result.final_run.record.run_id}")
    raise typer.Exit(ExitCode.OK)


@stress_app.command("list")
def stress_list(ctx: typer.Context, limit: Annotated[int, typer.Option("--limit", min=1)] = 20) -> None:  # noqa: E501
    """List recorded stress runs."""
    from argus.stress.engine import StressEngine

    obj = ctx.obj or {}
    app_config, _scenario = _load(obj.get("config"), obj.get("scenario"),
                                  verbosity=obj.get("verbosity", "normal"))
    records = StressEngine(app_config, persist=False).store.records()
    if not records:
        console.print("No stress runs recorded.")
        raise typer.Exit(ExitCode.OK)
    console.print(f"{'run id':<28} {'seed':>10} {'status':<10} {'actions':>8} {'mutations':>9} "
                  f"{'failures':>8}  scenario")
    for record in records[-limit:]:
        failures = len([f for f in record.failures if f.category.is_application])
        console.print(f"{record.run_id:<28} {record.seed:>10} {record.status:<10} "
                      f"{record.summary.actions:>8} {record.summary.mutations:>9} "
                      f"{failures:>8}  {escape(record.scenario_name)}")
    raise typer.Exit(ExitCode.OK)


__all__ = ["ExitCode", "stress_app"]
