"""ArgusService — the interface-neutral application service.

The CLI, the MCP server, and a future GUI/REST layer are all clients of this
class. It owns no engine logic: every operation delegates to ``TestRunner``,
``RunSession``, the preflight builders, the artifact manager, or the
reporters, and only adds what those need to be *served* — caching, run
tracking, device arbitration, and safe (bounded, redacted) views of results.
"""

from __future__ import annotations

import json
import mimetypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL.Image import Image

from argus.adapters.base import DeviceCapabilities
from argus.adapters.registry import DeviceRegistry
from argus.artifacts.manager import ArtifactManager
from argus.config.models import AppConfig, DeviceConfig
from argus.engine.filters import TestFilter
from argus.engine.runner import RunOptions, TestRunner
from argus.engine.session import RunSession
from argus.events.bus import EventBus
from argus.exceptions import ConfigurationError, UTFError
from argus.logging import get_logger, redact
from argus.models.common import HealthCheckResult
from argus.models.results import PreflightResult, RunResult, RunStatus, TestResult, TestStatus
from argus.models.test_definition import TestDefinition
from argus.preflight.checks import (
    build_preflight_checks,
    uses_backend,
    uses_instrumentation,
    uses_ocr,
)
from argus.preflight.runner import run_preflight
from argus.reporting import write_html_report, write_json_report, write_junit_report
from argus.service.catalog import TestCatalog
from argus.service.runs import (
    RunRecord,
    RunRegistry,
    RunRequest,
    RunStore,
    RunSummary,
)
from argus.service.validation import ValidationReport, validate_environment

# Configuration keys whose values are secrets, whatever their nesting.
_SECRET_KEY_RE = re.compile(
    r"(?i)(token|password|passwd|secret|api[_-]?key|private[_-]?key|credential|"
    r"passphrase|authorization|cookie)"
)
_REDACTED = "[REDACTED]"

_CATEGORY_HINTS: dict[str, str] = {
    "assertion": "The screen did not match the expectation; compare actual.png with "
    "expected.png (and diff.png) before changing thresholds or reference images.",
    "timeout": "The condition never became true within the timeout; check the app state, "
    "then the reference image/text, then the timeout value.",
    "device_connection": "The device could not be reached; run argus_get_device with "
    "probe=true, then argus_validate.",
    "backend": "The backend request failed; check backend.base_url, credentials, and that "
    "the backend test environment is running.",
    "screenshot": "Screen capture failed; check the device's screenshot provider and "
    "display state.",
    "error": "An action crashed; inspect the step error and the test definition.",
}


# -- views ------------------------------------------------------------------------------------


@dataclass
class DeviceInfo:
    name: str
    type: str
    platform: str
    configured: bool
    capabilities: list[str] | None
    instrumentation: str | None  # "http" | "fake" | "device" | None
    busy: str | None  # holder description when claimed by a run/operation
    health: HealthCheckResult | None = None
    screen: dict[str, Any] | None = None
    application_running: bool | None = None
    probe_error: str | None = None
    #: Non-secret adapter options (for display).
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreflightReport:
    passed: bool
    checks: list[PreflightResult]
    test_ids: list[str]
    device_names: list[str]
    backend_required: bool
    ocr_required: bool
    instrumentation_required: bool
    duration: float


@dataclass
class ArtifactInfo:
    artifact_id: str  # path relative to the run's results directory (POSIX)
    kind: str
    mime_type: str
    size: int
    modified_at: float
    test_id: str | None
    platform: str | None
    description: str


@dataclass
class ArtifactContent:
    info: ArtifactInfo
    data: bytes
    truncated: bool


@dataclass
class TestDiagnosis:
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


@dataclass
class RunDiagnosis:
    run_id: str
    state: str
    status: str | None
    stop_reason: str | None
    error: str | None
    preflight_failures: list[PreflightResult]
    tests: list[TestDiagnosis]


# -- service ------------------------------------------------------------------------------------


class ArgusService:
    """Facade over the engine for programmatic clients (CLI, MCP, GUI)."""

    def __init__(
        self,
        config: AppConfig,
        *,
        run_store: RunStore | None = None,
        max_concurrent_runs: int | None = None,
        max_run_events: int | None = None,
        max_retained_runs: int | None = None,
    ) -> None:
        self.config = config
        self.log = get_logger("argus.service")
        limits = config.mcp.limits
        self.catalog = TestCatalog(config)
        self.runs = RunRegistry(
            self._execute_run,
            store=run_store,
            max_concurrent_runs=max_concurrent_runs or limits.max_concurrent_runs,
            max_events=max_run_events or limits.max_run_events,
            max_retained_runs=max_retained_runs or limits.max_retained_runs,
        )
        self._device_registry = DeviceRegistry()

    # -- tests ----------------------------------------------------------------------------

    def load_tests(self) -> list[TestDefinition]:
        return self.catalog.load()

    def select_tests(self, filters: TestFilter | None = None) -> list[TestDefinition]:
        return self.catalog.select(filters)

    def get_test(self, test_id: str) -> TestDefinition | None:
        return self.catalog.get(test_id)

    # -- validation / preflight ----------------------------------------------------------

    def validate(self, *, framework_only: bool = False) -> ValidationReport:
        if framework_only:
            return validate_environment(self.config, framework_only=True)
        devices = sorted(self.config.devices)
        with self.runs.claim(devices, "environment validation"):
            return validate_environment(self.config, framework_only=False)

    def preflight(
        self, filters: TestFilter | None = None, *, device: str | None = None
    ) -> PreflightReport:
        """Run the same checks a real run would, without executing tests."""
        request = RunRequest(filters=filters or TestFilter(), device=device)
        config = self._config_for(request)
        options = self._options_for(request)
        runner = TestRunner(config)
        tests = runner.select(options.filters)
        device_names = runner.device_names_for(tests, options.filters)
        started = time.monotonic()
        with self.runs.claim(device_names, "preflight"), RunSession(config) as session:
            checks = build_preflight_checks(session, tests, device_names)
            results, passed = run_preflight(checks, EventBus())
        return PreflightReport(
            passed=passed,
            checks=results,
            test_ids=[t.id for t in tests],
            device_names=device_names,
            backend_required=uses_backend(tests),
            ocr_required=uses_ocr(tests),
            instrumentation_required=uses_instrumentation(tests),
            duration=time.monotonic() - started,
        )

    # -- devices ---------------------------------------------------------------------------

    def list_devices(self) -> list[DeviceInfo]:
        busy = self.runs.busy_devices()
        return [
            self._device_info(name, cfg, busy.get(name))
            for name, cfg in sorted(self.config.devices.items())
        ]

    def get_device(self, name: str, *, probe: bool = False) -> DeviceInfo:
        cfg = self._device_config(name)
        info = self._device_info(name, cfg, self.runs.busy_devices().get(name))
        if not probe:
            return info
        if not cfg.configured:
            info.probe_error = "Device has unresolved ${...} configuration values."
            return info
        with self.runs.claim([name], "device probe"), RunSession(self.config) as session:
            try:
                device = session.device(name)
            except UTFError as exc:
                info.probe_error = str(exc)
                return info
            info.health = device.health_check()
            if device.capabilities.supports_screenshot:
                try:
                    screen = device.get_screen_info()
                    info.screen = screen.model_dump(exclude_none=True)
                except Exception:  # noqa: BLE001 - screen info is best-effort metadata
                    info.screen = None
            if device.capabilities.supports_app_lifecycle:
                try:
                    info.application_running = device.is_application_running()
                except UTFError:
                    info.application_running = None
        return info

    def capture_screenshot(self, name: str) -> Image:
        cfg = self._device_config(name)
        if not cfg.configured:
            raise ConfigurationError(
                f"Device {name!r} has unresolved ${{...}} configuration values.",
                remediation="Set the referenced environment variables and restart.",
            )
        with self.runs.claim([name], "screenshot"), RunSession(self.config) as session:
            device = session.device(name)
            if not device.capabilities.supports_screenshot:
                raise ConfigurationError(
                    f"Device {name!r} does not support screenshots.",
                    remediation="Choose a screenshot-capable device (see argus_list_devices).",
                )
            return device.screenshot()

    def _device_config(self, name: str) -> DeviceConfig:
        cfg = self.config.devices.get(name)
        if cfg is None:
            raise ConfigurationError(
                f"Unknown device {name!r}.",
                remediation="Configured devices: "
                f"{', '.join(sorted(self.config.devices)) or '<none>'}.",
            )
        return cfg

    def _device_info(self, name: str, cfg: DeviceConfig, busy: str | None) -> DeviceInfo:
        capabilities: list[str] | None
        try:
            device = self._device_registry.create(name, cfg)
            capabilities = capability_names(device.capabilities)
        except Exception:  # noqa: BLE001 - optional adapter deps may be missing
            capabilities = None
        instrumentation = cfg.instrumentation.type if cfg.instrumentation else None
        if instrumentation and capabilities is not None:
            capabilities.append("instrumentation")
        return DeviceInfo(
            name=name,
            type=cfg.type,
            platform=cfg.effective_platform,
            configured=cfg.configured,
            capabilities=sorted(set(capabilities)) if capabilities is not None else None,
            instrumentation=instrumentation,
            busy=busy,
            options=redact_mapping(cfg.options),
        )

    # -- runs --------------------------------------------------------------------------------

    def start_run(self, request: RunRequest) -> RunRecord:
        config = self._config_for(request)
        options = self._options_for(request)
        runner = TestRunner(config)
        tests = runner.select(options.filters)  # raises TestDefinitionError early
        devices = runner.device_names_for(tests, options.filters)
        return self.runs.start(request, devices)

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.runs.get(run_id)

    def require_run(self, run_id: str) -> RunRecord:
        record = self.runs.get(run_id)
        if record is None:
            raise ConfigurationError(
                f"Unknown run_id {run_id!r}.",
                remediation="Use a run_id returned by argus_run_test/argus_run_tests, "
                "or list runs via the argus://runs resource.",
            )
        return record

    def list_runs(self, *, limit: int = 50) -> list[RunSummary]:
        return [r.summary() for r in self.runs.list_runs(limit=limit)]

    def _config_for(self, request: RunRequest) -> AppConfig:
        """Per-run configuration view: device restriction + comparison images."""
        if not request.device and not request.save_comparisons:
            return self.config
        config = self.config.model_copy(deep=True)
        if request.save_comparisons:
            config.results.save_comparison_images = True
        if request.device:
            cfg = self._device_config(request.device)
            config.devices = {request.device: cfg}
        return config

    def _options_for(self, request: RunRequest) -> RunOptions:
        filters = request.filters
        if request.device and not filters.platforms:
            platform = self._device_config(request.device).effective_platform
            filters = TestFilter(
                test_ids=list(filters.test_ids),
                features=list(filters.features),
                tags=list(filters.tags),
                platforms=[platform],
                tag_expression=filters.tag_expression,
            )
        return RunOptions(
            filters=filters,
            failure_policy=request.failure_policy,
            skip_preflight=request.skip_preflight,
        )

    def _execute_run(self, record: RunRecord, events: EventBus) -> RunResult:
        """RunRegistry executor: the exact sequence ``argus run`` performs."""
        config = self._config_for(record.request)
        runner = TestRunner(config, events)
        result = runner.run(self._options_for(record.request))
        self._write_reports(config, result, record)
        return result

    def _write_reports(self, config: AppConfig, result: RunResult, record: RunRecord) -> None:
        if result.status == RunStatus.PREFLIGHT_FAILED:
            manager = ArtifactManager(config.results, Path(config.root_dir or "."))
            path = manager.save_run_report(
                "preflight.json",
                json.dumps(
                    [r.model_dump(mode="json") for r in result.preflight],
                    indent=2,
                    default=str,
                ),
            )
            result.results_dir = str(path.parent)
            return
        if result.results_dir:
            results_dir = Path(result.results_dir)
            write_json_report(result, results_dir / "report.json")
            write_junit_report(result, results_dir / "junit.xml")
            write_html_report(result, results_dir / "report.html")

    # -- artifacts ----------------------------------------------------------------------------

    def list_artifacts(self, run_id: str, *, test_id: str | None = None) -> list[ArtifactInfo]:
        record = self.require_run(run_id)
        root = self._results_root(record)
        if root is None:
            return []
        owners = self._artifact_owners(record)
        infos: list[ArtifactInfo] = []
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root).as_posix()
            owner_test, owner_platform = owners.get(rel.split("/", 1)[0], (None, None))
            if test_id is not None and owner_test != test_id:
                continue
            infos.append(self._artifact_info(path, rel, owner_test, owner_platform))
        return infos

    def read_artifact(self, run_id: str, artifact_id: str, *, max_bytes: int) -> ArtifactContent:
        record = self.require_run(run_id)
        root = self._results_root(record)
        if root is None:
            raise ConfigurationError(
                f"Run {run_id} produced no artifacts.",
                remediation="Only runs that executed tests (or failed preflight) have "
                "artifacts.",
            )
        path = self._safe_artifact_path(root, artifact_id)
        rel = path.relative_to(root).as_posix()
        owner_test, owner_platform = self._artifact_owners(record).get(
            rel.split("/", 1)[0], (None, None)
        )
        info = self._artifact_info(path, rel, owner_test, owner_platform)
        with path.open("rb") as fh:
            data = fh.read(max_bytes + 1)
        truncated = len(data) > max_bytes
        return ArtifactContent(info=info, data=data[:max_bytes], truncated=truncated)

    @staticmethod
    def _results_root(record: RunRecord) -> Path | None:
        if not record.results_dir:
            return None
        root = Path(record.results_dir).resolve()
        return root if root.is_dir() else None

    @staticmethod
    def _safe_artifact_path(root: Path, artifact_id: str) -> Path:
        """Resolve ``artifact_id`` inside ``root`` or refuse (path traversal)."""
        candidate = Path(artifact_id)
        if (
            not artifact_id
            or candidate.is_absolute()
            or any(part in ("..", "") for part in candidate.parts)
            or "\\" in artifact_id
            or "\x00" in artifact_id
        ):
            raise ConfigurationError(
                f"Invalid artifact_id {artifact_id!r}.",
                remediation="Use an artifact_id exactly as returned by argus_list_artifacts.",
            )
        resolved = (root / candidate).resolve()
        if resolved != root and root not in resolved.parents:
            raise ConfigurationError(
                f"Artifact {artifact_id!r} is outside the run's results directory.",
                remediation="Use an artifact_id exactly as returned by argus_list_artifacts.",
            )
        if not resolved.is_file():
            raise ConfigurationError(
                f"Artifact {artifact_id!r} not found.",
                remediation="List available artifacts with argus_list_artifacts.",
            )
        return resolved

    @staticmethod
    def _artifact_owners(record: RunRecord) -> dict[str, tuple[str, str | None]]:
        """Map top-level artifact directory name -> (test_id, platform)."""
        owners: dict[str, tuple[str, str | None]] = {}
        if record.result is None:
            return owners
        for test in record.result.tests:
            if test.artifact_dir:
                owners[Path(test.artifact_dir).name] = (test.test_id, test.platform)
        return owners

    @staticmethod
    def _artifact_info(
        path: Path, rel: str, test_id: str | None, platform: str | None
    ) -> ArtifactInfo:
        stat = path.stat()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        kind, description = classify_artifact(path.name)
        return ArtifactInfo(
            artifact_id=rel,
            kind=kind,
            mime_type=mime,
            size=stat.st_size,
            modified_at=stat.st_mtime,
            test_id=test_id,
            platform=platform,
            description=description,
        )

    # -- diagnostics ---------------------------------------------------------------------------

    def diagnose_run(self, run_id: str, *, test_id: str | None = None) -> RunDiagnosis:
        record = self.require_run(run_id)
        summary = record.summary()
        result = record.result
        preflight_failures: list[PreflightResult] = []
        tests: list[TestDiagnosis] = []
        if result is not None:
            preflight_failures = [r for r in result.preflight if not r.passed and r.required]
            artifacts_by_test: dict[tuple[str, str | None], list[str]] = {}
            for info in self.list_artifacts(run_id) if summary.results_dir else []:
                if info.test_id is not None:
                    artifacts_by_test.setdefault((info.test_id, info.platform), []).append(
                        info.artifact_id
                    )
            for test in result.tests:
                if test_id is not None and test.test_id != test_id:
                    continue
                if test.status not in (TestStatus.FAILED, TestStatus.ERROR):
                    continue
                tests.append(
                    self._diagnose_test(
                        test, artifacts_by_test.get((test.test_id, test.platform), [])
                    )
                )
        return RunDiagnosis(
            run_id=run_id,
            state=summary.state.value,
            status=summary.status,
            stop_reason=summary.stop_reason,
            error=summary.error,
            preflight_failures=preflight_failures,
            tests=tests,
        )

    def _diagnose_test(self, test: TestResult, artifacts: list[str]) -> TestDiagnosis:
        failed_step = None
        expected = None
        observed = None
        for index, step in enumerate(test.steps):
            if step.passed:
                continue
            failed_step = {
                "index": index,
                "action": step.action,
                "name": step.name,
                "message": step.message,
                "failure_category": step.failure_category,
                "duration_ms": round(step.duration * 1000),
            }
            if step.verification is not None:
                v = step.verification
                expected = {
                    "verifier": v.verifier,
                    **{
                        k: val
                        for k, val in v.details.items()
                        if k in ("image", "text", "threshold", "region", "expected", "key")
                    },
                }
                observed = {
                    "confidence": v.confidence,
                    "location": v.location.model_dump() if v.location else None,
                    "message": v.message,
                    **{
                        k: val
                        for k, val in v.details.items()
                        if k in ("actual", "found", "best_match", "observed")
                    },
                }
            break
        device = None
        if test.platform:
            names = self.config.devices_for_platform(test.platform)
            device = {"platform": test.platform, "candidates": sorted(names)}
        return TestDiagnosis(
            test_id=test.test_id,
            name=test.name,
            platform=test.platform,
            status=test.status.value,
            failure_category=test.failure_category,
            error=test.error,
            attempts=test.attempts,
            failed_step=failed_step,
            expected=expected,
            observed=observed,
            device=device,
            instrumentation_state=test.instrumentation_state,
            artifacts=artifacts,
            hint=_CATEGORY_HINTS.get(test.failure_category or ""),
        )

    # -- configuration -----------------------------------------------------------------------

    def redacted_config(self) -> dict[str, Any]:
        """Configuration with every secret-looking value replaced."""
        data = self.config.model_dump(mode="json", exclude={"config_file", "root_dir"})
        data["config_file"] = self.config.config_file
        return redact_mapping(data)

    # -- lifecycle ------------------------------------------------------------------------------

    def close(self, *, timeout: float | None = None) -> None:
        self.runs.wait_all(timeout)


# -- helpers ----------------------------------------------------------------------------------


def capability_names(capabilities: DeviceCapabilities) -> list[str]:
    return [
        name.removeprefix("supports_")
        for name, enabled in vars(capabilities).items()
        if name.startswith("supports_") and enabled
    ]


def redact_mapping(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact secret-looking keys and strings."""
    if isinstance(value, dict):
        return {k: redact_mapping(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_mapping(v, key=key) for v in value]
    if key is not None and _SECRET_KEY_RE.search(key) and value not in (None, "", [], {}):
        if isinstance(value, str) and "${" in value:
            return value  # unresolved reference, not a secret
        return _REDACTED
    if isinstance(value, str):
        return redact(value)
    return value


_ARTIFACT_KINDS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(^|_)actual\.png$"), "screenshot", "Screen captured at the failing step"),
    (re.compile(r"(^|_)expected\.png$"), "reference", "Reference image the test expected"),
    (re.compile(r"(^|_)diff\.png$"), "diff", "Visual difference between actual and expected"),
    (re.compile(r"^logs\.txt$"), "log", "Device log captured after the failure"),
    (re.compile(r"^instrumentation\.json$"), "instrumentation", "Application-reported state"),
    (re.compile(r"^metrics\.json$"), "metrics", "In-run FPS, jank, memory and load samples"),
    (re.compile(r"^metadata\.json$"), "metadata", "Structured test result"),
    (re.compile(r"^report\.json$"), "report", "Machine-readable run report"),
    (re.compile(r"^report\.html$"), "report", "HTML run report"),
    (re.compile(r"^junit\.xml$"), "report", "JUnit XML run report"),
    (re.compile(r"^preflight\.json$"), "report", "Pre-flight check results"),
    (re.compile(r"\.png$|\.jpe?g$"), "image", "Image artifact"),
]


def classify_artifact(name: str) -> tuple[str, str]:
    for pattern, kind, description in _ARTIFACT_KINDS:
        if pattern.search(name):
            return kind, description
    return "file", "Run artifact"
