"""Test runner — the engine's public service API.

The CLI is one client of this class; a future GUI is another. It exposes::

    runner = TestRunner(config)
    result = runner.run(filters=TestFilter(features=["movies"]))

and publishes events on its bus throughout the run.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.artifacts.manager import ArtifactManager
from argus.config.models import AppConfig
from argus.engine.context import TestContext
from argus.engine.filters import TestFilter
from argus.engine.loader import TestSuite, load_suite
from argus.engine.metrics import MetricsSampler
from argus.engine.session import RunSession
from argus.events.bus import EventBus
from argus.events.events import (
    ActionCompleted,
    ActionStarted,
    FeatureSetupCompleted,
    FeatureSetupStarted,
    FeatureTeardownCompleted,
    FeatureTeardownStarted,
    SuiteSetupCompleted,
    SuiteSetupStarted,
    SuiteTeardownCompleted,
    SuiteTeardownStarted,
    TestFailed,
    TestPassed,
    TestRunCompleted,
    TestRunStarted,
    TestSkipped,
    TestStarted,
)
from argus.exceptions import ConfigurationError, TestDefinitionError, UTFError
from argus.logging import get_logger
from argus.models.metrics import merge_metrics_reports
from argus.models.results import (
    AttemptRecord,
    RunResult,
    RunStatus,
    StepResult,
    TestResult,
    TestStatus,
)
from argus.models.test_definition import (
    FeatureDefinition,
    Step,
    SuiteDefinition,
    TestDefinition,
)
from argus.preflight.checks import build_preflight_checks
from argus.preflight.runner import run_preflight
from argus.utilities.variables import expand_variables

_EXCEPTION_CATEGORIES: list[tuple[str, str]] = [
    ("TimeoutExceededError", "timeout"),
    ("DeviceConnectionError", "device_connection"),
    ("ScreenshotError", "screenshot"),
    ("BackendError", "backend"),
]


@dataclass
class FailurePolicy:
    """Centralized failure behavior (spec §24)."""

    stop_on_failure: bool = True
    max_failures: int | None = None

    def should_stop(self, failures: int) -> tuple[bool, str | None]:
        if self.stop_on_failure and failures > 0:
            return True, "stop-on-failure enabled"
        if self.max_failures is not None and failures >= self.max_failures:
            return True, f"maximum failures reached ({self.max_failures})"
        return False, None


@dataclass(frozen=True)
class RetryOverride:
    """Run-level retry policy layered over each test's own ``retry`` block.

    ``max_attempts`` is the *total* number of attempts (1 = no retry). The
    effective policy for a test is the more generous of the two: the larger
    attempt count and the union of retryable categories. Categories use the
    engine's failure-category names (``timeout``, ``device_connection``,
    ``backend``, ``screenshot``).
    """

    max_attempts: int = 1
    categories: frozenset[str] = frozenset()


@dataclass
class RunOptions:
    filters: TestFilter = field(default_factory=TestFilter)
    failure_policy: FailurePolicy = field(default_factory=FailurePolicy)
    skip_preflight: bool = False
    #: 1-based ordinal in the filtered suite to start at (``--skip-to``).
    skip_to: int | None = None
    #: Skip the configuration ``setup`` commands (a caller already ran them).
    skip_setup: bool = False
    #: Run-level retry policy (CI); ``None`` keeps per-test ``retry`` only.
    retry: RetryOverride | None = None
    #: Cooperative cancellation: when set, no further test is started and the
    #: run finishes with ``RunStatus.CANCELLED`` (remaining tests are skipped).
    cancel: threading.Event | None = None
    #: Pin the artifact run directory instead of a timestamped ``results.dir`` entry.
    results_dir: Path | None = None


class TestRunner:
    def __init__(self, config: AppConfig, events: EventBus | None = None) -> None:
        self.config = config
        self.events = events or EventBus()
        self.log = get_logger("argus.runner")

    # -- loading ---------------------------------------------------------------------

    def load_suite(self) -> TestSuite:
        paths = [self.config.resolve_path(p) for p in self.config.test_paths]
        return load_suite(paths)

    def load(self) -> list[TestDefinition]:
        return self.load_suite().tests

    def select(self, filters: TestFilter) -> list[TestDefinition]:
        return filters.apply(self.load())

    @staticmethod
    def _apply_skip_to(
        tests: list[TestDefinition], skip_to: int
    ) -> tuple[list[TestDefinition], int]:
        """Drop tests before the 1-based ``skip_to`` ordinal.

        Returns ``(remaining_tests, start_index)``. ``start_index`` matches the
        console ``i/N`` numbering from a full run of the same filtered suite.
        """
        if skip_to < 1:
            raise TestDefinitionError(
                f"--skip-to must be >= 1 (got {skip_to}).",
                remediation="Pass a 1-based test number, e.g. --skip-to 68.",
            )
        if not tests:
            raise TestDefinitionError(
                f"--skip-to {skip_to} requested but no tests are selected.",
                remediation="Relax filters or omit --skip-to.",
            )
        if skip_to > len(tests):
            raise TestDefinitionError(
                f"--skip-to {skip_to} is past the end of the selected suite "
                f"({len(tests)} tests).",
                remediation=f"Use a value between 1 and {len(tests)}.",
            )
        return tests[skip_to - 1 :], skip_to

    # -- main entry point ---------------------------------------------------------------

    def run(self, options: RunOptions | None = None) -> RunResult:
        options = options or RunOptions()
        started = time.monotonic()

        suite = self.load_suite()
        tests = options.filters.apply(suite.tests)
        total_tests = len(tests)
        start_index = 1
        if options.skip_to is not None:
            tests, start_index = self._apply_skip_to(tests, options.skip_to)

        run_result = RunResult(status=RunStatus.PASSED)
        filters_desc = options.filters.describe()
        if options.skip_to is not None:
            filters_desc["skip_to"] = options.skip_to
        self.events.publish(
            TestRunStarted(
                total_tests=total_tests,
                filters=filters_desc,
                start_index=start_index,
            )
        )

        with RunSession(self.config, self.events) as session:
            artifacts = ArtifactManager(
                self.config.results,
                Path(self.config.root_dir or "."),
                run_dir=options.results_dir,
            )

            # -- pre-flight -----------------------------------------------------------
            device_names = self.device_names_for(tests, options.filters)
            if not options.skip_preflight:
                checks = build_preflight_checks(session, tests, device_names)
                results, passed = run_preflight(checks, self.events)
                run_result.preflight = results
                if not passed:
                    run_result.status = RunStatus.PREFLIGHT_FAILED
                    run_result.duration = time.monotonic() - started
                    self.events.publish(TestRunCompleted(result=run_result))
                    return run_result

            if self.config.setup and not options.skip_setup:
                ok, setup_error = self.run_setup()
                if not ok:
                    run_result.status = RunStatus.SETUP_FAILED
                    run_result.stop_reason = setup_error
                    run_result.duration = time.monotonic() - started
                    self.events.publish(TestRunCompleted(result=run_result))
                    return run_result

            # -- execution --------------------------------------------------------------
            failures = 0
            stopped = False
            cancelled = False
            plan = [
                (test, platform)
                for test in tests
                for platform in self._platforms_for(test, options.filters, session)
            ]
            lifecycle = _FeatureLifecycle(self, session, artifacts, suite, plan)
            suite_lifecycle = _SuiteLifecycle(self, session, artifacts, suite.lifecycle)
            suite_error: str | None = None
            try:
                if tests:
                    suite_error = suite_lifecycle.setup()
                for test in tests:
                    if suite_error is not None:
                        for platform in self._platforms_for(test, options.filters, session):
                            run_result.tests.append(
                                self._suite_setup_failed(test, platform, suite_error)
                            )
                        continue
                    if not stopped and self._cancel_requested(options):
                        cancelled = True
                        stopped = True
                        run_result.stopped_early = True
                        run_result.stop_reason = "cancelled"
                    if stopped:
                        skip_reason = "cancelled" if cancelled else "run stopped early"
                        run_result.tests.append(self._skipped(test, skip_reason))
                        self.events.publish(TestSkipped(result=run_result.tests[-1]))
                        continue

                    for platform in self._platforms_for(test, options.filters, session):
                        setup_error = lifecycle.before(test, platform)
                        if setup_error is None:
                            result = self._run_test_with_retries(
                                session, artifacts, test, platform, options
                            )
                        else:
                            result = self._feature_setup_failed(test, platform, setup_error)
                        run_result.tests.append(result)
                        lifecycle.after(test, platform)
                        if result.status in (TestStatus.FAILED, TestStatus.ERROR):
                            failures += 1
                            stop, reason = options.failure_policy.should_stop(failures)
                            if stop:
                                run_result.stopped_early = True
                                run_result.stop_reason = reason
                                stopped = True
                                break
            finally:
                lifecycle.close()
                if tests:
                    suite_lifecycle.teardown()

            if run_result.tests and artifacts.has_run_dir:
                run_result.results_dir = str(artifacts.run_dir)
            collected = [t.metrics for t in run_result.tests if t.metrics is not None]
            merged = merge_metrics_reports(collected)
            if merged is not None:
                run_result.metrics = merged
                if artifacts.has_run_dir:
                    artifacts.save_run_report(
                        "metrics.json",
                        json.dumps(merged.model_dump(mode="json"), indent=2),
                    )

        run_result.duration = time.monotonic() - started
        if cancelled:
            run_result.status = RunStatus.CANCELLED
        elif run_result.stopped_early:
            run_result.status = RunStatus.STOPPED
        elif run_result.failed_count:
            run_result.status = RunStatus.FAILED
        self.events.publish(TestRunCompleted(result=run_result))
        return run_result

    # -- helpers -------------------------------------------------------------------------

    @staticmethod
    def _cancel_requested(options: RunOptions) -> bool:
        return options.cancel is not None and options.cancel.is_set()

    def run_setup(self) -> tuple[bool, str | None]:
        """Run config ``setup`` commands once after preflight. Returns (ok, error)."""
        self.log.info("Running %d setup command(s)", len(self.config.setup))
        variables = dict(self.config.variables)
        env = {**os.environ}
        for key, value in variables.items():
            if isinstance(key, str) and key.isidentifier() and value is not None:
                env[key] = str(value)

        for index, step in enumerate(self.config.setup, start=1):
            label = step.name or f"setup[{index}]"
            try:
                command = expand_variables(
                    step.command, variables, strict=True, source=f"setup.{label}.command"
                )
                args = expand_variables(
                    step.args, variables, strict=True, source=f"setup.{label}.args"
                )
                cwd = (
                    expand_variables(
                        step.cwd, variables, strict=True, source=f"setup.{label}.cwd"
                    )
                    if step.cwd
                    else None
                )
            except (UTFError, ConfigurationError) as exc:
                return False, str(exc)

            if not isinstance(command, str):
                return False, f"Setup '{label}': command must expand to a string"
            if not isinstance(args, list):
                return False, f"Setup '{label}': args must expand to a list"
            argv = [command, *[str(a) for a in args]]
            display = shlex.join(argv)
            self.log.info("setup: %s — %s", label, display)

            run_cwd: str | None = None
            if cwd is not None:
                run_cwd = str(self.config.resolve_path(str(cwd)))

            try:
                completed = subprocess.run(
                    argv,
                    cwd=run_cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=step.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return False, (
                    f"Setup '{label}' timed out after {step.timeout_seconds:.1f}s: {display}"
                )
            except OSError as exc:
                return False, f"Setup '{label}' failed to start: {display} ({exc})"

            if completed.stdout and completed.stdout.strip():
                self.log.debug("setup stdout (%s):\n%s", label, completed.stdout.rstrip())
            if completed.stderr and completed.stderr.strip():
                self.log.debug("setup stderr (%s):\n%s", label, completed.stderr.rstrip())

            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip() or "(no output)"
                return False, (
                    f"Setup '{label}' exited {completed.returncode}: {display}\n{detail}"
                )

        self.log.info("Setup complete")
        return True, None

    def _before_each_steps(self) -> list[Step]:
        """Config ``before_each`` host commands as ``shell.run`` steps (prepended to setup)."""
        steps: list[Step] = []
        for index, command in enumerate(self.config.before_each, start=1):
            params: dict[str, Any] = {
                "command": command.command,
                "args": list(command.args),
                "timeout": command.timeout,
            }
            if command.cwd is not None:
                params["cwd"] = command.cwd
            steps.append(
                Step.model_validate(
                    {
                        "action": "shell.run",
                        "name": command.name or f"before_each[{index}]",
                        **params,
                    }
                )
            )
        return steps

    def device_names_for(
        self, tests: list[TestDefinition], filters: TestFilter
    ) -> list[str]:
        platforms: set[str] = set(filters.platforms)
        required: list[str] = []
        for test in tests:
            platforms.update(test.platforms)
            required.extend(test.required_devices)
        names: list[str] = []
        for platform in sorted(platforms):
            names.extend(
                name
                for name, dev in sorted(self.config.devices.items())
                if dev.effective_platform == platform and dev.configured
            )
        for name in required:
            if name in self.config.devices and name not in names:
                names.append(name)
        seen: set[str] = set()
        unique: list[str] = []
        for name in names:
            if name not in seen:
                seen.add(name)
                unique.append(name)
        return unique

    def _platforms_for(
        self, test: TestDefinition, filters: TestFilter, session: RunSession
    ) -> list[str | None]:
        """Platforms this test executes on in this run (one execution each).

        A test without a platforms list runs once with no device bound.
        """
        platforms: list[str | None] = list(test.platforms) or [None]
        if filters.platforms:
            platforms = [p for p in platforms if p in filters.platforms]
        runnable = [
            p for p in platforms if p is None or session.devices_for_platform(p)
        ]
        if not runnable and not test.platforms:
            return [None]
        return runnable

    def _feature_setup_failed(
        self, test: TestDefinition, platform: str | None, error: str
    ) -> TestResult:
        """Record a test as failed because its feature's setup failed (not executed)."""
        self.events.publish(
            TestStarted(test_id=test.id, name=test.name, feature=test.feature, platform=platform)
        )
        result = TestResult(
            test_id=test.id,
            name=test.name,
            feature=test.feature,
            platform=platform,
            status=TestStatus.FAILED,
            error=f"Feature setup failed: {error}",
            failure_category="feature_setup",
        )
        self.events.publish(TestFailed(result=result))
        return result

    def _suite_setup_failed(
        self, test: TestDefinition, platform: str | None, error: str
    ) -> TestResult:
        """Record a test as failed because the suite's setup failed (not executed)."""
        self.events.publish(
            TestStarted(test_id=test.id, name=test.name, feature=test.feature, platform=platform)
        )
        result = TestResult(
            test_id=test.id,
            name=test.name,
            feature=test.feature,
            platform=platform,
            status=TestStatus.FAILED,
            error=f"Suite setup failed: {error}",
            failure_category="suite_setup",
        )
        self.events.publish(TestFailed(result=result))
        return result

    def _skipped(self, test: TestDefinition, reason: str) -> TestResult:
        return TestResult(
            test_id=test.id,
            name=test.name,
            feature=test.feature,
            status=TestStatus.SKIPPED,
            error=reason,
        )

    # -- single test execution --------------------------------------------------------------

    def _run_test_with_retries(
        self,
        session: RunSession,
        artifacts: ArtifactManager,
        test: TestDefinition,
        platform: str | None,
        options: RunOptions | None = None,
    ) -> TestResult:
        attempts = test.retry.count + 1
        retry_on: set[str] = set(test.retry.only)
        override = options.retry if options is not None else None
        if override is not None:
            attempts = max(attempts, override.max_attempts)
            retry_on |= set(override.categories)
        history: list[AttemptRecord] = []
        result: TestResult
        for attempt in range(1, attempts + 1):
            result = self._run_test(session, artifacts, test, platform, attempt=attempt)
            result.attempts = attempt
            history.append(
                AttemptRecord(
                    attempt=attempt,
                    status=result.status,
                    duration=result.duration,
                    failure_category=result.failure_category,
                    error=result.error,
                    artifact_dir=result.artifact_dir,
                )
            )
            if attempt > 1:
                # Keep every attempt's evidence; the final result summarizes them.
                result.attempt_history = list(history)
                result.initial_failure = history[0].failure_category
                result.flaky = result.passed
            if result.passed:
                return result
            category = result.failure_category
            cancelled = options is not None and self._cancel_requested(options)
            if attempt < attempts and category in retry_on and not cancelled:
                self.log.warning(
                    "Retrying %s (attempt %d/%d) after %s failure",
                    test.id,
                    attempt + 1,
                    attempts,
                    category,
                )
                continue
            return result
        return result  # pragma: no cover - loop always returns

    def _run_test(
        self,
        session: RunSession,
        artifacts: ArtifactManager,
        test: TestDefinition,
        platform: str | None,
        *,
        attempt: int = 1,
    ) -> TestResult:
        self.events.publish(
            TestStarted(test_id=test.id, name=test.name, feature=test.feature, platform=platform)
        )
        started = time.monotonic()
        artifact_name = test.id if platform is None else f"{test.id}_{platform}"
        if attempt > 1:
            # Retries never overwrite the previous attempt's evidence.
            artifact_name = f"{artifact_name}_attempt{attempt}"
        test_artifacts = artifacts.for_test(artifact_name)

        device = None
        device_name = self._device_name_for(session, test, platform)
        if device_name is not None:
            try:
                device = session.device(device_name)
            except UTFError as exc:
                return self._finish(
                    test,
                    platform,
                    TestResult(
                        test_id=test.id,
                        name=test.name,
                        feature=test.feature,
                        platform=platform,
                        status=TestStatus.ERROR,
                        error=str(exc),
                        failure_category="device_connection",
                        duration=time.monotonic() - started,
                    ),
                    artifacts,
                    test_artifacts,
                )

        context = TestContext(
            config=self.config,
            test=test,
            conditions=session.conditions,
            verifiers=session.verifiers,
            events=self.events,
            artifacts=test_artifacts,
            logger=get_logger(
                "argus.test",
                test_id=test.id,
                test_name=test.name,
                feature=test.feature,
                platform=platform,
                device=device_name,
            ),
            platform=platform,
            device=device,
            backend=session.backend if session.backend_available else None,
            instrumentation=(
                session.instrumentation(device_name) if device_name else None
            ),
            variables={**self.config.variables, **test.parameters},
        )

        result = TestResult(
            test_id=test.id,
            name=test.name,
            feature=test.feature,
            platform=platform,
            status=TestStatus.PASSED,
        )

        sampler: MetricsSampler | None = None
        if self.config.metrics.enabled and device is not None:
            sampler = MetricsSampler(
                device, interval_seconds=self.config.metrics.interval_seconds
            )
            sampler.start()
        try:
            setup_steps = [*self._before_each_steps(), *test.setup]
            failed_step = self._run_steps(session, context, setup_steps, result, "setup")
            if failed_step is None:
                failed_step = self._run_steps(session, context, test.steps, result, "steps")
        finally:
            # Teardown always runs, even after failures.
            self._run_steps(
                session, context, test.teardown, result, "teardown", record_failure=False
            )
            if sampler is not None:
                result.metrics = sampler.stop()

        result.duration = time.monotonic() - started
        return self._finish(test, platform, result, artifacts, test_artifacts, context)

    @staticmethod
    def _device_name_for(
        session: RunSession, test: TestDefinition, platform: str | None
    ) -> str | None:
        if platform is None:
            return None
        names = [
            n for n in test.required_devices if n in session.config.devices
        ] or session.devices_for_platform(platform)
        return names[0] if names else None

    def _run_steps(
        self,
        session: RunSession,
        context: TestContext,
        steps: list[Step],
        result: TestResult,
        phase: str,
        *,
        record_failure: bool = True,
    ) -> StepResult | None:
        """Execute steps; returns the failing StepResult, if any."""
        for index, step in enumerate(steps):
            self.events.publish(
                ActionStarted(test_id=context.test.id, action=step.action, step_index=index)
            )
            step_started = time.monotonic()
            try:
                action = session.actions.get(step.action)
                # Any step other than verify invalidates a pending wait_until reuse
                # (verify itself pops/consumes the marker).
                if step.action != "verify":
                    context.state.pop("_reuse_wait_verify", None)
                params = context.expand(step.params)
                outcome = action.execute(context, params)
                step_result = StepResult(
                    action=step.action,
                    name=step.name,
                    passed=outcome.passed,
                    duration=time.monotonic() - step_started,
                    message=outcome.message,
                    failure_category=outcome.failure_category,
                    verification=outcome.verification,
                    details=outcome.details,
                )
            except UTFError as exc:
                step_result = StepResult(
                    action=step.action,
                    name=step.name,
                    passed=False,
                    duration=time.monotonic() - step_started,
                    error=str(exc),
                    message=str(exc),
                    failure_category=_categorize(exc),
                )
            except Exception as exc:  # noqa: BLE001 - engine must survive action crashes
                step_result = StepResult(
                    action=step.action,
                    name=step.name,
                    passed=False,
                    duration=time.monotonic() - step_started,
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}",
                    message=f"Unexpected error in action {step.action!r}: {exc}",
                    failure_category="error",
                )

            result.steps.append(step_result)
            self.events.publish(
                ActionCompleted(
                    test_id=context.test.id,
                    action=step.action,
                    step_index=index,
                    result=step_result,
                )
            )
            if not step_result.passed and record_failure:
                result.status = (
                    TestStatus.ERROR
                    if step_result.failure_category == "error"
                    else TestStatus.FAILED
                )
                result.error = step_result.message
                result.failure_category = step_result.failure_category
                return step_result
        return None

    def _finish(
        self,
        test: TestDefinition,
        platform: str | None,
        result: TestResult,
        artifacts: ArtifactManager,
        test_artifacts: object,
        context: TestContext | None = None,
    ) -> TestResult:
        from argus.artifacts.manager import TestArtifacts

        assert isinstance(test_artifacts, TestArtifacts)
        if context is not None:
            save_comparisons = self.config.results.save_comparison_images
            if not result.passed or save_comparisons:
                self._save_image_comparisons(
                    context,
                    result,
                    test_artifacts,
                    all_steps=save_comparisons,
                )
                self._save_text_evidence(
                    context,
                    result,
                    test_artifacts,
                    all_steps=save_comparisons,
                )
            if not result.passed:
                self._save_failure_diagnostics(context, result, test_artifacts)
            if result.metrics is not None:
                test_artifacts.save_json(
                    "metrics.json", result.metrics.model_dump(mode="json")
                )
        artifacts.finalize_test(test_artifacts, passed=result.passed)
        result.artifact_dir = (
            str(test_artifacts.directory) if test_artifacts.directory.exists() else None
        )
        event = TestPassed(result=result) if result.passed else TestFailed(result=result)
        self.events.publish(event)
        return result

    def _save_image_comparisons(
        self,
        context: TestContext,
        result: TestResult,
        artifacts: object,
        *,
        all_steps: bool,
    ) -> None:
        """Save actual/expected/diff for image-based verification steps."""
        from argus.artifacts.manager import TestArtifacts
        from argus.verifiers.image import diff_image

        assert isinstance(artifacts, TestArtifacts)
        if (
            not result.passed
            and not self.config.results.save_screenshots_on_failure
            and not all_steps
        ):
            return

        try:
            observation = context.last_observation
            if observation is None and context.device is not None:
                try:
                    observation = context.observe()
                except UTFError:
                    observation = None

            def _images_from(ver: object) -> list[str]:
                found: list[str] = []
                if ver is None:
                    return found
                details = getattr(ver, "details", None)
                if details is None and isinstance(ver, dict):
                    details = ver.get("details") or ver
                if not isinstance(details, dict):
                    return found
                if details.get("image"):
                    found.append(str(details["image"]))
                for child in details.get("children") or []:
                    found.extend(_images_from(child))
                return found

            image_steps = [
                s
                for s in result.steps
                if s.verification is not None
                and s.verification.details.get("image")
            ]
            if not image_steps:
                # wait_until all/any: the step verifier is the composite; image
                # names live on children. Always keep the screenshot so a miss
                # is diagnosable.
                if observation is not None and (not result.passed or all_steps):
                    artifacts.save_image("actual.png", observation.image)
                    artifacts.saved_comparisons = True
                seen: set[str] = set()
                for step in result.steps:
                    for image_name in _images_from(step.verification):
                        if image_name in seen:
                            continue
                        seen.add(image_name)
                        if context.verifiers.assets.exists(image_name):
                            artifacts.save_image(
                                f"{Path(image_name).stem}_expected.png",
                                context.verifiers.assets.load_array(image_name),
                            )
                return

            if all_steps:
                targets = image_steps
            else:
                failed = next((s for s in image_steps if not s.passed), None)
                targets = [failed] if failed is not None else [image_steps[-1]]

            canonical_written = False
            for step in targets:
                assert step.verification is not None
                image_name = str(step.verification.details["image"])
                stem = Path(image_name).stem
                reference = None
                if context.verifiers.assets.exists(image_name):
                    reference = context.verifiers.assets.load_array(image_name)
                diff = None
                if observation is not None and reference is not None:
                    diff = diff_image(observation, reference)
                if all_steps and len(targets) > 1:
                    prefix = stem
                    also_canonical = (not step.passed) or (
                        step is targets[-1] and not canonical_written
                    )
                else:
                    prefix = ""
                    also_canonical = True
                if also_canonical:
                    canonical_written = True
                artifacts.save_comparison_set(
                    actual=observation.image if observation is not None else None,
                    expected=reference,
                    diff=diff,
                    prefix=prefix,
                    also_canonical=also_canonical,
                )
        except Exception:  # noqa: BLE001 - diagnostics must never mask the real failure
            self.log.exception(
                "Failed to save comparison images for %s", result.test_id
            )

    def _save_text_evidence(
        self,
        context: TestContext,
        result: TestResult,
        artifacts: object,
        *,
        all_steps: bool,
    ) -> None:
        """Evidence for OCR/text verifications: the screen, the recognised text, the region.

        Writes ``actual.png`` (once), ``ocr.txt`` (one block per text step) and, when a
        step used a ``region``, ``<n>_ocr_region.png`` with that region outlined. Marks
        the artifact directory as holding comparisons so it is retained on success when
        ``results.save_comparison_images`` is on.
        """
        from argus.artifacts.manager import TestArtifacts

        assert isinstance(artifacts, TestArtifacts)
        if (
            not result.passed
            and not self.config.results.save_screenshots_on_failure
            and not all_steps
        ):
            return
        # Text verifiers record the OCR output; that marks a step as a text check
        # whatever the condition was called (text_present, text_not_present, ...).
        text_steps = [
            (index, s)
            for index, s in enumerate(result.steps, start=1)
            if s.verification is not None and "extracted_text" in s.verification.details
        ]
        if not text_steps:
            return
        if not all_steps:
            failed = [(i, s) for i, s in text_steps if not s.passed]
            text_steps = failed or text_steps[-1:]
        try:
            observation = context.last_observation
            if observation is None and context.device is not None:
                try:
                    observation = context.observe()
                except UTFError:
                    observation = None
            if observation is not None and not (artifacts.directory / "actual.png").exists():
                artifacts.save_image("actual.png", observation.image)
            blocks: list[str] = []
            for index, step in text_steps:
                assert step.verification is not None
                details = step.verification.details
                expected = details.get("expected_text", details.get("expected_absent", ""))
                region = details.get("region")
                blocks.append(
                    f"step {index}: {step.name or step.action} — {step.verification.verifier} "
                    f"{'PASSED' if step.passed else 'FAILED'}\n"
                    f"expected: {expected!r}\n"
                    + (f"region: {region}\n" if region else "")
                    + f"message: {step.verification.message}\n"
                    f"extracted text:\n{details.get('extracted_text', '')}\n"
                )
                if observation is not None and isinstance(region, dict):
                    artifacts.save_image(
                        f"{index}_ocr_region.png", _outline_region(observation.image, region)
                    )
            artifacts.save_text("ocr.txt", "\n".join(blocks))
            artifacts.saved_comparisons = True
        except Exception:  # noqa: BLE001 - evidence must never mask the real result
            self.log.exception("Failed to save text evidence for %s", result.test_id)

    def _save_failure_diagnostics(
        self, context: TestContext, result: TestResult, artifacts: object
    ) -> None:
        """On failure: logs, instrumentation, metadata (images via comparisons)."""
        from argus.artifacts.manager import TestArtifacts

        assert isinstance(artifacts, TestArtifacts)
        try:
            # If comparisons were disabled or produced nothing, still capture a screen.
            if (
                self.config.results.save_screenshots_on_failure
                and not artifacts.saved_comparisons
            ):
                observation = context.last_observation
                if observation is None and context.device is not None:
                    try:
                        observation = context.observe()
                    except UTFError:
                        observation = None
                if observation is not None:
                    artifacts.save_image("actual.png", observation.image)

            if context.device is not None and context.device.capabilities.supports_logs:
                with contextlib.suppress(UTFError):
                    artifacts.save_text("logs.txt", context.device.get_logs())

            if context.instrumentation is not None:
                try:
                    status = context.instrumentation.status()
                    result.instrumentation_state = status.model_dump(exclude_none=True)
                    artifacts.save_json(
                        "instrumentation.json", result.instrumentation_state
                    )
                except UTFError:
                    pass

            artifacts.save_json("metadata.json", result.model_dump(mode="json"))
        except Exception:  # noqa: BLE001 - diagnostics must never mask the real failure
            self.log.exception("Failed to save failure diagnostics for %s", result.test_id)


def _outline_region(image: Any, region: dict[str, Any]) -> Any:
    """A copy of ``image`` with the OCR region outlined (evidence for text checks)."""
    from PIL import ImageDraw

    annotated = image.convert("RGB").copy()
    draw = ImageDraw.Draw(annotated)
    x, y = int(region["x"]), int(region["y"])
    right = x + int(region.get("width", 1))
    bottom = y + int(region.get("height", 1))
    for inset in range(3):
        draw.rectangle([x - inset, y - inset, right + inset, bottom + inset], outline="#ff2d55")
    return annotated


def _categorize(exc: UTFError) -> str:
    name = type(exc).__name__
    for exc_name, category in _EXCEPTION_CATEGORIES:
        if name == exc_name:
            return category
    return "error"


class _FeatureLifecycle:
    """Runs feature ``setup`` before the first selected test of a feature on a
    platform and ``teardown`` after its last one (or when the run ends early)."""

    def __init__(
        self,
        runner: TestRunner,
        session: RunSession,
        artifacts: ArtifactManager,
        suite: TestSuite,
        plan: list[tuple[TestDefinition, str | None]],
    ) -> None:
        self._runner = runner
        self._session = session
        self._artifacts = artifacts
        self._suite = suite
        self._remaining: dict[tuple[str, str | None], int] = {}
        for test, platform in plan:
            key = self._key(test, platform)
            self._remaining[key] = self._remaining.get(key, 0) + 1
        # key -> setup error (None = setup passed); only for features that started
        self._open: dict[tuple[str, str | None], str | None] = {}
        self._first_test: dict[tuple[str, str | None], TestDefinition] = {}
        self._contexts: dict[tuple[str, str | None], TestContext] = {}

    @staticmethod
    def _key(test: TestDefinition, platform: str | None) -> tuple[str, str | None]:
        return (test.feature.strip().lower(), platform)

    def before(self, test: TestDefinition, platform: str | None) -> str | None:
        """Ensure feature setup has run; returns its error message if it failed."""
        feature = self._suite.feature_for(test.feature)
        if feature is None:
            return None
        key = self._key(test, platform)
        if key not in self._open:
            self._first_test[key] = test
            self._open[key] = self._run_phase(feature, test, platform, "setup")
        return self._open[key]

    def after(self, test: TestDefinition, platform: str | None) -> None:
        key = self._key(test, platform)
        self._remaining[key] = self._remaining.get(key, 1) - 1
        if self._remaining[key] <= 0 and key in self._open:
            self._teardown(key, test, platform)

    def close(self) -> None:
        """Tear down every feature still open (run stopped early or crashed)."""
        for key in list(self._open):
            self._teardown(key, self._first_test[key], key[1])

    def _teardown(
        self, key: tuple[str, str | None], test: TestDefinition, platform: str | None
    ) -> None:
        feature = self._suite.feature_for(test.feature)
        self._open.pop(key, None)
        if feature is not None:
            self._run_phase(feature, test, platform, "teardown")
        self._contexts.pop(key, None)
        self._first_test.pop(key, None)

    def _run_phase(
        self,
        feature: FeatureDefinition,
        test: TestDefinition,
        platform: str | None,
        phase: str,
    ) -> str | None:
        """Run the feature's ``phase`` steps; returns an error message on failure."""
        runner = self._runner
        steps = feature.setup if phase == "setup" else feature.teardown
        if not steps:
            return None
        started_event = FeatureSetupStarted if phase == "setup" else FeatureTeardownStarted
        completed_event = (
            FeatureSetupCompleted if phase == "setup" else FeatureTeardownCompleted
        )
        runner.events.publish(started_event(feature=feature.name, platform=platform))
        result = TestResult(
            test_id=_feature_test_id(feature.name),
            name=f"{feature.name} {phase}",
            feature=feature.name,
            platform=platform,
            status=TestStatus.PASSED,
        )
        error: str | None = None
        try:
            key = self._key(test, platform)
            context = self._contexts.get(key)
            if context is None:
                context = self._context(feature, test, platform)
                self._contexts[key] = context
            failed = runner._run_steps(
                self._session, context, steps, result, phase, record_failure=(phase == "setup")
            )
            if failed is not None:
                error = f"{failed.action} — {failed.message or 'step failed'}"
            elif any(not s.passed for s in result.steps):
                error = next(
                    f"{s.action} — {s.message or 'step failed'}"
                    for s in result.steps
                    if not s.passed
                )
        except UTFError as exc:
            error = str(exc)
        if error is not None:
            runner.log.warning("Feature %s %s failed: %s", feature.name, phase, error)
        runner.events.publish(
            completed_event(
                feature=feature.name,
                platform=platform,
                passed=error is None,
                error=error,
                steps=list(result.steps),
            )
        )
        return error

    def _context(
        self, feature: FeatureDefinition, test: TestDefinition, platform: str | None
    ) -> TestContext:
        runner = self._runner
        session = self._session
        device = None
        device_name = runner._device_name_for(session, test, platform)
        if device_name is not None:
            device = session.device(device_name)
        pseudo_test = test.model_copy(
            update={
                "id": _feature_test_id(feature.name),
                "name": f"{feature.name} feature",
                "parameters": {},
            }
        )
        return TestContext(
            config=runner.config,
            test=pseudo_test,
            conditions=session.conditions,
            verifiers=session.verifiers,
            events=runner.events,
            artifacts=self._artifacts.for_test(pseudo_test.id),
            logger=get_logger(
                "argus.feature",
                test_id=pseudo_test.id,
                feature=feature.name,
                platform=platform,
                device=device_name,
            ),
            platform=platform,
            device=device,
            backend=session.backend if session.backend_available else None,
            instrumentation=session.instrumentation(device_name) if device_name else None,
            variables=dict(runner.config.variables),
        )


class _SuiteLifecycle:
    """Runs the suite's ``setup`` once before the first selected test and its
    ``teardown`` once at the very end (after every feature teardown), always."""

    TEST_ID = "suite_setup"

    def __init__(
        self,
        runner: TestRunner,
        session: RunSession,
        artifacts: ArtifactManager,
        definition: SuiteDefinition | None,
    ) -> None:
        self._runner = runner
        self._session = session
        self._artifacts = artifacts
        self._definition = definition
        self._context: TestContext | None = None
        self._started = False

    def setup(self) -> str | None:
        """Run suite setup; returns an error message when it failed."""
        if self._definition is None or self._definition.empty:
            return None
        self._started = True
        return self._run_phase("setup")

    def teardown(self) -> None:
        if not self._started:
            return
        self._started = False
        self._run_phase("teardown")

    def _run_phase(self, phase: str) -> str | None:
        runner = self._runner
        definition = self._definition
        assert definition is not None
        steps = definition.setup if phase == "setup" else definition.teardown
        if not steps:
            return None
        device_name = definition.device
        started_event = SuiteSetupStarted if phase == "setup" else SuiteTeardownStarted
        completed_event = SuiteSetupCompleted if phase == "setup" else SuiteTeardownCompleted
        runner.events.publish(started_event(device=device_name))
        result = TestResult(
            test_id=self.TEST_ID,
            name=f"suite {phase}",
            feature="Suite",
            platform=None,
            status=TestStatus.PASSED,
        )
        error: str | None = None
        try:
            context = self._context or self._context_for(device_name)
            self._context = context
            failed = runner._run_steps(
                self._session, context, steps, result, phase, record_failure=(phase == "setup")
            )
            if failed is not None:
                error = f"{failed.action} — {failed.message or 'step failed'}"
            elif any(not s.passed for s in result.steps):
                error = next(
                    f"{s.action} — {s.message or 'step failed'}"
                    for s in result.steps
                    if not s.passed
                )
        except UTFError as exc:
            error = str(exc)
        if error is not None:
            runner.log.warning("Suite %s failed: %s", phase, error)
        runner.events.publish(
            completed_event(device=device_name, passed=error is None, error=error,
                            steps=list(result.steps))
        )
        return error

    def _context_for(self, device_name: str | None) -> TestContext:
        runner = self._runner
        session = self._session
        device = session.device(device_name) if device_name is not None else None
        pseudo_test = TestDefinition(
            id=self.TEST_ID,
            name="suite lifecycle",
            feature="Suite",
            steps=[Step(action="log", message="suite")],  # type: ignore[call-arg]
        )
        platform = device.platform if device is not None else None
        return TestContext(
            config=runner.config,
            test=pseudo_test,
            conditions=session.conditions,
            verifiers=session.verifiers,
            events=runner.events,
            artifacts=self._artifacts.for_test(self.TEST_ID),
            logger=get_logger(
                "argus.suite", test_id=self.TEST_ID, feature="Suite", platform=platform,
                device=device_name,
            ),
            platform=platform,
            device=device,
            backend=session.backend if session.backend_available else None,
            instrumentation=session.instrumentation(device_name) if device_name else None,
            variables=dict(runner.config.variables),
        )


def _feature_test_id(feature_name: str) -> str:
    slug = "".join(c if c.isalnum() or c in "_-" else "_" for c in feature_name.strip())
    return f"feature_{slug or 'unnamed'}"
