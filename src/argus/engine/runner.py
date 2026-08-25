"""Test runner — the engine's public service API.

The CLI is one client of this class; a future GUI is another. It exposes::

    runner = TestRunner(config)
    result = runner.run(filters=TestFilter(features=["movies"]))

and publishes events on its bus throughout the run.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.artifacts.manager import ArtifactManager
from argus.config.models import AppConfig
from argus.engine.context import TestContext
from argus.engine.filters import TestFilter
from argus.engine.loader import load_tests
from argus.engine.session import RunSession
from argus.events.bus import EventBus
from argus.events.events import (
    ActionCompleted,
    ActionStarted,
    TestFailed,
    TestPassed,
    TestRunCompleted,
    TestRunStarted,
    TestSkipped,
    TestStarted,
)
from argus.exceptions import ConfigurationError, TestDefinitionError, UTFError
from argus.logging import get_logger
from argus.models.results import (
    RunResult,
    RunStatus,
    StepResult,
    TestResult,
    TestStatus,
)
from argus.models.test_definition import Step, TestDefinition
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


@dataclass
class RunOptions:
    filters: TestFilter = field(default_factory=TestFilter)
    failure_policy: FailurePolicy = field(default_factory=FailurePolicy)
    skip_preflight: bool = False
    #: 1-based ordinal in the filtered suite to start at (``--skip-to``).
    skip_to: int | None = None


class TestRunner:
    def __init__(self, config: AppConfig, events: EventBus | None = None) -> None:
        self.config = config
        self.events = events or EventBus()
        self.log = get_logger("argus.runner")

    # -- loading ---------------------------------------------------------------------

    def load(self) -> list[TestDefinition]:
        paths = [self.config.resolve_path(p) for p in self.config.test_paths]
        return load_tests(paths)

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

        tests = self.select(options.filters)
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
                self.config.results, Path(self.config.root_dir or ".")
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

            if self.config.setup:
                ok, setup_error = self._run_setup()
                if not ok:
                    run_result.status = RunStatus.SETUP_FAILED
                    run_result.stop_reason = setup_error
                    run_result.duration = time.monotonic() - started
                    self.events.publish(TestRunCompleted(result=run_result))
                    return run_result

            # -- execution --------------------------------------------------------------
            failures = 0
            stopped = False
            for test in tests:
                if stopped:
                    run_result.tests.append(self._skipped(test, "run stopped early"))
                    self.events.publish(TestSkipped(result=run_result.tests[-1]))
                    continue

                for platform in self._platforms_for(test, options.filters, session):
                    result = self._run_test_with_retries(
                        session, artifacts, test, platform
                    )
                    run_result.tests.append(result)
                    if result.status in (TestStatus.FAILED, TestStatus.ERROR):
                        failures += 1
                        stop, reason = options.failure_policy.should_stop(failures)
                        if stop:
                            run_result.stopped_early = True
                            run_result.stop_reason = reason
                            stopped = True
                            break

            if run_result.tests and artifacts.has_run_dir:
                run_result.results_dir = str(artifacts.run_dir)

        run_result.duration = time.monotonic() - started
        if run_result.stopped_early:
            run_result.status = RunStatus.STOPPED
        elif run_result.failed_count:
            run_result.status = RunStatus.FAILED
        self.events.publish(TestRunCompleted(result=run_result))
        return run_result

    # -- helpers -------------------------------------------------------------------------

    def _run_setup(self) -> tuple[bool, str | None]:
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
    ) -> TestResult:
        attempts = test.retry.count + 1
        result: TestResult
        for attempt in range(1, attempts + 1):
            result = self._run_test(session, artifacts, test, platform)
            result.attempts = attempt
            if result.passed:
                return result
            category = result.failure_category
            if attempt < attempts and category in test.retry.only:
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
    ) -> TestResult:
        self.events.publish(
            TestStarted(test_id=test.id, name=test.name, feature=test.feature, platform=platform)
        )
        started = time.monotonic()
        test_artifacts = artifacts.for_test(
            test.id if platform is None else f"{test.id}_{platform}"
        )

        device = None
        device_name = None
        if platform is not None:
            names = [
                n for n in test.required_devices if n in session.config.devices
            ] or session.devices_for_platform(platform)
            device_name = names[0] if names else None
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

        result.duration = time.monotonic() - started
        return self._finish(test, platform, result, artifacts, test_artifacts, context)

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
            if not result.passed:
                self._save_failure_diagnostics(context, result, test_artifacts)
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
        if not result.passed and not self.config.results.save_screenshots_on_failure:
            if not all_steps:
                return

        try:
            observation = context.last_observation
            if observation is None and context.device is not None:
                try:
                    observation = context.observe()
                except UTFError:
                    observation = None

            image_steps = [
                s
                for s in result.steps
                if s.verification is not None
                and s.verification.details.get("image")
            ]
            if not image_steps:
                if observation is not None and not result.passed:
                    artifacts.save_image("actual.png", observation.image)
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


def _categorize(exc: UTFError) -> str:
    name = type(exc).__name__
    for exc_name, category in _EXCEPTION_CATEGORIES:
        if name == exc_name:
            return category
    return "error"
