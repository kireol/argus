"""CreatorApp — the composition root and use-case layer.

The UI and CLI call methods here; nothing below this layer knows about Qt.
Expensive work returns a :class:`Job` from the shared worker pool.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL.Image import Image

from argus_test_creator.app.config import CreatorConfig, load_config
from argus_test_creator.assets import AssetManager
from argus_test_creator.authoring import AuthoringService
from argus_test_creator.core.errors import (
    CreatorError,
    ProjectError,
    RecordingError,
    UnsupportedCapabilityError,
)
from argus_test_creator.core.events import Event, EventBus
from argus_test_creator.core.logging import get_logger
from argus_test_creator.core.workers import Job, WorkerPool
from argus_test_creator.integrations.argus import ArgusIntegration, ArgusRunResult
from argus_test_creator.models.authoring import (
    AssetReference,
    AuthoringDocument,
    ConditionDraft,
    Provenance,
    StepDraft,
    ValidationIssue,
)
from argus_test_creator.models.capabilities import TargetProfile
from argus_test_creator.models.common import Rect
from argus_test_creator.models.recording import (
    NormalizedAction,
    OCRObservation,
    RecordingMode,
    ScreenCapture,
)
from argus_test_creator.observation import (
    AssertionCandidate,
    CaptureStore,
    OCRProvider,
    create_ocr_provider,
)
from argus_test_creator.project import CreatorProject
from argus_test_creator.quality import QualityReport, TestQualityAnalyzer
from argus_test_creator.recording import (
    RecorderAdapter,
    RecorderRegistry,
    RecordingSession,
    RecordingSessionState,
    SessionJournal,
    actions_to_steps,
)
from argus_test_creator.targets import TargetCatalog
from argus_test_creator.validation import DocumentValidator

_log = get_logger("app")

FAKE_FRAMES_DIR = Path("assets") / "frames"


@dataclass(frozen=True, kw_only=True)
class ProjectOpened(Event):
    root: Path


@dataclass(frozen=True, kw_only=True)
class TargetConnected(Event):
    target_id: str


@dataclass(frozen=True, kw_only=True)
class TargetDisconnected(Event):
    target_id: str
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class ValidationCompleted(Event):
    issues: list[ValidationIssue]
    argus_checked: bool


@dataclass(frozen=True, kw_only=True)
class RunStarted(Event):
    test_id: str


@dataclass(frozen=True, kw_only=True)
class RunOutput(Event):
    line: str


@dataclass(frozen=True, kw_only=True)
class RunFinished(Event):
    result: ArgusRunResult


@dataclass
class VerificationContext:
    """What the Add-Verification dialog works with."""

    capture: ScreenCapture
    ocr: OCRObservation | None = None
    suggestions: list[AssertionCandidate] = field(default_factory=list)


class CreatorApp:
    def __init__(
        self,
        *,
        config: CreatorConfig | None = None,
        events: EventBus | None = None,
        registry: RecorderRegistry | None = None,
        ocr: OCRProvider | None = None,
        argus: ArgusIntegration | None = None,
    ) -> None:
        self.config = config or load_config()
        self.events = events or EventBus()
        self.workers = WorkerPool(max_workers=self.config.workers)
        self.targets = TargetCatalog()
        for target_id, fields in self.config.targets.items():
            self.targets.add(TargetProfile.model_validate({"id": target_id, **fields}))
        self.registry = registry or RecorderRegistry()
        self._ocr_override = ocr
        self._argus_override = argus
        self.authoring = AuthoringService(self.events)
        self.project: CreatorProject | None = None
        self.recorder: RecorderAdapter | None = None
        self.session: RecordingSession | None = None
        self._session_steps_added: set[str] = set()
        self._last_run: ArgusRunResult | None = None

    # -- infrastructure -----------------------------------------------------------------

    @property
    def ocr(self) -> OCRProvider | None:
        if self._ocr_override is not None:
            return self._ocr_override
        if self.recorder is not None and self.recorder.target.adapter == "fake":
            return create_ocr_provider("fake")
        provider = create_ocr_provider(self.config.ocr.provider, language=self.config.ocr.language)
        available, _reason = provider.is_available()
        return provider if available else None

    @property
    def argus(self) -> ArgusIntegration:
        if self._argus_override is not None:
            return self._argus_override
        return ArgusIntegration(
            executable=self.config.argus.executable,
            project_root=self.project.root if self.project else None,
            timeout=self.config.argus.run_timeout,
        )

    def shutdown(self) -> None:
        if self.session is not None and self.session.state == RecordingSessionState.RECORDING:
            try:
                self.session.stop()
            except CreatorError:
                pass
        self.disconnect_target()
        self.workers.shutdown(wait=True, timeout=5)

    # -- project -------------------------------------------------------------------------

    def create_project(self, root: Path, *, name: str | None = None) -> CreatorProject:
        self.project = CreatorProject.create(root, name=name)
        self.config = load_config(project_root=self.project.root)
        self.events.publish(ProjectOpened(root=self.project.root))
        return self.project

    def open_project(self, root: Path) -> CreatorProject:
        self.project = CreatorProject.open(root)
        self.config = load_config(project_root=self.project.root)
        self.events.publish(ProjectOpened(root=self.project.root))
        return self.project

    def require_project(self) -> CreatorProject:
        if self.project is None:
            raise ProjectError("No project is open.", remediation="Create or open a project.")
        return self.project

    @property
    def assets(self) -> AssetManager:
        project = self.require_project()
        return AssetManager(project.assets_dir, project.workspace_dir)

    # -- documents ----------------------------------------------------------------------

    def new_test(self, *, target: TargetProfile | None = None, test_id: str = "",
                 name: str = "", feature: str = "") -> AuthoringDocument:
        document = AuthoringDocument()
        document.metadata.id = test_id
        document.metadata.name = name
        document.metadata.feature = feature
        target = target or (self.recorder.target if self.recorder else None)
        if target is not None:
            document.target = target
            document.metadata.platforms = [target.platform]
        self.authoring.replace_document(document)
        self._session_steps_added.clear()
        return document

    def open_test(self, test_id: str) -> AuthoringDocument:
        document = self.require_project().load_document(test_id)
        self.authoring.replace_document(document)
        self.authoring.mark_clean()
        return document

    def import_yaml(self, path: Path) -> AuthoringDocument:
        from argus_test_creator.serialization import load_document

        document = load_document(path)
        if self.recorder is not None:
            document.target = self.recorder.target
        self.authoring.replace_document(document)
        return document

    def save_test(self) -> Path:
        project = self.require_project()
        document = self.authoring.document
        if document.target is not None and document.target.adapter == "fake":
            document.target = self._fake_replay_target(document.target)
        path = project.save_document(document)
        self.authoring.mark_clean()
        return path

    def export_yaml(self, destination: Path) -> Path:
        """Write just the YAML (and referenced assets) somewhere else."""
        from argus_test_creator.serialization import document_to_yaml

        document = self.authoring.document
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(document_to_yaml(document), encoding="utf-8")
        if self.project is not None:
            asset_dir = destination.parent / "assets" / "images"
            for image in document.referenced_images():
                source = self.project.assets_dir / image
                if source.is_file():
                    asset_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, asset_dir / image)
        return destination

    # -- targets ---------------------------------------------------------------------------

    def select_target(self, target_id: str, settings: dict[str, Any] | None = None) -> TargetProfile:  # noqa: E501
        target = self.targets.get(target_id)
        if target is None:
            raise RecordingError(f"Unknown target {target_id!r}.",
                                 remediation=f"Available: {[t.id for t in self.targets.all()]}")
        if settings:
            target = target.model_copy(update={"settings": {**target.settings, **settings}})
        target = _sync_argus_options(target)
        self.disconnect_target()
        self.recorder = self.registry.create(target, dict(target.settings))
        document = self.authoring.document
        if document.target is None or document.target.id != target.id:
            document.target = self.recorder.target
            if not document.metadata.platforms:
                document.metadata.platforms = [target.platform]
        return self.recorder.target

    def connect_target(self) -> RecorderAdapter:
        recorder = self._require_recorder()
        recorder.connect()
        self.events.publish(TargetConnected(target_id=recorder.target.id))
        return recorder

    def disconnect_target(self, reason: str | None = None) -> None:
        if self.recorder is None:
            return
        target_id = self.recorder.target.id
        try:
            if self.recorder.connected:
                self.recorder.disconnect()
        finally:
            self.events.publish(TargetDisconnected(target_id=target_id, reason=reason))

    def reconnect_target(self) -> RecorderAdapter:
        """After a connection loss: re-attach the same device and resume the session."""
        recorder = self._require_recorder()
        reconnect = getattr(recorder, "reconnect", None)
        if callable(reconnect):
            reconnect()
        else:
            recorder.connect()
        self.events.publish(TargetConnected(target_id=recorder.target.id))
        return recorder

    def list_target_devices(self) -> list[Any]:
        """Devices the current recorder can address (Android serials); empty otherwise."""
        recorder = self._require_recorder()
        lister = getattr(recorder, "list_devices", None)
        return list(lister()) if callable(lister) else []

    def _require_recorder(self) -> RecorderAdapter:
        if self.recorder is None:
            raise RecordingError("No target selected.", remediation="Choose a target first.")
        return self.recorder

    def require_capability(self, name: str) -> None:
        recorder = self._require_recorder()
        if not recorder.capabilities.has(name):
            raise UnsupportedCapabilityError(
                f"{recorder.target.name} does not support {name.replace('_', ' ')}.",
                remediation="Choose another target or a different action.",
            )

    # -- recording ---------------------------------------------------------------------------

    def start_recording(self, *, mode: RecordingMode | None = None) -> RecordingSession:
        project = self.require_project()
        recorder = self._require_recorder()
        if not recorder.capabilities.supports_input_recording and not hasattr(
            recorder, "send_tap"
        ):
            raise UnsupportedCapabilityError(
                f"{recorder.target.name} cannot record user input.",
                remediation="Use the wizard to author steps against screenshots.",
            )
        if self.session is not None and self.session.state == RecordingSessionState.RECORDING:
            raise RecordingError("A recording is already in progress.")
        if not recorder.connected:
            self.connect_target()
        mode = mode or RecordingMode(self.config.recording.mode)
        self.session = RecordingSession(
            adapter=recorder,
            directory=project.sessions_dir / _session_dirname(),
            events=self.events,
            workers=self.workers,
            ocr=self.ocr,
            mode=mode,
            capture_after_actions=self.config.recording.capture_after_actions,
            settle_ms=self.config.recording.settle_ms,
            suggest=self.config.recording.suggest_assertions,
        )
        self._session_steps_added.clear()
        self.session.start()
        document = self.authoring.document
        if self.session.id not in document.session_ids:
            document.session_ids.append(self.session.id)
        return self.session

    def pause_recording(self) -> None:
        if self.session:
            self.session.pause()

    def resume_recording(self) -> None:
        if self.session:
            self.session.resume()

    def stop_recording(self, *, append_steps: bool = True) -> list[StepDraft]:
        if self.session is None:
            return []
        actions = self.session.stop()
        if not append_steps:
            return []
        return self.append_actions(actions)

    def append_actions(self, actions: list[NormalizedAction]) -> list[StepDraft]:
        """Turn observed actions into steps (skipping ones already added)."""
        session = self.session
        recorder = self.recorder
        fresh = [a for a in actions if a.id not in self._session_steps_added]
        steps, warnings = actions_to_steps(
            fresh, session_id=session.id if session else None,
            capabilities=recorder.capabilities if recorder else None,
        )
        for warning in warnings:
            self.authoring.document.warnings.append(
                _warning("normalization", warning)
            )
        if steps:
            self.authoring.add_steps(steps)
        self._session_steps_added.update(a.id for a in fresh)
        return steps

    def recoverable_sessions(self) -> list[Path]:
        project = self.project
        if project is None:
            return []
        return SessionJournal.recoverable(project.sessions_dir)

    def recover_session(self, directory: Path) -> RecordingSession:
        recorder = self._require_recorder()
        self.session = RecordingSession.recover(
            directory, adapter=recorder, events=self.events, workers=self.workers, ocr=self.ocr
        )
        self._session_steps_added.clear()
        return self.session

    # -- verification authoring -------------------------------------------------------------

    @property
    def captures(self) -> CaptureStore | None:
        return self.session.captures if self.session else None

    def capture_screen(self) -> Job[ScreenCapture]:
        """Screenshot for Add Verification (background)."""
        recorder = self._require_recorder()
        self.require_capability("screenshot")

        def work() -> ScreenCapture:
            if self.session is not None:
                return self.session.capture_now()
            store = self._adhoc_captures()
            metadata = getattr(recorder, "last_screen_metadata", None)
            image = recorder.screenshot()
            return store.save(image, phase="manual",
                              metadata=metadata() if callable(metadata) else None)

        return self.workers.submit("capture", work)

    def _adhoc_captures(self) -> CaptureStore:
        project = self.require_project()
        return CaptureStore(project.workspace_dir / "captures")

    def load_capture(self, capture: ScreenCapture) -> Image:
        store = self.captures or self._adhoc_captures()
        return store.load(capture)

    def run_ocr(self, capture: ScreenCapture, region: Rect | None = None) -> Job[OCRObservation | None]:  # noqa: E501
        provider = self.ocr

        def work() -> OCRObservation | None:
            if provider is None:
                return None
            if self.session is not None and region is None:
                return self.session.run_ocr(capture)
            store = self.captures or self._adhoc_captures()
            image = store.load(capture)
            try:
                return provider.extract(image, capture_id=capture.id, region=region,
                                        metadata=store.metadata(capture))
            finally:
                image.close()

        return self.workers.submit("ocr", work)

    def create_image_asset(self, capture: ScreenCapture, region: Rect, *, label: str) -> AssetReference:  # noqa: E501
        assets = self.assets
        crop = assets.crop(Path(capture.path), region, label=label)
        asset = assets.promote(crop, label=label, source_capture_id=capture.id,
                               source_region=region)
        self.authoring.add_asset(asset)
        return asset

    def add_image_verification(
        self, capture: ScreenCapture, region: Rect, *, label: str,
        condition_type: str = "image_present", threshold: float = 0.9,
        wait: bool = True, timeout: str = "10s", include_region: bool = False,
        index: int | None = None,
    ) -> StepDraft:
        self.require_capability("screenshot")
        asset = self.create_image_asset(capture, region, label=label)
        params: dict[str, Any] = {"image": asset.relative_path, "threshold": threshold}
        if include_region or condition_type == "screenshot_matches":
            params["region"] = region.to_argus()
        condition = ConditionDraft(type=condition_type, params=params)
        return self.authoring.add_verification(
            condition, wait=wait, timeout=timeout, index=index,
            provenance=Provenance(source="manual", capture_id=capture.id,
                                  session_id=self.session.id if self.session else None),
        )

    def add_text_verification(
        self, text: str, *, region: Rect | None = None, negated: bool = False,
        case_sensitive: bool = False, wait: bool = True, timeout: str = "10s",
        capture: ScreenCapture | None = None, index: int | None = None,
    ) -> StepDraft:
        self.require_capability("ocr")
        params: dict[str, Any] = {"text": text}
        if region is not None:
            params["region"] = region.to_argus()
        if case_sensitive:
            params["case_sensitive"] = True
        condition = ConditionDraft(type="text_not_present" if negated else "text_present",
                                   params=params)
        return self.authoring.add_verification(
            condition, wait=wait and not negated, timeout=timeout, index=index,
            provenance=Provenance(source="manual", capture_id=capture.id if capture else None,
                                  session_id=self.session.id if self.session else None),
        )

    def accept_suggestion(self, candidate: AssertionCandidate, *, index: int | None = None,
                          label: str | None = None) -> StepDraft:
        condition = candidate.condition
        capture = self.captures.get(candidate.capture_id) if (
            self.captures and candidate.capture_id
        ) else None
        if condition.type == "image_present" and "image" not in condition.params:
            if capture is None or candidate.region is None:
                raise RecordingError("The suggested image region is no longer available.")
            return self.add_image_verification(
                capture, candidate.region, label=label or "suggested region",
                threshold=float(condition.params.get("threshold", 0.9)),
                wait=candidate.synchronize, index=index,
            )
        return self.authoring.add_verification(
            condition.model_copy(deep=True), wait=candidate.synchronize, timeout="10s",
            index=index,
            provenance=Provenance(source="suggestion", capture_id=candidate.capture_id,
                                  note=candidate.reason),
        )

    # -- validation / quality / run ----------------------------------------------------------

    def validate(self, *, with_argus: bool = False) -> list[ValidationIssue]:
        document = self.authoring.document
        asset_root = self.project.assets_dir if self.project else None
        issues = DocumentValidator(asset_root=asset_root).validate(document)
        argus_checked = False
        if with_argus and not any(i.is_error for i in issues) and self.project is not None:
            self.save_test()
            result = self.argus.validate(self.project.config_path, test_id=document.metadata.id)
            issues.extend(result.issues)
            argus_checked = True
        self.events.publish(ValidationCompleted(issues=issues, argus_checked=argus_checked))
        return issues

    def quality(self) -> QualityReport:
        size = self.recorder.screen_size() if self.recorder and self.recorder.connected else None
        return TestQualityAnalyzer(screen_size=size).analyze(self.authoring.document)

    def run_with_argus(self, on_output: Callable[[str], None] | None = None) -> Job[ArgusRunResult]:  # noqa: E501
        project = self.require_project()
        issues = self.validate()
        errors = [i for i in issues if i.is_error]
        if errors:
            raise CreatorError(
                f"Fix {len(errors)} validation error(s) before running.",
                remediation=errors[0].fix or errors[0].message,
            )
        self.save_test()
        test_id = self.authoring.document.metadata.id
        argus = self.argus
        argus.require()

        def emit(line: str) -> None:
            self.events.publish(RunOutput(line=line))
            if on_output is not None:
                on_output(line)

        def work() -> ArgusRunResult:
            self.events.publish(RunStarted(test_id=test_id))
            result = argus.run_test(project.config_path, test_id, on_output=emit)
            self._last_run = result
            self.events.publish(RunFinished(result=result))
            return result

        return self.workers.submit("argus-run", work)

    @property
    def last_run(self) -> ArgusRunResult | None:
        return self._last_run

    # -- fake target replay ---------------------------------------------------------------------

    def _fake_replay_target(self, target: TargetProfile) -> TargetProfile:
        """Make the fake target runnable by Argus: replay the recording's last frame.

        Argus's fake device serves PNGs from ``screenshot_dir`` in order and
        holds the last one, so the final observed screen is what assertions see.
        """
        project = self.require_project()
        frames_dir = project.root / FAKE_FRAMES_DIR
        latest: Path | None = None
        if self.session is not None:
            captures = sorted(self.session.captures.all(), key=lambda c: c.timestamp)
            if captures:
                latest = Path(captures[-1].path)
        if latest is not None and latest.is_file():
            frames_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(latest, frames_dir / "frame_001.png")
        size = list(self.recorder.screen_size()) if self.recorder else [1280, 720]
        options = {**target.argus_device_options, "screen_size": size,
                   "screenshot_dir": FAKE_FRAMES_DIR.as_posix()}
        return target.model_copy(update={"argus_device_options": options})


def _sync_argus_options(target: TargetProfile) -> TargetProfile:
    """Mirror recorder settings into the Argus device options where they are the same thing."""
    options = dict(target.argus_device_options)
    if target.adapter == "browser":
        for key in ("url", "browser", "viewport"):
            if target.settings.get(key):
                options[key] = target.settings[key]
        options["headless"] = True  # Argus runs unattended; the Creator records headed
    elif target.adapter == "android":
        for key in ("serial", "app_package", "app_activity", "adb_path"):
            if target.settings.get(key):
                options[key] = target.settings[key]
    elif target.adapter == "desktop":
        for key in ("command", "args", "cwd"):
            if target.settings.get(key):
                options[key] = target.settings[key]
    return target.model_copy(update={"argus_device_options": options})


def _session_dirname() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _warning(code: str, message: str) -> Any:
    from argus_test_creator.models.authoring import AuthoringWarning

    return AuthoringWarning(code=code, message=message)
