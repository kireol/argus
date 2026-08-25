"""Background run execution and run records.

A *run* is one ``TestRunner.run`` invocation. Interfaces that cannot block for
minutes (MCP, a GUI, a REST API) start a run, receive a ``run_id`` and poll:

    start ──> run_id ──> status / events / result / artifacts

Design notes (see docs/mcp.md, "State and scaling"):

- :class:`RunRegistry` executes runs on worker threads and records the
  engine's own :class:`~argus.events.bus.EventBus` events into a bounded,
  MCP-friendly event list. It does not re-implement any engine behavior.
- Records live in a :class:`RunStore`. The default :class:`InMemoryRunStore`
  is process-local; a shared implementation (SQLite/Redis/...) can replace
  it without changing any caller.
- Argus runs tests sequentially and never lets two tests fight over one
  device. The registry enforces the same rule across *interfaces*: a run (or
  an ad-hoc device operation such as a screenshot) claims the devices it
  needs, and a conflicting request is rejected — never queued silently.
"""

from __future__ import annotations

import contextlib
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from argus.engine.filters import TestFilter
from argus.engine.runner import FailurePolicy
from argus.events.bus import EventBus
from argus.events.events import (
    ActionCompleted,
    ActionStarted,
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
from argus.exceptions import UTFError
from argus.logging import get_logger
from argus.models.results import RunResult, TestStatus

_MESSAGE_LIMIT = 300


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"  # the engine returned a RunResult (any RunStatus)
    ERRORED = "errored"  # the engine raised before producing a result


@dataclass(frozen=True)
class RunRequest:
    """What to run — a thin, interface-neutral view of ``RunOptions``."""

    filters: TestFilter = field(default_factory=TestFilter)
    failure_policy: FailurePolicy = field(default_factory=FailurePolicy)
    skip_preflight: bool = False
    save_comparisons: bool = False
    #: Restrict the run to one configured device (its platform is implied).
    device: str | None = None
    #: Free-form label supplied by the caller (shown in run listings).
    label: str | None = None

    def describe(self) -> dict[str, Any]:
        described: dict[str, Any] = dict(self.filters.describe())
        if self.device:
            described["device"] = self.device
        if self.label:
            described["label"] = self.label
        described["stop_on_failure"] = self.failure_policy.stop_on_failure
        if self.failure_policy.max_failures is not None:
            described["max_failures"] = self.failure_policy.max_failures
        if self.skip_preflight:
            described["skip_preflight"] = True
        if self.save_comparisons:
            described["save_comparisons"] = True
        return described


@dataclass(frozen=True)
class RunEvent:
    """Compact, serializable projection of an engine event."""

    seq: int
    timestamp: datetime
    type: str
    data: dict[str, Any]


@dataclass
class RunSummary:
    """Point-in-time snapshot of a run (safe to hand to any interface)."""

    run_id: str
    state: RunState
    request: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    devices: list[str]
    total_tests: int
    executed: int
    passed: int
    failed: int
    skipped: int
    current_test: dict[str, Any] | None
    status: str | None
    stop_reason: str | None
    results_dir: str | None
    error: str | None
    error_category: str | None
    event_count: int
    dropped_events: int

    @property
    def finished(self) -> bool:
        return self.state in (RunState.COMPLETED, RunState.ERRORED)


class RunRecord:
    """Mutable state of one run; every mutation happens under its lock."""

    def __init__(
        self,
        run_id: str,
        request: RunRequest,
        devices: list[str],
        *,
        max_events: int,
    ) -> None:
        self.run_id = run_id
        self.request = request
        self.devices = list(devices)
        self.state = RunState.QUEUED
        self.created_at = datetime.now(UTC)
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.result: RunResult | None = None
        self.error: str | None = None
        self.error_category: str | None = None
        self.results_dir: str | None = None
        self.total_tests = 0
        self.executed = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.current_test: dict[str, Any] | None = None
        self._events: deque[RunEvent] = deque(maxlen=max_events)
        self._seq = 0
        self.dropped_events = 0
        self._lock = threading.Lock()
        self._done = threading.Event()

    # -- event capture (EventBus subscriber) -----------------------------------------

    def on_event(self, event: Event) -> None:
        projected = project_event(event)
        if projected is None:
            return
        event_type, data = projected
        with self._lock:
            self._seq += 1
            if len(self._events) == self._events.maxlen:
                self.dropped_events += 1
            self._events.append(RunEvent(self._seq, event.timestamp, event_type, data))
            self._apply(event)

    def _apply(self, event: Event) -> None:
        if isinstance(event, TestRunStarted):
            self.total_tests = event.total_tests
        elif isinstance(event, TestStarted):
            self.current_test = {
                "test_id": event.test_id,
                "name": event.name,
                "platform": event.platform,
            }
        elif isinstance(event, TestPassed):
            self.passed += 1
            self.executed += 1
            self.current_test = None
        elif isinstance(event, TestFailed):
            self.failed += 1
            self.executed += 1
            self.current_test = None
        elif isinstance(event, TestSkipped):
            self.skipped += 1
            self.current_test = None
        elif isinstance(event, TestRunCompleted):
            self.current_test = None

    # -- lifecycle ---------------------------------------------------------------------

    def mark_running(self) -> None:
        with self._lock:
            self.state = RunState.RUNNING
            self.started_at = datetime.now(UTC)

    def mark_completed(self, result: RunResult) -> None:
        with self._lock:
            self.state = RunState.COMPLETED
            self.result = result
            self.results_dir = result.results_dir or self.results_dir
            self.finished_at = datetime.now(UTC)
            # Authoritative counts come from the result, not the event stream.
            self.executed = result.executed
            self.passed = result.passed_count
            self.failed = result.failed_count
            self.skipped = result.skipped_count
            self.current_test = None
        self._done.set()

    def mark_errored(self, error: str, category: str) -> None:
        with self._lock:
            self.state = RunState.ERRORED
            self.error = error
            self.error_category = category
            self.finished_at = datetime.now(UTC)
            self.current_test = None
        self._done.set()

    def wait(self, timeout: float | None) -> bool:
        return self._done.wait(timeout)

    # -- views ---------------------------------------------------------------------------

    def summary(self) -> RunSummary:
        with self._lock:
            result = self.result
            return RunSummary(
                run_id=self.run_id,
                state=self.state,
                request=self.request.describe(),
                created_at=self.created_at,
                started_at=self.started_at,
                finished_at=self.finished_at,
                devices=list(self.devices),
                total_tests=self.total_tests,
                executed=self.executed,
                passed=self.passed,
                failed=self.failed,
                skipped=self.skipped,
                current_test=dict(self.current_test) if self.current_test else None,
                status=result.status.value if result is not None else None,
                stop_reason=result.stop_reason if result is not None else None,
                results_dir=self.results_dir,
                error=self.error,
                error_category=self.error_category,
                event_count=self._seq,
                dropped_events=self.dropped_events,
            )

    def events(self, *, after: int = 0, limit: int = 100) -> tuple[list[RunEvent], bool]:
        """Events with ``seq > after`` (oldest first) and whether more remain."""
        with self._lock:
            selected = [e for e in self._events if e.seq > after]
        more = len(selected) > limit
        return selected[:limit], more


class RunStore(Protocol):
    """Where run records live. Swap for a shared backend to scale out."""

    def get(self, run_id: str) -> RunRecord | None: ...

    def add(self, record: RunRecord) -> None: ...

    def list_runs(self, *, limit: int) -> list[RunRecord]:
        """Newest first."""
        ...


class InMemoryRunStore:
    """Process-local store; evicts the oldest *finished* runs beyond a cap."""

    def __init__(self, *, max_retained: int = 100) -> None:
        self._records: OrderedDict[str, RunRecord] = OrderedDict()
        self._max_retained = max_retained
        self._lock = threading.Lock()

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def add(self, record: RunRecord) -> None:
        with self._lock:
            self._records[record.run_id] = record
            self._evict()

    def list_runs(self, *, limit: int) -> list[RunRecord]:
        with self._lock:
            records = list(self._records.values())
        records.reverse()
        return records[:limit]

    def _evict(self) -> None:
        excess = len(self._records) - self._max_retained
        if excess <= 0:
            return
        for run_id, record in list(self._records.items()):
            if excess <= 0:
                break
            if record.state in (RunState.COMPLETED, RunState.ERRORED):
                del self._records[run_id]
                excess -= 1


class RunConflictError(UTFError):
    """A run or device operation would collide with an active run."""


RunExecutor = Callable[[RunRecord, EventBus], RunResult]


class RunRegistry:
    """Starts runs in the background and arbitrates device usage."""

    def __init__(
        self,
        executor: RunExecutor,
        *,
        store: RunStore | None = None,
        max_concurrent_runs: int = 1,
        max_events: int = 2000,
        max_retained_runs: int = 100,
    ) -> None:
        self._executor = executor
        self._store = store or InMemoryRunStore(max_retained=max_retained_runs)
        self._max_concurrent = max_concurrent_runs
        self._max_events = max_events
        self._lock = threading.Lock()
        self._active: dict[str, RunRecord] = {}
        self._claims: dict[str, str] = {}  # device name -> holder description
        self._threads: dict[str, threading.Thread] = {}
        self.log = get_logger("argus.service.runs")

    @property
    def store(self) -> RunStore:
        return self._store

    # -- device arbitration ------------------------------------------------------------

    def _check_free(self, devices: list[str]) -> None:
        for name in devices:
            holder = self._claims.get(name)
            if holder is not None:
                raise RunConflictError(
                    f"Device {name!r} is busy ({holder}).",
                    remediation="Wait for the active operation to finish (poll its run_id "
                    "with argus_get_run) and retry.",
                )

    @contextlib.contextmanager
    def claim(self, devices: list[str], holder: str) -> Iterator[None]:
        """Reserve devices for an ad-hoc operation (screenshot, preflight, probe)."""
        with self._lock:
            self._check_free(devices)
            for name in devices:
                self._claims[name] = holder
        try:
            yield
        finally:
            with self._lock:
                for name in devices:
                    if self._claims.get(name) == holder:
                        del self._claims[name]

    def busy_devices(self) -> dict[str, str]:
        with self._lock:
            return dict(self._claims)

    # -- runs ---------------------------------------------------------------------------

    def start(self, request: RunRequest, devices: list[str]) -> RunRecord:
        run_id = f"run-{secrets.token_hex(6)}"
        record = RunRecord(run_id, request, devices, max_events=self._max_events)
        holder = f"run {run_id}"
        with self._lock:
            if len(self._active) >= self._max_concurrent:
                active = ", ".join(sorted(self._active))
                raise RunConflictError(
                    f"Maximum concurrent runs reached ({self._max_concurrent}); "
                    f"active: {active}.",
                    remediation="Poll the active run with argus_get_run and start this "
                    "run when it finishes, or raise mcp.limits.max_concurrent_runs.",
                )
            self._check_free(devices)
            for name in devices:
                self._claims[name] = holder
            self._active[run_id] = record
            self._store.add(record)
            thread = threading.Thread(
                target=self._execute, args=(record,), name=f"argus-{run_id}", daemon=True
            )
            self._threads[run_id] = thread
            thread.start()
        return record

    def _execute(self, record: RunRecord) -> None:
        events = EventBus()
        events.subscribe(record.on_event)
        record.mark_running()
        self.log.info("Run started", extra={"run_id": record.run_id, "operation": "run"})
        started = time.monotonic()
        try:
            result = self._executor(record, events)
        except UTFError as exc:
            record.mark_errored(str(exc), _categorize(exc))
        except Exception as exc:  # noqa: BLE001 - a crashed run must still be reported
            self.log.exception("Run %s crashed", record.run_id)
            record.mark_errored(f"{type(exc).__name__}: {exc}", "error")
        else:
            record.mark_completed(result)
        finally:
            with self._lock:
                self._active.pop(record.run_id, None)
                self._threads.pop(record.run_id, None)
                holder = f"run {record.run_id}"
                for name in list(self._claims):
                    if self._claims[name] == holder:
                        del self._claims[name]
            self.log.info(
                "Run finished in %.1fs (%s)",
                time.monotonic() - started,
                record.state.value,
                extra={"run_id": record.run_id, "operation": "run"},
            )

    def get(self, run_id: str) -> RunRecord | None:
        return self._store.get(run_id)

    def list_runs(self, *, limit: int = 50) -> list[RunRecord]:
        return self._store.list_runs(limit=limit)

    def active(self) -> list[RunRecord]:
        with self._lock:
            return list(self._active.values())

    def wait_all(self, timeout: float | None = None) -> None:
        """Block until every active run finishes (used on shutdown and in tests)."""
        with self._lock:
            threads = list(self._threads.values())
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in threads:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            thread.join(remaining)


# -- event projection -----------------------------------------------------------------------


def _clip(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= _MESSAGE_LIMIT else text[: _MESSAGE_LIMIT - 1] + "…"


def project_event(event: Event) -> tuple[str, dict[str, Any]] | None:
    """Reduce an engine event to ``(type, compact data)``; ``None`` to skip it."""
    if isinstance(event, TestRunStarted):
        return "run_started", {"total_tests": event.total_tests, "filters": event.filters}
    if isinstance(event, PreflightStarted):
        return "preflight_started", {"total_checks": event.total_checks}
    if isinstance(event, PreflightCheckCompleted):
        check = event.result
        return "preflight_check", {
            "name": check.name,
            "passed": check.passed,
            "required": check.required,
            "target": check.target,
            "error": _clip(check.error),
        }
    if isinstance(event, PreflightCompleted):
        return "preflight_completed", {
            "passed": event.passed,
            "failed_checks": [r.name for r in event.results if not r.passed and r.required],
        }
    if isinstance(event, TestStarted):
        return "test_started", {
            "test_id": event.test_id,
            "name": event.name,
            "feature": event.feature,
            "platform": event.platform,
        }
    if isinstance(event, ActionStarted):
        return "action_started", {
            "test_id": event.test_id,
            "action": event.action,
            "step_index": event.step_index,
        }
    if isinstance(event, ActionCompleted):
        step = event.result
        return "action_completed", {
            "test_id": event.test_id,
            "action": event.action,
            "step_index": event.step_index,
            "passed": step.passed,
            "duration_ms": round(step.duration * 1000),
            "message": _clip(step.message),
            "failure_category": step.failure_category,
        }
    if isinstance(event, (TestPassed, TestFailed, TestSkipped)):
        test = event.result
        kind = {
            TestStatus.PASSED: "test_passed",
            TestStatus.FAILED: "test_failed",
            TestStatus.ERROR: "test_failed",
            TestStatus.SKIPPED: "test_skipped",
        }[test.status]
        return kind, {
            "test_id": test.test_id,
            "platform": test.platform,
            "status": test.status.value,
            "duration_ms": round(test.duration * 1000),
            "failure_category": test.failure_category,
            "error": _clip(test.error),
            "attempts": test.attempts,
        }
    if isinstance(event, TestRunCompleted):
        run = event.result
        return "run_completed", {
            "status": run.status.value,
            "executed": run.executed,
            "passed": run.passed_count,
            "failed": run.failed_count,
            "skipped": run.skipped_count,
            "duration_ms": round(run.duration * 1000),
            "stop_reason": run.stop_reason,
        }
    return None


def _categorize(exc: UTFError) -> str:
    name = type(exc).__name__
    mapping = {
        "TimeoutExceededError": "timeout",
        "DeviceConnectionError": "device_connection",
        "ScreenshotError": "screenshot",
        "BackendError": "backend",
        "ConfigurationError": "configuration",
        "TestDefinitionError": "test_definition",
        "PreflightError": "preflight",
    }
    return mapping.get(name, "error")
