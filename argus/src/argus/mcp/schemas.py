"""MCP-facing output models.

These are the *public contract* of the Argus MCP API (see docs/mcp.md,
"Versioning"). They are projections of Argus models — compact, bounded, and
stable — built through ``from_*`` constructors so internal refactors touch
one place. Input schemas are the typed tool signatures themselves.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from argus.models.results import PreflightResult, TestResult
from argus.models.test_definition import Step, TestDefinition
from argus.service.facade import (
    ArtifactInfo,
    DeviceInfo,
    PreflightReport,
    RunDiagnosis,
    TestDiagnosis,
)
from argus.service.runs import RunEvent, RunRecord, RunSummary
from argus.service.validation import CheckState, ValidationReport

_TEXT_LIMIT = 500


def clip(text: str | None, limit: int = _TEXT_LIMIT) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ms(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds * 1000)


# -- tests ------------------------------------------------------------------------------------


class TestSummary(BaseModel):
    id: str
    name: str
    feature: str
    tags: list[str]
    platforms: list[str]
    description: str = ""
    priority: str | None = None
    step_count: int
    required_devices: list[str] = Field(default_factory=list)

    @classmethod
    def from_definition(cls, test: TestDefinition) -> TestSummary:
        return cls(
            id=test.id,
            name=test.name,
            feature=test.feature,
            tags=list(test.tags),
            platforms=list(test.platforms),
            description=clip(test.description, 300) or "",
            priority=test.priority,
            step_count=len(test.steps),
            required_devices=test.required_devices,
        )


class TestList(BaseModel):
    items: list[TestSummary]
    total: int = Field(description="Tests matching the filters (before pagination).")
    truncated: bool
    next_cursor: str | None = None


class StepView(BaseModel):
    action: str
    name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_step(cls, step: Step) -> StepView:
        return cls(action=step.action, name=step.name, params=step.params)


class TestDetail(BaseModel):
    id: str
    name: str
    description: str
    feature: str
    tags: list[str]
    platforms: list[str]
    priority: str | None
    timeout: str | float | None
    requires: dict[str, Any]
    parameters: dict[str, Any]
    retry: dict[str, Any]
    setup: list[StepView]
    steps: list[StepView]
    teardown: list[StepView]
    source: str | None = Field(description="Definition file, relative to the project root.")

    @classmethod
    def from_definition(cls, test: TestDefinition, root_dir: str | None) -> TestDetail:
        source = test.source_file
        if source and root_dir:
            try:
                source = str(Path(source).resolve().relative_to(Path(root_dir).resolve()))
            except ValueError:
                source = Path(source).name
        return cls(
            id=test.id,
            name=test.name,
            description=test.description,
            feature=test.feature,
            tags=list(test.tags),
            platforms=list(test.platforms),
            priority=test.priority,
            timeout=test.timeout,
            requires=dict(test.requires),
            parameters=dict(test.parameters),
            retry=test.retry.model_dump(),
            setup=[StepView.from_step(s) for s in test.setup],
            steps=[StepView.from_step(s) for s in test.steps],
            teardown=[StepView.from_step(s) for s in test.teardown],
            source=source,
        )


# -- validation / preflight ----------------------------------------------------------------


class CheckItem(BaseModel):
    section: str
    name: str
    state: str = Field(description="ok | warn | fail | not_configured")
    detail: str = ""
    required: bool


class ValidationResult(BaseModel):
    status: str = Field(description="ready | not_ready")
    framework_only: bool
    checks: list[CheckItem]
    failures: list[str]
    warnings: list[str]
    remediation: list[str]

    @classmethod
    def from_report(cls, report: ValidationReport, *, framework_only: bool) -> ValidationResult:
        checks: list[CheckItem] = []
        failures: list[str] = []
        warnings: list[str] = []
        remediation: list[str] = []
        for section in report.sections:
            for item in section.items:
                label = f"{section.title}: {item.name}"
                checks.append(
                    CheckItem(
                        section=section.title,
                        name=item.name,
                        state=item.state.value,
                        detail=clip(item.detail, 300) or "",
                        required=item.required,
                    )
                )
                if item.state == CheckState.FAIL and item.required:
                    failures.append(f"{label} — {item.detail}" if item.detail else label)
                    remediation.append(_validation_hint(item.name, section.title))
                elif item.state in (CheckState.WARN, CheckState.NOT_CONFIGURED) or (
                    item.state == CheckState.FAIL
                ):
                    warnings.append(f"{label} — {item.detail}" if item.detail else label)
        return cls(
            status="ready" if report.ready else "not_ready",
            framework_only=framework_only,
            checks=checks,
            failures=failures,
            warnings=warnings,
            remediation=sorted(set(remediation)),
        )


def _validation_hint(name: str, section: str) -> str:
    if name == "Dependencies":
        return 'Reinstall the framework: pip install -e "." (or ./install.sh).'
    if name == "Test definitions":
        return "Fix the YAML error reported in the detail; see docs/test-authoring.md."
    if name == "Python":
        return "Use Python 3.12 or newer."
    if name == "OpenCV":
        return "Reinstall opencv-python-headless."
    if section == "Backend":
        return "Check backend.base_url / credentials and that the backend is running."
    if section.startswith("Device:"):
        device = section.removeprefix("Device: ")
        return f"Fix connectivity for {device}; see docs/troubleshooting.md."
    return "See docs/troubleshooting.md."


class PreflightCheckItem(BaseModel):
    name: str
    passed: bool
    required: bool
    target: str | None = None
    error: str | None = None
    remediation: str | None = None
    causes: list[str] = Field(default_factory=list)
    duration_ms: int
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_result(cls, result: PreflightResult) -> PreflightCheckItem:
        return cls(
            name=result.name,
            passed=result.passed,
            required=result.required,
            target=result.target,
            error=clip(result.error),
            remediation=result.remediation,
            causes=list(result.causes),
            duration_ms=_ms(result.duration) or 0,
            diagnostics={k: v for k, v in result.diagnostics.items() if _small(v)},
        )


def _small(value: Any) -> bool:
    return len(str(value)) <= 200


class Requirements(BaseModel):
    devices: list[str]
    backend: bool
    screenshot: list[str] = Field(description="Devices that must capture the screen.")
    ocr: bool
    instrumentation: bool


class PreflightOutcome(BaseModel):
    status: str = Field(description="ready | not_ready | no_tests")
    tests: list[str]
    requirements: Requirements
    checks: list[PreflightCheckItem]
    passed: list[str]
    failed: list[str]
    remediation: list[str]
    duration_ms: int

    @classmethod
    def from_report(cls, report: PreflightReport) -> PreflightOutcome:
        checks = [PreflightCheckItem.from_result(r) for r in report.checks]
        failed = [c for c in checks if not c.passed and c.required]
        if not report.passed:
            status = "not_ready"
        elif not report.test_ids:
            status = "no_tests"
        else:
            status = "ready"
        return cls(
            status=status,
            tests=report.test_ids,
            requirements=Requirements(
                devices=report.device_names,
                backend=report.backend_required,
                screenshot=report.device_names,
                ocr=report.ocr_required,
                instrumentation=report.instrumentation_required,
            ),
            checks=checks,
            passed=[c.name for c in checks if c.passed],
            failed=[c.name for c in failed],
            remediation=[f"{c.name}: {c.remediation}" for c in failed if c.remediation],
            duration_ms=_ms(report.duration) or 0,
        )


# -- runs --------------------------------------------------------------------------------------


class TestOutcome(BaseModel):
    test_id: str
    name: str
    platform: str | None
    status: str
    duration_ms: int
    failure_category: str | None = None
    error: str | None = None
    attempts: int = 1
    has_artifacts: bool = False

    @classmethod
    def from_result(cls, result: TestResult) -> TestOutcome:
        return cls(
            test_id=result.test_id,
            name=result.name,
            platform=result.platform,
            status=result.status.value,
            duration_ms=_ms(result.duration) or 0,
            failure_category=result.failure_category,
            error=clip(result.error),
            attempts=result.attempts,
            has_artifacts=bool(result.artifact_dir),
        )


class RunStatusView(BaseModel):
    run_id: str
    state: str = Field(description="queued | running | completed | errored")
    status: str | None = Field(
        default=None,
        description="Engine status once completed: passed | failed | stopped | "
        "preflight_failed | setup_failed",
    )
    request: dict[str, Any]
    devices: list[str]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    total_tests: int
    executed: int
    passed: int
    failed: int
    skipped: int
    current_test: dict[str, Any] | None
    stop_reason: str | None
    error: str | None = None
    error_category: str | None = None
    results_dir: str | None = Field(
        default=None, description="Argus-managed directory holding reports and artifacts."
    )
    event_count: int
    summary: str

    @classmethod
    def from_summary(cls, s: RunSummary) -> RunStatusView:
        duration = None
        if s.started_at and s.finished_at:
            duration = _ms((s.finished_at - s.started_at).total_seconds())
        return cls(
            run_id=s.run_id,
            state=s.state.value,
            status=s.status,
            request=s.request,
            devices=s.devices,
            created_at=s.created_at,
            started_at=s.started_at,
            finished_at=s.finished_at,
            duration_ms=duration,
            total_tests=s.total_tests,
            executed=s.executed,
            passed=s.passed,
            failed=s.failed,
            skipped=s.skipped,
            current_test=s.current_test,
            stop_reason=s.stop_reason,
            error=s.error,
            error_category=s.error_category,
            results_dir=s.results_dir,
            event_count=s.event_count,
            summary=describe_run(s),
        )


def describe_run(s: RunSummary) -> str:
    if s.state.value == "queued":
        return "Run queued."
    if s.state.value == "running":
        current = f" — running {s.current_test['test_id']}" if s.current_test else ""
        return f"Running: {s.executed}/{s.total_tests} done, {s.failed} failed{current}."
    if s.state.value == "errored":
        return f"Run errored ({s.error_category}): {s.error}"
    parts = [f"{s.passed} passed", f"{s.failed} failed"]
    if s.skipped:
        parts.append(f"{s.skipped} skipped")
    text = f"Run {s.status}: " + ", ".join(parts) + "."
    if s.stop_reason:
        text += f" Stopped early: {s.stop_reason}."
    return text


class RunOutcome(BaseModel):
    """Result of ``argus_run_test`` / ``argus_run_tests``."""

    run: RunStatusView
    completed: bool = Field(description="False when the call returned before the run ended.")
    tests: list[TestOutcome]
    failures: list[TestOutcome]
    artifacts: list[str] = Field(description="artifact_ids (bounded); see argus_list_artifacts.")
    next_step: str

    @classmethod
    def from_record(cls, record: RunRecord, artifact_ids: list[str]) -> RunOutcome:
        summary = record.summary()
        result = record.result
        tests = [TestOutcome.from_result(t) for t in result.tests] if result else []
        failures = [t for t in tests if t.status in ("failed", "error")]
        view = RunStatusView.from_summary(summary)
        return cls(
            run=view,
            completed=summary.finished,
            tests=tests,
            failures=failures,
            artifacts=artifact_ids,
            next_step=_next_step(view, failures),
        )


def _next_step(view: RunStatusView, failures: list[TestOutcome]) -> str:
    if view.state != "completed":
        if view.state == "errored":
            return "Fix the reported error (see argus_validate) and rerun."
        return f"Poll argus_get_run with run_id={view.run_id!r} until state is completed."
    if view.status == "preflight_failed":
        return (
            f"Call argus_diagnose_run({view.run_id!r}) or argus_preflight to see the "
            "failed checks."
        )
    if failures:
        return (
            f"Call argus_diagnose_run(run_id={view.run_id!r}) then argus_get_artifact for "
            "actual/expected/diff images."
        )
    return "All tests passed; nothing to diagnose."


class RunEventView(BaseModel):
    seq: int
    timestamp: datetime
    type: str
    data: dict[str, Any]

    @classmethod
    def from_event(cls, event: RunEvent) -> RunEventView:
        return cls(seq=event.seq, timestamp=event.timestamp, type=event.type, data=event.data)


class RunEvents(BaseModel):
    run_id: str
    state: str
    events: list[RunEventView]
    next_after: int = Field(description="Pass as `after` to fetch newer events.")
    has_more: bool
    dropped: int = Field(description="Oldest events discarded because of the event cap.")


class RunListItem(BaseModel):
    run_id: str
    state: str
    status: str | None
    created_at: datetime
    finished_at: datetime | None
    passed: int
    failed: int
    skipped: int
    request: dict[str, Any]

    @classmethod
    def from_summary(cls, s: RunSummary) -> RunListItem:
        return cls(
            run_id=s.run_id,
            state=s.state.value,
            status=s.status,
            created_at=s.created_at,
            finished_at=s.finished_at,
            passed=s.passed,
            failed=s.failed,
            skipped=s.skipped,
            request=s.request,
        )


class RunList(BaseModel):
    items: list[RunListItem]
    total: int


# -- devices ------------------------------------------------------------------------------------


class DeviceSummary(BaseModel):
    name: str
    adapter: str
    platform: str
    state: str = Field(description="idle | busy | not_configured")
    busy_with: str | None = None
    capabilities: list[str] | None = Field(
        description="None when the adapter's optional dependency is not installed."
    )
    instrumentation: str | None

    @classmethod
    def from_info(cls, info: DeviceInfo) -> DeviceSummary:
        return cls(
            name=info.name,
            adapter=info.type,
            platform=info.platform,
            state=_device_state(info),
            busy_with=info.busy,
            capabilities=info.capabilities,
            instrumentation=info.instrumentation,
        )


def _device_state(info: DeviceInfo) -> str:
    if not info.configured:
        return "not_configured"
    return "busy" if info.busy else "idle"


class DeviceList(BaseModel):
    items: list[DeviceSummary]
    total: int
    truncated: bool
    next_cursor: str | None = None


class DeviceDetail(DeviceSummary):
    probed: bool
    health: dict[str, Any] | None = None
    screen: dict[str, Any] | None = None
    application_running: bool | None = None
    probe_error: str | None = None
    options: dict[str, Any] = Field(description="Adapter options with secrets redacted.")

    @classmethod
    def from_info(cls, info: DeviceInfo, *, probed: bool = False) -> DeviceDetail:
        base = DeviceSummary.from_info(info)
        health = None
        if info.health is not None:
            health = {
                "status": info.health.status.value,
                "message": info.health.message,
                "details": {k: v for k, v in info.health.details.items() if _small(v)},
            }
        return cls(
            **base.model_dump(),
            probed=probed,
            health=health,
            screen=info.screen,
            application_running=info.application_running,
            probe_error=clip(info.probe_error),
            options=info.options,
        )


# -- artifacts -----------------------------------------------------------------------------------


class ArtifactItem(BaseModel):
    artifact_id: str
    kind: str = Field(description="screenshot | reference | diff | log | instrumentation | "
    "metadata | report | image | file")
    mime_type: str
    size: int
    modified_at: datetime
    test_id: str | None
    platform: str | None
    description: str

    @classmethod
    def from_info(cls, info: ArtifactInfo) -> ArtifactItem:
        return cls(
            artifact_id=info.artifact_id,
            kind=info.kind,
            mime_type=info.mime_type,
            size=info.size,
            modified_at=datetime.fromtimestamp(info.modified_at).astimezone(),
            test_id=info.test_id,
            platform=info.platform,
            description=info.description,
        )


class ArtifactList(BaseModel):
    run_id: str
    items: list[ArtifactItem]
    total: int
    truncated: bool
    next_cursor: str | None = None


class ArtifactContentView(BaseModel):
    run_id: str
    artifact_id: str
    kind: str
    mime_type: str
    size: int
    returned_bytes: int
    truncated: bool
    delivery: str = Field(description="text | json | image | omitted")
    note: str | None = None


# -- diagnostics ---------------------------------------------------------------------------------


class TestDiagnosisView(BaseModel):
    test_id: str
    name: str
    platform: str | None
    status: str
    failure_category: str | None
    error: str | None
    attempts: int
    failed_step: dict[str, Any] | None
    expected: dict[str, Any] | None
    observed: dict[str, Any] | None
    device: dict[str, Any] | None
    instrumentation_state: dict[str, Any] | None
    artifacts: list[str]
    hint: str | None

    @classmethod
    def from_diagnosis(cls, d: TestDiagnosis) -> TestDiagnosisView:
        state = None
        if d.instrumentation_state:
            state = {k: v for k, v in d.instrumentation_state.items() if _small(v)}
        return cls(
            test_id=d.test_id,
            name=d.name,
            platform=d.platform,
            status=d.status,
            failure_category=d.failure_category,
            error=clip(d.error),
            attempts=d.attempts,
            failed_step=d.failed_step,
            expected=d.expected,
            observed=d.observed,
            device=d.device,
            instrumentation_state=state,
            artifacts=d.artifacts,
            hint=d.hint,
        )


class DiagnosisView(BaseModel):
    run_id: str
    state: str
    status: str | None
    stop_reason: str | None
    error: str | None
    preflight_failures: list[PreflightCheckItem]
    tests: list[TestDiagnosisView]
    next_steps: list[str]

    @classmethod
    def from_diagnosis(cls, d: RunDiagnosis) -> DiagnosisView:
        tests = [TestDiagnosisView.from_diagnosis(t) for t in d.tests]
        preflight = [PreflightCheckItem.from_result(r) for r in d.preflight_failures]
        next_steps: list[str] = []
        if d.state in ("queued", "running"):
            next_steps.append("The run has not finished; poll argus_get_run first.")
        if preflight:
            next_steps.append(
                "Fix the failed pre-flight checks (their remediation is listed), then "
                "argus_preflight to confirm before rerunning."
            )
        for t in tests:
            if t.artifacts:
                next_steps.append(
                    f"{t.test_id}: argus_get_artifact for "
                    + ", ".join(a for a in t.artifacts if a.endswith(".png"))[:200]
                )
        if d.state == "completed" and not tests and not preflight:
            next_steps.append("No failed tests in this run.")
        return cls(
            run_id=d.run_id,
            state=d.state,
            status=d.status,
            stop_reason=d.stop_reason,
            error=d.error,
            preflight_failures=preflight,
            tests=tests,
            next_steps=next_steps,
        )
