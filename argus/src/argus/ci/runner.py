"""CIRunner — the ``argus ci run`` orchestration layer.

Sequence::

    plan      detect provider, resolve suite -> engine TestFilter, select tests,
              expand execution units, schedule workers
    prepare   artifact directory, run log, metadata (ci/git/environment)
    preflight once (the engine's own checks), then config ``setup`` once
    execute   one TestRunner.run per batch; workers own disjoint devices
    classify  engine results -> CI outcomes, known failures, flaky detection
    policy    quality gates -> PolicyResult
    report    report.json / junit.xml / report.html, provider publishing
    exit      map everything to the ExitCode contract

Everything test-related is delegated to :class:`~argus.engine.runner.TestRunner`.
No provider-specific logic lives here: providers come from the registry,
publishing from the reporter registry.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from argus.ci.artifacts import CIArtifactLayout
from argus.ci.categories import FailureCategory, retry_categories_for_engine
from argus.ci.classify import CancelledRun, ReportingError, classify_exception, classify_test
from argus.ci.console import CIConsoleReporter
from argus.ci.context import CIContext
from argus.ci.exit_codes import ExitCode
from argus.ci.policy import evaluate_policy
from argus.ci.providers import CIProvider, ProviderRegistry, default_provider_registry
from argus.ci.reporters import ReporterRegistry, default_reporter_registry
from argus.ci.result import (
    CIRunResult,
    CIRunStatus,
    CITestResult,
    RetrySummary,
    TestOutcome,
    ci_result_to_dict,
)
from argus.ci.scheduler import ExecutionUnit, Schedule, plan_units, scheduler_for
from argus.ci.suites import combine_filters, resolve_suite, suite_filter
from argus.config.models import AppConfig, CIKnownFailure, CISuiteConfig
from argus.engine.filters import TestFilter
from argus.engine.runner import FailurePolicy, RetryOverride, RunOptions, TestRunner
from argus.engine.session import RunSession
from argus.events.bus import EventBus
from argus.exceptions import UTFError
from argus.logging import get_logger
from argus.models.results import (
    PreflightResult,
    RunResult,
    RunStatus,
    TestResult,
    TestStatus,
)
from argus.models.test_definition import TestDefinition
from argus.preflight.checks import build_preflight_checks
from argus.preflight.runner import run_preflight
from argus.reporting import write_html_report, write_junit_report

Clock = Callable[[], float]


def new_run_id(now: datetime | None = None) -> str:
    """``YYYYMMDD-HHMMSS-<6 hex>`` — readable, sortable, collision-safe."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


@dataclass(frozen=True)
class CIRunRequest:
    """What ``argus ci run`` was asked to do (CLI flags, already parsed)."""

    suite: str | None = None
    filters: TestFilter = field(default_factory=TestFilter)
    provider: str | None = None
    dry_run: bool = False
    publish: bool = True
    artifacts: bool = True
    fail_fast: bool | None = None
    retry: int | None = None
    workers: int | None = None
    strategy: str | None = None
    output_dir: str | None = None
    skip_preflight: bool = False
    quiet: bool = False


@dataclass
class RunPlan:
    """Everything resolved before any test executes."""

    provider: CIProvider
    context: CIContext
    suite_name: str | None
    suite: CISuiteConfig | None
    filters: TestFilter
    tests: list[TestDefinition]
    units: list[ExecutionUnit]
    schedule: Schedule
    workers: int
    strategy: str
    retry: RetryOverride | None
    retry_summary: RetrySummary
    fail_fast: bool
    layout: CIArtifactLayout | None
    required_selection: dict[str, set[str]]
    known_failures: dict[tuple[str, str | None], CIKnownFailure]
    device_names: list[str]

    def describe_selection(self) -> dict[str, Any]:
        described: dict[str, Any] = dict(self.filters.describe())
        if self.suite_name:
            described["suite"] = self.suite_name
        return described


@dataclass
class CIRunOutcome:
    result: CIRunResult
    exit_code: ExitCode
    plan: RunPlan | None


class _StopSignal(threading.Event):
    """An event that is also set whenever any of its ``sources`` is set.

    Workers stop on *either* user cancellation (the runner's ``cancel`` event)
    or a fail-fast trigger, while the runner can still tell the two apart.
    """

    def __init__(self, *sources: threading.Event) -> None:
        super().__init__()
        self._sources = sources

    def is_set(self) -> bool:
        return super().is_set() or any(source.is_set() for source in self._sources)


class CIRunner:
    """Orchestrates one CI run over the existing engine (see module docstring)."""

    def __init__(
        self,
        config: AppConfig,
        *,
        environment: Mapping[str, str] | None = None,
        providers: ProviderRegistry | None = None,
        reporters: ReporterRegistry | None = None,
        console: Console | None = None,
        cancel: threading.Event | None = None,
        clock: Clock = time.monotonic,
        run_id: str | None = None,
    ) -> None:
        self.config = config
        self.environment: dict[str, str] = dict(os.environ if environment is None else environment)
        self.providers = providers or default_provider_registry()
        self.reporters = reporters or default_reporter_registry()
        self.console = console or Console(highlight=False)
        self.cancel = cancel or threading.Event()
        self.clock = clock
        self.run_id = run_id or new_run_id()
        self.log = get_logger("argus.ci", run_id=self.run_id)
        self._timings: dict[str, float] = {}
        self._quiet = False
        self._previous_log_level = logging.INFO

    # -- planning ----------------------------------------------------------------------

    def plan(self, request: CIRunRequest) -> RunPlan:
        ci = self.config.ci
        started = self.clock()
        provider_name = (request.provider or ci.provider).strip().lower()
        provider = self.providers.resolve(provider_name, self.environment)
        context = provider.collect_context(self.environment)

        suite: CISuiteConfig | None = None
        filters = request.filters
        if request.suite:
            suite = resolve_suite(ci, request.suite)
            filters = combine_filters(suite_filter(suite), request.filters)
        required_selection: dict[str, set[str]] = {}
        for name in ci.policy.required:
            resolve_suite(ci, name)  # unknown required suite -> configuration error

        runner = TestRunner(self.config)
        loaded = runner.load()
        tests = filters.apply(loaded)
        for name in ci.policy.required:
            required_selection[name] = {
                t.id for t in suite_filter(resolve_suite(ci, name)).apply(loaded)
            }

        workers = request.workers or ci.execution.workers
        strategy = request.strategy or ci.execution.strategy
        if workers <= 1:
            strategy = "sequential"
        units = plan_units(self.config, tests, filters)
        schedule = scheduler_for(strategy).schedule(self.config, units, workers)

        retry_override: RetryOverride | None = None
        attempts = (
            request.retry
            if request.retry is not None
            else (ci.retry.max_attempts if ci.retry.enabled else 1)
        )
        retry_on = list(ci.retry.on)
        if attempts > 1:
            retry_override = RetryOverride(
                max_attempts=attempts, categories=retry_categories_for_engine(retry_on)
            )
        retry_summary = RetrySummary(enabled=attempts > 1, max_attempts=attempts, on=retry_on)

        fail_fast = request.fail_fast if request.fail_fast is not None else ci.execution.fail_fast

        layout: CIArtifactLayout | None = None
        if request.artifacts and ci.artifacts.enabled:
            layout = CIArtifactLayout(
                request.output_dir or ci.artifacts.directory,
                Path(self.config.root_dir or "."),
            )

        known = {(k.test, k.platform): k for k in ci.known_failures}
        device_names = runner.device_names_for(tests, filters)
        self._timings["plan"] = self.clock() - started
        return RunPlan(
            provider=provider,
            context=context,
            suite_name=request.suite,
            suite=suite,
            filters=filters,
            tests=tests,
            units=units,
            schedule=schedule,
            workers=schedule.worker_count,
            strategy=schedule.strategy,
            retry=retry_override,
            retry_summary=retry_summary,
            fail_fast=fail_fast,
            layout=layout,
            required_selection=required_selection,
            known_failures=known,
            device_names=device_names,
        )

    # -- dry run -----------------------------------------------------------------------

    def dry_run(self, request: CIRunRequest) -> CIRunOutcome:
        """Resolve everything, validate the environment, execute nothing."""
        self._quiet = request.quiet
        plan = self.plan(request)
        result = self._new_result(plan, status=CIRunStatus.NOT_RUN)
        self._print_header(plan, dry=True)
        preflight_ok = True
        if not request.skip_preflight and plan.tests:
            results, preflight_ok = self._preflight(plan)
            result.preflight = results
        self.console.print("Selected tests:")
        for unit in plan.units:
            target = f" ({unit.platform})" if unit.platform else ""
            self.console.print(f"  ✓ {escape(unit.test.id)}  {escape(unit.test.name)}{target}")
        if not plan.units:
            self.console.print("  (none)")
        self.console.print()
        if plan.layout is not None:
            self.console.print(f"Artifacts:\n  {plan.layout.directory}\n")
        else:
            self.console.print("Artifacts:\n  disabled\n")
        for note in plan.schedule.notes:
            self.console.print(f"[yellow]note:[/yellow] {escape(note)}")
        self.console.print("No tests were executed.")
        result.finished_at = datetime.now(UTC)
        result.timings = dict(self._timings)
        exit_code = ExitCode.SUCCESS if preflight_ok else ExitCode.ENVIRONMENT_ERROR
        return CIRunOutcome(result=result, exit_code=exit_code, plan=plan)

    # -- run ---------------------------------------------------------------------------

    def run(self, request: CIRunRequest) -> CIRunOutcome:
        started = self.clock()
        self._quiet = request.quiet
        plan: RunPlan | None = None
        log_handler: logging.Handler | None = None
        try:
            plan = self.plan(request)
            result = self._new_result(plan)
            if plan.layout is not None:
                plan.layout.prepare()
                log_handler = self._attach_run_log(plan.layout)
                plan.layout.write_metadata(plan.context, self.environment)
            self._print_header(plan)
            exit_code = self._execute(request, plan, result)
        except BaseException as exc:  # noqa: BLE001 - classified, never swallowed
            category, exit_code = classify_exception(exc)
            if isinstance(exc, KeyboardInterrupt):
                self.cancel.set()
            if exit_code == ExitCode.INTERNAL_ERROR or not isinstance(
                exc, (UTFError, KeyboardInterrupt)
            ):
                self.log.exception("Unexpected error during CI run")
            message = str(exc) if str(exc) else type(exc).__name__
            result = self._error_result(plan, category, message, exit_code)
            self.console.print(f"\n[bold red]{category.value.replace('_', ' ').upper()}[/bold red]")
            self.console.print(escape(message))
            if plan is not None and plan.layout is not None:
                self._finalize_best_effort(plan, result)
        finally:
            if log_handler is not None:
                root_logger = logging.getLogger("argus")
                root_logger.removeHandler(log_handler)
                root_logger.setLevel(self._previous_log_level)
                log_handler.close()
        result.duration = self.clock() - started
        result.finished_at = datetime.now(UTC)
        result.timings = dict(self._timings)
        result.timings["total"] = result.duration
        if plan is not None and plan.layout is not None and result.status != CIRunStatus.ERROR:
            # report.json is written last so it carries the final timings.
            self._write_json_report(plan.layout, result)
        self._print_summary(result, plan, exit_code)
        return CIRunOutcome(result=result, exit_code=exit_code, plan=plan)

    def _execute(self, request: CIRunRequest, plan: RunPlan, result: CIRunResult) -> ExitCode:
        if not plan.units:
            result.status = CIRunStatus.NOT_RUN
            result.error = "no tests match the given selection"
            result.error_category = FailureCategory.CONFIGURATION_ERROR
            self._apply_policy(plan, result, run_failed=result.error)
            self._write_reports(plan, result)
            return ExitCode.CONFIGURATION_ERROR

        # -- preflight + setup, once for the whole run --------------------------------
        if not request.skip_preflight:
            checks, passed = self._preflight(plan)
            result.preflight = checks
            if plan.layout is not None:
                plan.layout.write_json(
                    "preflight.json", [c.model_dump(mode="json") for c in checks]
                )
            if not passed:
                return self._environment_failure(
                    plan, result, "pre-flight checks failed", FailureCategory.DEVICE_ERROR
                )
        if self.cancel.is_set():
            raise CancelledRun("cancelled before tests started")
        if self.config.setup:
            phase_started = self.clock()
            ok, error = TestRunner(self.config, EventBus()).run_setup()
            self._timings["setup"] = self.clock() - phase_started
            if not ok:
                return self._environment_failure(
                    plan,
                    result,
                    f"setup failed: {error}",
                    FailureCategory.INFRASTRUCTURE_ERROR,
                )

        # -- execution -------------------------------------------------------------------
        phase_started = self.clock()
        engine_results = self._run_workers(plan, request)
        self._timings["execution"] = self.clock() - phase_started
        self._merge(plan, result, engine_results)

        # -- policy + reports --------------------------------------------------------------
        self._apply_policy(plan, result)
        if self.cancel.is_set():
            result.status = CIRunStatus.CANCELLED
            result.error = "run cancelled"
        elif result.policy.failed or any(
            t.outcome in (TestOutcome.FAILED, TestOutcome.ERROR) for t in result.tests
        ):
            result.status = CIRunStatus.FAILED
        else:
            result.status = CIRunStatus.PASSED

        reporting_error = self._write_reports(plan, result)
        if request.publish:
            reporting_error = self._publish(plan, result) or reporting_error

        if result.status == CIRunStatus.CANCELLED:
            return ExitCode.CANCELLED
        if result.policy.failed:
            rules = result.policy.failing_rules
            if rules & {"failures", "visual_regression"}:
                return ExitCode.TEST_FAILURE
            return ExitCode.POLICY_FAILURE
        if result.status == CIRunStatus.FAILED:
            # Failures exist but the policy chose to warn/ignore them.
            return ExitCode.SUCCESS if not reporting_error else ExitCode.CI_ERROR
        if reporting_error:
            return ExitCode.CI_ERROR
        return ExitCode.SUCCESS

    # -- phases ------------------------------------------------------------------------

    def _preflight(self, plan: RunPlan) -> tuple[list[PreflightResult], bool]:
        started = self.clock()
        events = EventBus()
        with RunSession(self.config, events) as session:
            checks = build_preflight_checks(session, plan.tests, plan.device_names)
            results, passed = run_preflight(checks, events)
        self._timings["preflight"] = self.clock() - started
        failed = [r for r in results if not r.passed and r.required]
        for check in failed:
            self.console.print(
                f"[red]✗[/red] preflight: {escape(check.name)} — {escape(check.error or '')}"
            )
            if check.remediation:
                self.console.print(f"    {escape(check.remediation)}")
        if not self._quiet:
            self.console.print(
                f"Preflight: {len(results) - len(failed)} passed, {len(failed)} failed"
            )
        return results, passed

    def _run_workers(self, plan: RunPlan, request: CIRunRequest) -> list[tuple[int, RunResult]]:
        reporter = CIConsoleReporter(
            self.console, total=len(plan.units), workers=plan.workers, quiet=request.quiet
        )
        results: list[tuple[int, RunResult]] = []
        lock = threading.Lock()
        errors: list[BaseException] = []
        stop = _StopSignal(self.cancel)
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if plan.layout is not None:
            results_dir = plan.layout.tests_dir
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="argus-ci-")
            results_dir = Path(temp_dir.name)

        def worker_main(worker_plan: Any) -> None:
            config = self._worker_config(plan, worker_plan.devices)
            for batch in worker_plan.batches:
                if stop.is_set():
                    break
                events = EventBus()
                reporter.attach(events, worker_plan.worker)
                options = RunOptions(
                    filters=batch.filters(plan.filters),
                    failure_policy=FailurePolicy(stop_on_failure=plan.fail_fast),
                    skip_preflight=True,
                    skip_setup=True,
                    retry=plan.retry,
                    cancel=stop,
                    results_dir=results_dir,
                )
                try:
                    engine_result = TestRunner(config, events).run(options)
                except BaseException as exc:  # noqa: BLE001 - surfaced after join
                    with lock:
                        errors.append(exc)
                    stop.set()
                    return
                with lock:
                    results.append((worker_plan.worker, engine_result))
                if plan.fail_fast and engine_result.failed_count:
                    stop.set()  # other workers stop scheduling; this is not a cancellation

        try:
            if plan.workers <= 1:
                for worker_plan in plan.schedule.workers:
                    worker_main(worker_plan)
            else:
                threads = [
                    threading.Thread(
                        target=worker_main,
                        args=(worker_plan,),
                        name=f"argus-ci-w{worker_plan.worker}",
                        daemon=True,
                    )
                    for worker_plan in plan.schedule.workers
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    while thread.is_alive():
                        thread.join(0.2)
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()
        if errors:
            raise errors[0]
        return results

    def _worker_config(self, plan: RunPlan, devices: list[str]) -> AppConfig:
        config = self.config.model_copy(deep=True)
        config.results.retain_on_success = self.config.ci.artifacts.retain_on_success
        if self.config.ci.artifacts.save_comparisons:
            config.results.save_comparison_images = True
        if plan.workers > 1:
            config.devices = {n: d for n, d in self.config.devices.items() if n in devices}
        return config

    # -- merging + classification -----------------------------------------------------------

    def _merge(
        self, plan: RunPlan, result: CIRunResult, engine_results: list[tuple[int, RunResult]]
    ) -> None:
        by_key: dict[tuple[str, str | None], tuple[int, TestResult]] = {}
        preflight = list(result.preflight)
        stopped = False
        reason: str | None = None
        for worker_id, engine_result in engine_results:
            preflight.extend(engine_result.preflight)
            if engine_result.stopped_early:
                stopped = True
                if reason is None and engine_result.stop_reason != "cancelled":
                    reason = engine_result.stop_reason
            for test in engine_result.tests:
                key = (test.test_id, test.platform)
                if key not in by_key or test.status != TestStatus.SKIPPED:
                    by_key[key] = (worker_id, test)

        merged_tests: list[TestResult] = []
        ci_tests: list[CITestResult] = []
        cancelled = self.cancel.is_set()
        for unit in plan.units:
            key = (unit.test.id, unit.platform)
            found = by_key.get(key)
            worker: int | None
            if found is None:
                engine_test = TestResult(
                    test_id=unit.test.id,
                    name=unit.test.name,
                    feature=unit.test.feature,
                    platform=unit.platform,
                    status=TestStatus.SKIPPED,
                    error="cancelled" if cancelled else "not run",
                )
                worker = None
            else:
                worker, engine_test = found
            merged_tests.append(engine_test)
            ci_tests.append(self._classify(plan, engine_test, worker, stopped or cancelled))

        merged = RunResult(
            status=RunStatus.PASSED,
            started_at=result.started_at,
            preflight=preflight,
            tests=merged_tests,
            results_dir=str(plan.layout.tests_dir) if plan.layout is not None else None,
            stopped_early=stopped or cancelled,
            stop_reason="cancelled"
            if cancelled
            else (reason or ("stopped early" if stopped else None)),
        )
        if cancelled:
            merged.status = RunStatus.CANCELLED
        elif stopped:
            merged.status = RunStatus.STOPPED
        elif merged.failed_count:
            merged.status = RunStatus.FAILED
        result.engine_result = merged
        result.preflight = preflight
        result.tests = ci_tests

    def _classify(
        self, plan: RunPlan, test: TestResult, worker: int | None, interrupted: bool
    ) -> CITestResult:
        category = classify_test(test)
        known = plan.known_failures.get((test.test_id, test.platform)) or plan.known_failures.get(
            (test.test_id, None)
        )
        if test.status == TestStatus.PASSED:
            outcome = TestOutcome.FLAKY_PASSED if test.flaky else TestOutcome.PASSED
        elif test.status == TestStatus.SKIPPED:
            outcome = TestOutcome.NOT_RUN if interrupted else TestOutcome.SKIPPED
        elif known is not None:
            outcome = TestOutcome.KNOWN_FAILURE
        elif test.status == TestStatus.ERROR:
            outcome = TestOutcome.ERROR
        else:
            outcome = TestOutcome.FAILED
        artifacts: list[str] = []
        if plan.layout is not None:
            for record in [*test.attempt_history, None]:
                directory = record.artifact_dir if record is not None else test.artifact_dir
                if directory and Path(directory).is_dir():
                    for file in sorted(p for p in Path(directory).rglob("*") if p.is_file()):
                        rel = plan.layout.relative(file)
                        if rel not in artifacts:
                            artifacts.append(rel)
        return CITestResult(
            test_id=test.test_id,
            name=test.name,
            feature=test.feature,
            platform=test.platform,
            status=test.status,
            outcome=outcome,
            duration=test.duration,
            attempts=test.attempts,
            flaky=test.flaky,
            initial_failure=test.initial_failure,
            failure_category=category,
            failure_message=test.error,
            known_failure_reason=(
                known.reason if known is not None and outcome == TestOutcome.KNOWN_FAILURE else None
            ),
            worker=worker,
            artifact_dir=test.artifact_dir,
            artifacts=artifacts,
            attempt_history=list(test.attempt_history),
            metrics=test.metrics,
        )

    def _apply_policy(
        self, plan: RunPlan, result: CIRunResult, *, run_failed: str | None = None
    ) -> None:
        started = self.clock()
        result.policy = evaluate_policy(
            self.config.ci.policy,
            result.tests,
            required_selection=plan.required_selection,
            run_failed=run_failed,
        )
        self._timings["policy"] = self.clock() - started

    def _environment_failure(
        self, plan: RunPlan, result: CIRunResult, message: str, category: FailureCategory
    ) -> ExitCode:
        result.status = CIRunStatus.ERROR
        result.error = message
        result.error_category = category
        result.tests = [
            self._classify(
                plan,
                TestResult(
                    test_id=u.test.id,
                    name=u.test.name,
                    feature=u.test.feature,
                    platform=u.platform,
                    status=TestStatus.SKIPPED,
                    error=message,
                ),
                None,
                True,
            )
            for u in plan.units
        ]
        result.engine_result = RunResult(
            status=RunStatus.PREFLIGHT_FAILED
            if category == FailureCategory.DEVICE_ERROR
            else RunStatus.SETUP_FAILED,
            started_at=result.started_at,
            preflight=list(result.preflight),
            tests=[],
            stop_reason=message,
        )
        self._apply_policy(plan, result, run_failed=message)
        self._write_reports(plan, result)
        self.console.print(f"\n[bold red]ENVIRONMENT ERROR[/bold red]\n{escape(message)}")
        return ExitCode.ENVIRONMENT_ERROR

    # -- reports --------------------------------------------------------------------------

    def _write_reports(self, plan: RunPlan, result: CIRunResult) -> ReportingError | None:
        layout = plan.layout
        if layout is None:
            return None
        started = self.clock()
        try:
            result.artifacts_dir = str(layout.directory)
            engine_result = result.engine_result
            if engine_result is not None:
                self._write_junit(layout, result, engine_result)
                self._write_html(layout, result, engine_result)
            owners = {
                Path(t.artifact_dir).name: (t.test_id, t.platform)
                for t in result.tests
                if t.artifact_dir
            }
            for t in result.tests:
                for record in t.attempt_history:
                    if record.artifact_dir:
                        owners[Path(record.artifact_dir).name] = (t.test_id, t.platform)
            self._write_json_report(layout, result)
            result.artifacts = layout.inventory(owners)
            self._write_json_report(layout, result)  # now includes the inventory
        except OSError as exc:
            self.log.error("Failed to write CI reports: %s", exc)
            return ReportingError(f"failed to write CI reports: {exc}")
        finally:
            self._timings["reports"] = self.clock() - started
        return None

    def _write_json_report(self, layout: CIArtifactLayout, result: CIRunResult) -> None:
        layout.report_json.parent.mkdir(parents=True, exist_ok=True)
        layout.report_json.write_text(
            json.dumps(ci_result_to_dict(result), indent=2, default=str), encoding="utf-8"
        )

    def _write_junit(
        self, layout: CIArtifactLayout, result: CIRunResult, engine_result: RunResult
    ) -> None:
        by_key = {t.key: t for t in result.tests}
        ctx = result.context

        def case_properties(test: TestResult) -> dict[str, str]:
            ci_test = by_key.get((test.test_id, test.platform))
            props: dict[str, str] = {"attempts": str(test.attempts)}
            if ci_test is not None:
                props["outcome"] = ci_test.outcome.value
                if ci_test.failure_category:
                    props["failure_category"] = ci_test.failure_category.value
                if ci_test.flaky:
                    props["flaky"] = "true"
                if ci_test.known_failure_reason:
                    props["known_failure"] = ci_test.known_failure_reason
            return props

        suite_props = {
            "argus.run_id": result.run_id,
            "argus.provider": result.provider,
        }
        if result.suite:
            suite_props["argus.suite"] = result.suite
        if ctx.short_commit:
            suite_props["argus.commit"] = ctx.short_commit
        if ctx.branch:
            suite_props["argus.branch"] = ctx.branch
        write_junit_report(
            engine_result,
            layout.junit_xml,
            properties=suite_props,
            case_properties=case_properties,
        )

    def _write_html(
        self, layout: CIArtifactLayout, result: CIRunResult, engine_result: RunResult
    ) -> None:
        by_key = {t.key: t for t in result.tests}
        ctx = result.context
        fields: list[tuple[str, str]] = [
            ("Status", result.status.value.replace("_", " ").upper()),
            ("Policy", result.policy.status),
            ("Provider", ctx.display_name),
            ("Suite", result.suite or "—"),
            ("Branch", ctx.branch or "—"),
            ("Commit", ctx.short_commit or "—"),
            ("PR", f"#{ctx.pull_request}" if ctx.pull_request else "—"),
            ("Run ID", result.run_id),
            ("Workers", f"{result.workers} ({result.strategy})"),
            (
                "Retry",
                f"{result.retry.max_attempts} attempt(s) on {', '.join(result.retry.on)}"
                if result.retry.enabled
                else "disabled",
            ),
            ("Flaky", str(result.flaky_count)),
            ("Known failures", str(result.known_failure_count)),
        ]
        notices: list[tuple[str, str]] = [
            ("error" if v.action == "fail" else "warning", f"policy {v.rule}: {v.message}")
            for v in result.policy.violations
        ]
        if result.error:
            notices.insert(0, ("error", result.error))

        def badges(test: TestResult) -> list[str]:
            ci_test = by_key.get((test.test_id, test.platform))
            if ci_test is None:
                return []
            labels: list[str] = []
            if ci_test.flaky:
                labels.append(f"flaky ×{ci_test.attempts}")
            if ci_test.outcome == TestOutcome.KNOWN_FAILURE:
                labels.append("known failure")
            if ci_test.outcome == TestOutcome.NOT_RUN:
                labels.append("not run")
            if ci_test.failure_category == FailureCategory.VISUAL_REGRESSION:
                labels.append("visual regression")
            return labels

        write_html_report(
            engine_result,
            layout.report_html,
            title="Argus CI report",
            status_label=result.status.value.replace("_", " ").upper(),
            header_fields=fields,
            notices=notices,
            badges=badges,
        )

    def _publish(self, plan: RunPlan, result: CIRunResult) -> ReportingError | None:
        started = self.clock()
        reporting = self.config.ci.reporting
        reporter = self.reporters.for_provider(plan.provider.name)
        try:
            notes = reporter.publish(
                result,
                plan.layout,
                self.environment,
                summary=reporting.summary and plan.provider.capabilities.supports_summary,
                annotations=reporting.annotations
                and plan.provider.capabilities.supports_annotations,
                max_annotations=reporting.max_annotations,
            )
        except (OSError, UTFError) as exc:
            self.log.error("Provider reporting failed (%s): %s", reporter.name, exc)
            return ReportingError(f"provider reporting failed: {exc}")
        finally:
            self._timings["publish"] = self.clock() - started
        for note in notes:
            self.log.debug("%s", note)
        return None

    def _finalize_best_effort(self, plan: RunPlan, result: CIRunResult) -> None:
        """After a crash: keep whatever evidence exists and write the report."""
        layout = plan.layout
        if layout is None:
            return
        try:
            result.artifacts_dir = str(layout.directory)
            result.artifacts = layout.inventory()
            self._write_json_report(layout, result)
        except OSError:  # pragma: no cover - nothing more we can do
            self.log.warning("Could not write report.json after failure")

    # -- results ------------------------------------------------------------------------------

    def _new_result(
        self, plan: RunPlan, *, status: CIRunStatus = CIRunStatus.NOT_RUN
    ) -> CIRunResult:
        return CIRunResult(
            run_id=self.run_id,
            status=status,
            provider=plan.provider.name,
            suite=plan.suite_name,
            workers=plan.workers,
            strategy=plan.strategy,
            retry=plan.retry_summary,
            selection=plan.describe_selection(),
            context=plan.context,
            artifacts_dir=str(plan.layout.directory) if plan.layout is not None else None,
        )

    def _error_result(
        self,
        plan: RunPlan | None,
        category: FailureCategory,
        message: str,
        exit_code: ExitCode,
    ) -> CIRunResult:
        if plan is not None:
            result = self._new_result(plan)
        else:
            result = CIRunResult(
                run_id=self.run_id,
                status=CIRunStatus.ERROR,
                provider="unknown",
                context=CIContext(provider="unknown", display_name="Unknown"),
            )
        result.status = (
            CIRunStatus.CANCELLED if exit_code == ExitCode.CANCELLED else CIRunStatus.ERROR
        )
        result.error = message
        result.error_category = category
        if plan is not None and not result.tests:
            result.tests = [
                self._classify(
                    plan,
                    TestResult(
                        test_id=u.test.id,
                        name=u.test.name,
                        feature=u.test.feature,
                        platform=u.platform,
                        status=TestStatus.SKIPPED,
                        error=message,
                    ),
                    None,
                    True,
                )
                for u in plan.units
            ]
            self._apply_policy(plan, result, run_failed=message)
        return result

    # -- presentation ------------------------------------------------------------------------

    def _attach_run_log(self, layout: CIArtifactLayout) -> logging.Handler:
        """Write a DEBUG-level JSON run log regardless of console verbosity."""
        from argus.logging.setup import _JsonFormatter, _RedactingFilter

        layout.logs_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(layout.log_file, encoding="utf-8")
        handler.setFormatter(_JsonFormatter())
        handler.addFilter(_RedactingFilter())
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger("argus")
        # Console handlers keep their effective verbosity; the logger itself
        # opens up so the file receives everything.
        for existing in root.handlers:
            if existing.level == logging.NOTSET:
                existing.setLevel(root.level or logging.INFO)
        self._previous_log_level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        return handler

    def _print_header(self, plan: RunPlan, *, dry: bool = False) -> None:
        ctx = plan.context
        title = "Argus CI Dry Run" if dry else "Argus CI"
        self.console.print(f"\n[bold]{title}[/bold]\n{'─' * len(title)}\n")
        rows: list[tuple[str, str]] = [("Provider", ctx.display_name)]
        if plan.suite_name:
            rows.append(("Suite", plan.suite_name))
        if ctx.branch:
            rows.append(("Branch", ctx.branch))
        if ctx.short_commit:
            rows.append(("Commit", ctx.short_commit))
        if ctx.pull_request:
            rows.append(("PR", f"#{ctx.pull_request}"))
        selection = plan.filters.describe()
        if selection:
            rows.append(("Filters", ", ".join(f"{k}={v}" for k, v in selection.items())))
        rows.append(("Tests", str(len(plan.units))))
        rows.append(("Workers", f"{plan.workers} ({plan.strategy})"))
        rows.append(
            (
                "Retries",
                f"{plan.retry_summary.max_attempts} attempt(s) on "
                f"{', '.join(plan.retry_summary.on)}"
                if plan.retry_summary.enabled
                else "disabled",
            )
        )
        rows.append(("Run ID", self.run_id))
        width = max(len(k) for k, _ in rows) + 1
        for key, value in rows:
            self.console.print(f"{escape(key + ':'):<{width + 1}} {escape(value)}")
        self.log.info(
            "CI run %s: provider=%s suite=%s tests=%d workers=%d retry=%s",
            self.run_id,
            plan.provider.name,
            plan.suite_name or "-",
            len(plan.units),
            plan.workers,
            plan.retry_summary.max_attempts if plan.retry_summary.enabled else "off",
        )
        for note in plan.schedule.notes:
            self.console.print(f"[yellow]note:[/yellow] {escape(note)}")
        self.console.print()
        if not dry:
            self.console.print("Running tests...\n")

    def _print_summary(
        self, result: CIRunResult, plan: RunPlan | None, exit_code: ExitCode
    ) -> None:
        self.log.info(
            "CI run %s: %s (exit %d) passed=%d failed=%d flaky=%d not_run=%d artifacts=%s",
            result.run_id,
            result.status.value,
            int(exit_code),
            result.passed_count,
            result.failed_count + result.errored_count,
            result.flaky_count,
            result.not_run_count,
            result.artifacts_dir or "-",
        )
        self.console.print(f"\n{'─' * 28}\n[bold]Argus CI Result[/bold]\n")
        self.console.print(f"Passed:   {result.passed_count}")
        self.console.print(f"Failed:   {result.failed_count + result.errored_count}")
        self.console.print(f"Skipped:  {result.skipped_count}")
        if result.not_run_count:
            self.console.print(f"Not run:  {result.not_run_count}")
        self.console.print(f"Flaky:    {result.flaky_count}")
        if result.known_failure_count:
            self.console.print(f"Known:    {result.known_failure_count}")
        if result.policy.violations:
            self.console.print("\nPolicy:")
            for violation in result.policy.violations:
                colour = "red" if violation.action == "fail" else "yellow"
                self.console.print(
                    f"  [{colour}]{violation.action}[/{colour}] {escape(violation.rule)}: "
                    f"{escape(violation.message)}"
                )
        label = result.status.value.replace("_", " ").upper()
        colour = "green" if exit_code == ExitCode.SUCCESS else "red"
        self.console.print(
            f"\nResult: [bold {colour}]{label}[/bold {colour}] "
            f"(exit {int(exit_code)}: {exit_code.description})"
        )
        if result.artifacts_dir:
            self.console.print(f"\nArtifacts:\n  {result.artifacts_dir}")
        self.console.print()
