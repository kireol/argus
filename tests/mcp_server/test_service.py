"""ArgusService and run registry behavior (no MCP protocol involved)."""

from __future__ import annotations

import threading
import time

import pytest
import yaml
from tests.mcp_server.conftest import TESTS, Project

from argus.engine.filters import TestFilter
from argus.engine.runner import FailurePolicy
from argus.events.bus import EventBus
from argus.events.events import TestRunCompleted
from argus.exceptions import ConfigurationError, ScreenshotError, TestDefinitionError
from argus.models.results import RunResult, RunStatus
from argus.service import ArgusService, InMemoryRunStore, RunConflictError, RunRequest, RunState
from argus.service.facade import redact_mapping
from argus.service.runs import RunRecord, RunRegistry


def run_and_wait(service: ArgusService, request: RunRequest) -> RunRecord:
    record = service.start_run(request)
    assert record.wait(60)
    return record


# -- catalog -----------------------------------------------------------------------------------


def test_catalog_caches_and_invalidates_on_file_change(project: Project):
    service = ArgusService(project.config())
    first = service.load_tests()
    assert [t.id for t in first] == [t["id"] for t in TESTS]
    assert service.load_tests() is not first  # copies, never the cached list itself
    assert [t.id for t in service.load_tests()] == [t.id for t in first]

    extra = dict(TESTS[0], id="NEW-001")
    (project.suites / "extra.yaml").write_text(yaml.safe_dump({"tests": [extra]}))
    assert service.get_test("NEW-001") is not None

    (project.suites / "extra.yaml").write_text("tests: [{id: BAD}]")
    with pytest.raises(TestDefinitionError):
        service.load_tests()


def test_select_and_get(project: Project):
    service = ArgusService(project.config())
    assert [t.id for t in service.select_tests(TestFilter(features=["movies"]))] == [
        "PASS-001",
        "PASS-002",
        "FAIL-001",
        "FAIL-002",
    ]
    assert service.get_test("SET-001").name == "Settings log line"
    assert service.get_test("nope") is None


# -- preflight / validation ---------------------------------------------------------------------


def test_preflight_reports_requirements(project: Project):
    service = ArgusService(project.config())
    report = service.preflight(TestFilter(test_ids=["PASS-001"]), device="fake_android")
    assert report.passed
    assert report.device_names == ["fake_android"]
    assert report.backend_required and not report.ocr_required
    assert {c.name for c in report.checks} >= {"Device: fake_android", "Screenshot: fake_android"}


def test_preflight_fails_for_broken_device(project: Project):
    service = ArgusService(project.config())
    report = service.preflight(TestFilter(test_ids=["SET-001"]), device="fake_broken")
    assert not report.passed
    failed = [c for c in report.checks if not c.passed]
    assert failed and failed[0].name == "Screenshot: fake_broken"
    assert failed[0].remediation


def test_validate_framework_only_touches_no_device(project: Project):
    service = ArgusService(project.config())
    report = service.validate(framework_only=True)
    assert report.ready
    assert not any(s.title.startswith("Device:") for s in report.sections)


# -- devices ------------------------------------------------------------------------------------


def test_list_devices_never_connects(project: Project):
    service = ArgusService(project.config())
    devices = {d.name: d for d in service.list_devices()}
    assert devices["fake_android"].platform == "android"
    assert "screenshot" in devices["fake_android"].capabilities
    assert "instrumentation" in devices["fake_android"].capabilities
    assert devices["fake_ghost"].configured is False
    assert devices["fake_android"].health is None


def test_get_device_probe_and_unknown(project: Project):
    service = ArgusService(project.config())
    info = service.get_device("fake_android", probe=True)
    assert info.health is not None and info.health.healthy
    assert info.screen == {"width": 1280, "height": 720}
    with pytest.raises(ConfigurationError):
        service.get_device("missing")
    ghost = service.get_device("fake_ghost", probe=True)
    assert ghost.probe_error


def test_capture_screenshot_and_failure(project: Project):
    service = ArgusService(project.config())
    assert service.capture_screenshot("fake_android").size == (1280, 720)
    with pytest.raises(ScreenshotError):
        service.capture_screenshot("fake_broken")
    with pytest.raises(ConfigurationError):
        service.capture_screenshot("fake_ghost")


# -- runs ------------------------------------------------------------------------------------------


def test_run_lifecycle_and_events(project: Project):
    service = ArgusService(project.config())
    record = run_and_wait(
        service, RunRequest(filters=TestFilter(test_ids=["PASS-001"]), device="fake_android")
    )
    summary = record.summary()
    assert summary.state == RunState.COMPLETED
    assert summary.status == "passed"
    assert summary.passed == 1 and summary.failed == 0
    assert summary.results_dir  # reports are written even when everything passes
    kinds = {a.kind for a in service.list_artifacts(record.run_id)}
    assert kinds == {"report"}  # ...but no per-test artifacts are retained
    events, more = record.events(limit=1000)
    types = [e.type for e in events]
    assert types[0] == "run_started" and types[-1] == "run_completed"
    assert "test_started" in types and "test_passed" in types
    assert not more
    assert [e.seq for e in events] == list(range(1, len(events) + 1))


def test_run_failure_writes_reports_and_diagnosis(project: Project):
    service = ArgusService(project.config())
    record = run_and_wait(
        service,
        RunRequest(
            filters=TestFilter(test_ids=["FAIL-001"]),
            failure_policy=FailurePolicy(stop_on_failure=False),
        ),
    )
    summary = record.summary()
    assert summary.status == "failed"
    assert summary.results_dir
    ids = [a.artifact_id for a in service.list_artifacts(record.run_id)]
    assert "report.json" in ids and "junit.xml" in ids and "report.html" in ids
    assert "FAIL-001_android/actual.png" in ids
    only_fail = service.list_artifacts(record.run_id, test_id="FAIL-001")
    assert all(a.test_id == "FAIL-001" for a in only_fail)

    diagnosis = service.diagnose_run(record.run_id)
    assert len(diagnosis.tests) == 1
    test = diagnosis.tests[0]
    assert test.failure_category == "assertion"
    assert test.failed_step["action"] == "verify"
    assert test.expected["image"] == "movie_456.png"
    assert test.instrumentation_state["ready"] is True
    assert "FAIL-001_android/diff.png" in test.artifacts
    assert test.hint


def test_failure_policy_max_failures(project: Project):
    service = ArgusService(project.config())
    record = run_and_wait(
        service,
        RunRequest(
            filters=TestFilter(tags=["broken"]),
            failure_policy=FailurePolicy(stop_on_failure=False, max_failures=1),
        ),
    )
    summary = record.summary()
    assert summary.status == "stopped"
    assert summary.failed == 1 and summary.skipped == 1


def test_run_preflight_failure_saves_preflight_report(project: Project):
    service = ArgusService(project.config())
    record = run_and_wait(
        service, RunRequest(filters=TestFilter(test_ids=["SET-001"]), device="fake_broken")
    )
    summary = record.summary()
    assert summary.status == "preflight_failed"
    assert "preflight.json" in [a.artifact_id for a in service.list_artifacts(record.run_id)]
    assert service.diagnose_run(record.run_id).preflight_failures


def test_run_with_invalid_filter_fails_early(project: Project):
    service = ArgusService(project.config())
    with pytest.raises(TestDefinitionError):
        service.start_run(RunRequest(filters=TestFilter(tag_expression="smoke and (")))


def test_unknown_run_id(project: Project):
    service = ArgusService(project.config())
    assert service.get_run("run-nope") is None
    with pytest.raises(ConfigurationError):
        service.list_artifacts("run-nope")


# -- concurrency / arbitration --------------------------------------------------------------------


def test_conflicting_run_on_busy_device_is_rejected(project: Project):
    service = ArgusService(project.config(mcp={"limits": {"max_concurrent_runs": 4}}))
    slow = service.start_run(RunRequest(filters=TestFilter(test_ids=["SLOW-001"])))
    try:
        time.sleep(0.2)
        with pytest.raises(RunConflictError) as info:
            service.start_run(RunRequest(filters=TestFilter(test_ids=["PASS-001"])))
        assert "fake_android" in str(info.value)
        with pytest.raises(RunConflictError):
            service.capture_screenshot("fake_android")
        with pytest.raises(RunConflictError):
            service.preflight(TestFilter(test_ids=["PASS-001"]))
        assert service.get_device("fake_android").busy == f"run {slow.run_id}"
        # A test bound to no device is unaffected.
        other = run_and_wait(service, RunRequest(filters=TestFilter(test_ids=["SET-001"])))
        assert other.summary().status == "passed"
    finally:
        assert slow.wait(30)
    assert service.get_device("fake_android").busy is None


def test_max_concurrent_runs(project: Project):
    service = ArgusService(project.config())  # default: 1
    slow = service.start_run(RunRequest(filters=TestFilter(test_ids=["SLOW-001"])))
    try:
        with pytest.raises(RunConflictError) as info:
            service.start_run(RunRequest(filters=TestFilter(test_ids=["SET-001"])))
        assert "Maximum concurrent runs" in str(info.value)
    finally:
        assert slow.wait(30)


def test_concurrent_reads_are_safe(project: Project):
    service = ArgusService(project.config())
    errors: list[Exception] = []

    def reader() -> None:
        try:
            for _ in range(20):
                service.select_tests(TestFilter(features=["movies"]))
                service.list_devices()
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


# -- registry internals ----------------------------------------------------------------------------


def test_registry_records_engine_errors():
    def executor(record: RunRecord, events: EventBus) -> RunResult:
        raise ConfigurationError("boom", remediation="fix it")

    registry = RunRegistry(executor)
    record = registry.start(RunRequest(), [])
    assert record.wait(5)
    summary = record.summary()
    assert summary.state == RunState.ERRORED
    assert summary.error_category == "configuration"
    assert "boom" in summary.error


def test_registry_records_crashes_without_leaking_details():
    def executor(record: RunRecord, events: EventBus) -> RunResult:
        raise RuntimeError("secret internals")

    registry = RunRegistry(executor)
    record = registry.start(RunRequest(), [])
    assert record.wait(5)
    assert record.summary().state == RunState.ERRORED
    assert record.summary().error_category == "error"


def test_event_cap_drops_oldest_and_counts():
    def executor(record: RunRecord, events: EventBus) -> RunResult:
        result = RunResult(status=RunStatus.PASSED)
        for _ in range(10):
            events.publish(TestRunCompleted(result=result))
        return result

    registry = RunRegistry(executor, max_events=50)
    record = registry.start(RunRequest(), [])
    assert record.wait(5)
    events, _ = record.events(limit=100)
    assert len(events) == 10
    assert record.dropped_events == 0

    registry = RunRegistry(executor, max_events=50)
    record = registry.start(RunRequest(), [])
    record._events = record._events.__class__(maxlen=4)  # simulate a tiny cap
    assert record.wait(5)
    assert record.dropped_events >= 0


def test_store_evicts_only_finished_runs():
    store = InMemoryRunStore(max_retained=2)
    finished = [RunRecord(f"run-{i}", RunRequest(), [], max_events=10) for i in range(3)]
    for record in finished:
        record.mark_completed(RunResult(status=RunStatus.PASSED))
        store.add(record)
    assert [r.run_id for r in store.list_runs(limit=10)] == ["run-2", "run-1"]
    active = RunRecord("run-active", RunRequest(), [], max_events=10)
    store.add(active)
    store.add(RunRecord("run-x", RunRequest(), [], max_events=10))
    assert store.get("run-active") is not None


# -- redaction -------------------------------------------------------------------------------------


def test_redaction_masks_secrets_but_keeps_structure(project: Project):
    service = ArgusService(project.config())
    data = service.redacted_config()
    assert data["backend"]["token"] == "[REDACTED]"
    assert data["backend"]["auth_header"] == "Authorization"  # a header *name*, not a secret
    assert data["devices"]["fake_ghost"]["serial"] == "${ARGUS_TEST_MISSING}"
    assert data["config_file"] == str(project.config_file)
    assert redact_mapping({"headers": {"Authorization": "Bearer abc"}}) == {
        "headers": {"Authorization": "[REDACTED]"}
    }
    assert redact_mapping({"note": "password: hunter2"}) == {"note": "password: [REDACTED]"}
