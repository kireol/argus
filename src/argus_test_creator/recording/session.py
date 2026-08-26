"""RecordingSession — orchestrates adapter, journal, captures, OCR, normalization.

Thread model: the adapter pushes events into an EventSink from its own thread;
the session's drain thread persists them, captures "after" screenshots,
schedules OCR/diff on the worker pool, and publishes events. The UI only ever
subscribes to events and calls ``start``/``pause``/``resume``/``stop``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from PIL.Image import Image

from argus_test_creator.core.errors import RecordingError, ScreenshotError
from argus_test_creator.core.events import Event, EventBus
from argus_test_creator.core.ids import new_id
from argus_test_creator.core.logging import get_logger
from argus_test_creator.core.workers import WorkerPool
from argus_test_creator.models.capabilities import TargetProfile
from argus_test_creator.models.recording import (
    NormalizedAction,
    OCRObservation,
    RecordingEvent,
    RecordingEventType,
    RecordingMode,
    ScreenCapture,
)
from argus_test_creator.observation.captures import CaptureStore
from argus_test_creator.observation.diff import ScreenDiff, compare_images
from argus_test_creator.observation.ocr import OCRProvider
from argus_test_creator.observation.suggestions import AssertionCandidate, AssertionSuggester
from argus_test_creator.recording.adapter import EventSink, RecorderAdapter
from argus_test_creator.recording.journal import SessionJournal
from argus_test_creator.recording.normalizer import EventNormalizer

_log = get_logger("recording")


class RecordingSessionState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    DISCARDED = "discarded"


# -- events ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RecordingStarted(Event):
    session_id: str
    target_id: str


@dataclass(frozen=True, kw_only=True)
class RecordingPaused(Event):
    session_id: str


@dataclass(frozen=True, kw_only=True)
class RecordingResumed(Event):
    session_id: str


@dataclass(frozen=True, kw_only=True)
class RecordingStopped(Event):
    session_id: str
    event_count: int
    action_count: int


@dataclass(frozen=True, kw_only=True)
class ActionObserved(Event):
    session_id: str
    action: NormalizedAction


@dataclass(frozen=True, kw_only=True)
class ActionUpdated(Event):
    """A previously observed action was extended (e.g. more typed text)."""

    session_id: str
    action: NormalizedAction


@dataclass(frozen=True, kw_only=True)
class ScreenshotCaptured(Event):
    session_id: str
    capture: ScreenCapture


@dataclass(frozen=True, kw_only=True)
class OCRCompleted(Event):
    session_id: str
    observation: OCRObservation


@dataclass(frozen=True, kw_only=True)
class ScreenChanged(Event):
    session_id: str
    action_id: str
    diff: ScreenDiff


@dataclass(frozen=True, kw_only=True)
class AssertionSuggested(Event):
    session_id: str
    action_id: str
    candidates: list[AssertionCandidate]


@dataclass(frozen=True, kw_only=True)
class RecordingFailed(Event):
    session_id: str
    operation: str
    message: str
    remediation: str | None
    details: str | None


# -- session -----------------------------------------------------------------------------


class RecordingSession:
    def __init__(
        self,
        *,
        adapter: RecorderAdapter,
        directory: Path,
        events: EventBus,
        workers: WorkerPool,
        ocr: OCRProvider | None = None,
        mode: RecordingMode = RecordingMode.SMART,
        session_id: str | None = None,
        capture_after_actions: bool = True,
        settle_ms: int = 150,
        suggest: bool = True,
    ) -> None:
        self.id = session_id or new_id("session")
        self.adapter = adapter
        self.target: TargetProfile = adapter.target
        self.mode = mode
        self.directory = directory
        self._events = events
        self._workers = workers
        self._ocr = ocr
        self._journal = SessionJournal(directory)
        self.captures = CaptureStore(self._journal.screenshots_dir)
        self._normalizer = EventNormalizer()
        self._suggester = AssertionSuggester() if suggest else None
        self._sink: EventSink | None = None
        self._drain: threading.Thread | None = None
        self._state = RecordingSessionState.IDLE
        self._state_lock = threading.Lock()
        self._raw: list[RecordingEvent] = []
        self._actions: list[NormalizedAction] = []
        self._ocr_by_capture: dict[str, OCRObservation] = {}
        self._capture_after = capture_after_actions
        self._settle_ms = settle_ms
        self._sequence = 0
        self._start_capture_id: str | None = None
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None

    # -- state ----------------------------------------------------------------------------

    @property
    def state(self) -> RecordingSessionState:
        return self._state

    @property
    def raw_events(self) -> list[RecordingEvent]:
        return list(self._raw)

    @property
    def actions(self) -> list[NormalizedAction]:
        return list(self._actions)

    @property
    def event_count(self) -> int:
        return len(self._raw)

    def ocr_for(self, capture_id: str | None) -> OCRObservation | None:
        return self._ocr_by_capture.get(capture_id) if capture_id else None

    # -- lifecycle ------------------------------------------------------------------------

    def start(self) -> None:
        if self._state != RecordingSessionState.IDLE:
            raise RecordingError(f"Session is {self._state.value}; cannot start.")
        if not self.adapter.connected:
            self._guard("connect", self.adapter.connect)
        self._journal.open()
        self.started_at = datetime.now(UTC)
        self._sink = EventSink()
        self._state = RecordingSessionState.RECORDING
        self._drain = threading.Thread(
            target=self._drain_loop, name=f"drain-{self.id}", daemon=True
        )
        self._drain.start()
        try:
            self.adapter.start_recording(self._sink)
        except Exception as exc:  # noqa: BLE001 - adapter error → classified
            self._state = RecordingSessionState.IDLE
            self._sink.close()
            self._fail("start recording", exc)
            raise RecordingError(
                f"Recording could not start on {self.target.name}: {exc}",
                remediation="Reconnect the target and try again.",
            ) from exc
        self._checkpoint()
        try:
            image, metadata = self._screenshot()
            capture = self.captures.save(image, phase="start", metadata=metadata)
            self._start_capture_id = capture.id
            self._events.publish(ScreenshotCaptured(session_id=self.id, capture=capture))
            if self._ocr is not None:
                self._workers.submit("ocr:start", self.run_ocr, capture)
        except ScreenshotError as exc:
            self._start_capture_id = None
            self._fail("screenshot", exc)
        self._events.publish(RecordingStarted(session_id=self.id, target_id=self.target.id))

    def pause(self) -> None:
        with self._state_lock:
            if self._state != RecordingSessionState.RECORDING:
                return
            self._state = RecordingSessionState.PAUSED
            if self._sink is not None:
                self._sink.pause()
        self._events.publish(RecordingPaused(session_id=self.id))

    def resume(self) -> None:
        with self._state_lock:
            if self._state != RecordingSessionState.PAUSED:
                return
            self._state = RecordingSessionState.RECORDING
            if self._sink is not None:
                self._sink.resume()
        self._events.publish(RecordingResumed(session_id=self.id))

    def flush(self, timeout: float = 30.0) -> None:
        """Wait until queued events are persisted and background analysis finished."""
        if self._sink is not None:
            waiter = threading.Thread(target=self._sink.join, daemon=True)
            waiter.start()
            waiter.join(timeout)
        for job in self._workers.active_jobs():
            if job.name.startswith(("analyze:", "ocr:")):
                try:
                    job.result(timeout)
                except Exception:  # noqa: BLE001 - failures are reported via events
                    pass

    def stop(self) -> list[NormalizedAction]:
        if self._state in (RecordingSessionState.STOPPED, RecordingSessionState.DISCARDED):
            return self.actions
        if self._state == RecordingSessionState.IDLE:
            raise RecordingError("Session was never started.")
        try:
            self.adapter.stop_recording()
        except Exception as exc:  # noqa: BLE001
            self._fail("stop recording", exc)
        assert self._sink is not None
        self._sink.close()
        if self._drain is not None:
            self._drain.join(timeout=10)
        self._sink.task_done()  # the None sentinel
        self._state = RecordingSessionState.STOPPED
        self.stopped_at = datetime.now(UTC)
        self._journal.close()
        self._checkpoint()
        self._events.publish(RecordingStopped(
            session_id=self.id, event_count=len(self._raw), action_count=len(self._actions)
        ))
        return self.actions

    def discard(self) -> None:
        if self._state == RecordingSessionState.RECORDING:
            self.stop()
        self._state = RecordingSessionState.DISCARDED
        self._checkpoint()

    # -- manual capture ---------------------------------------------------------------------

    def capture_now(self, *, phase: str = "manual") -> ScreenCapture:
        """Screenshot on demand (Add Verification). Runs on the caller's thread."""
        image, metadata = self._screenshot()
        capture = self.captures.save(image, phase=phase, metadata=metadata)
        self._events.publish(ScreenshotCaptured(session_id=self.id, capture=capture))
        return capture

    def run_ocr(self, capture: ScreenCapture, region: Any = None) -> OCRObservation | None:
        """Synchronous OCR (call from a worker or a test)."""
        if self._ocr is None:
            return None
        image = self.captures.load(capture)
        observation = self._ocr.extract(
            image, capture_id=capture.id, region=region, metadata=self.captures.metadata(capture)
        )
        self._ocr_by_capture[capture.id] = observation
        self._events.publish(OCRCompleted(session_id=self.id, observation=observation))
        return observation

    def renormalize(self, mode: RecordingMode) -> list[NormalizedAction]:
        """Re-run normalization (e.g. after switching exact ↔ smart)."""
        self.mode = mode
        self._actions = self._normalizer.normalize(self._raw, mode)
        return self.actions

    # -- recovery -----------------------------------------------------------------------------

    @classmethod
    def recover(
        cls, directory: Path, *, adapter: RecorderAdapter, events: EventBus, workers: WorkerPool,
        ocr: OCRProvider | None = None,
    ) -> RecordingSession:
        journal = SessionJournal(directory)
        snapshot = journal.read_snapshot() or {}
        session = cls(
            adapter=adapter, directory=directory, events=events, workers=workers, ocr=ocr,
            mode=RecordingMode(snapshot.get("mode", RecordingMode.SMART)),
            session_id=snapshot.get("id", directory.name),
        )
        session._raw = journal.read_events()
        session._sequence = len(session._raw)
        for capture_path in sorted(journal.screenshots_dir.glob("*.png")):
            from PIL import Image as PILImage

            with PILImage.open(capture_path) as img:
                width, height = img.size
            session.captures.register(ScreenCapture(
                id=capture_path.stem, path=str(capture_path), width=width, height=height,
            ))
        session._actions = session._normalizer.normalize(session._raw, session.mode)
        session._state = RecordingSessionState.STOPPED
        session._checkpoint()
        return session

    # -- internals ------------------------------------------------------------------------------

    def _drain_loop(self) -> None:
        assert self._sink is not None
        pending: list[RecordingEvent] = []
        while True:
            event = self._sink.pop(timeout=0.2)
            if event is None:
                if self._sink.closed and self._sink.qsize() == 0:
                    break
                continue
            try:
                self._handle_event(event, pending)
            except Exception as exc:  # noqa: BLE001 - keep draining
                self._fail("process event", exc)
            finally:
                self._sink.task_done()
        if pending:
            self._flush_actions(pending)

    def _handle_event(self, event: RecordingEvent, pending: list[RecordingEvent]) -> None:
        self._sequence += 1
        event = event.model_copy(update={"sequence": self._sequence})
        discrete = event.event_type not in (
            RecordingEventType.POINTER_MOVE, RecordingEventType.KEY_DOWN,
            RecordingEventType.KEY_UP,
        )
        gesture_end = event.event_type in (
            RecordingEventType.POINTER_UP, RecordingEventType.CLICK,
            RecordingEventType.DOUBLE_CLICK, RecordingEventType.KEY_PRESS,
            RecordingEventType.TEXT_INPUT, RecordingEventType.SCROLL,
            RecordingEventType.NAVIGATION, RecordingEventType.APP_STARTED,
            RecordingEventType.APP_STOPPED,
        )
        if gesture_end and self._capture_after and event.capture_after is None:
            if self._settle_ms:
                time.sleep(self._settle_ms / 1000)
            try:
                image, metadata = self._screenshot()
                capture = self.captures.save(
                    image, event_id=event.id, phase="after", metadata=metadata
                )
                event = event.model_copy(update={"capture_after": capture.id})
                self._events.publish(ScreenshotCaptured(session_id=self.id, capture=capture))
            except ScreenshotError as exc:
                self._fail("screenshot", exc)
        self._raw.append(event)
        self._journal.append_event(event)
        if discrete and len(self._raw) % 25 == 0:
            self._checkpoint()
        pending.append(event)
        if gesture_end:
            self._flush_actions(pending)

    def _flush_actions(self, pending: list[RecordingEvent]) -> None:
        # Normalize the whole stream (pure & cheap) and emit actions not yet seen.
        actions = self._normalizer.normalize(self._raw, self.mode)
        previous = {a.id: a for a in self._actions}
        self._actions = actions
        pending.clear()
        for action in actions:
            old = previous.get(action.id)
            if old is None:
                self._journal.append_action(action)
                self._events.publish(ActionObserved(session_id=self.id, action=action))
            elif old.source_event_ids != action.source_event_ids:
                # Smart mode extended an existing action (typing): update, don't duplicate.
                self._journal.append_action(action)
                self._events.publish(ActionUpdated(session_id=self.id, action=action))
            else:
                continue
            if action.capture_after:
                self._workers.submit(f"analyze:{action.id}", self._analyze, action)

    def _analyze(self, action: NormalizedAction) -> None:
        """Background: OCR the after-capture, diff against before, suggest assertions."""
        after = self.captures.get(action.capture_after or "")
        if after is None:
            return
        before_id = action.capture_before or self._previous_capture_id(action)
        before = self.captures.get(before_id) if before_id else None
        diff: ScreenDiff | None = None
        after_img = self.captures.load(after)
        try:
            if before is not None:
                before_img = self.captures.load(before)
                try:
                    diff = compare_images(before_img, after_img)
                finally:
                    before_img.close()
            ocr_after = self._ocr_by_capture.get(after.id)
            if ocr_after is None and self._ocr is not None and (diff is None or diff.significant):
                ocr_after = self._ocr.extract(
                    after_img, capture_id=after.id, metadata=self.captures.metadata(after)
                )
                self._ocr_by_capture[after.id] = ocr_after
                self._events.publish(OCRCompleted(session_id=self.id, observation=ocr_after))
        finally:
            after_img.close()
        if diff is not None and diff.significant:
            self._events.publish(ScreenChanged(session_id=self.id, action_id=action.id, diff=diff))
        if self._suggester is not None and (diff is None or diff.significant):
            candidates = self._suggester.suggest(
                diff=diff,
                ocr_before=self._ocr_by_capture.get(before_id or ""),
                ocr_after=self._ocr_by_capture.get(after.id),
                capture_after=after.id,
                screen_size=(after.width, after.height),
            )
            if candidates:
                self._events.publish(AssertionSuggested(
                    session_id=self.id, action_id=action.id, candidates=candidates
                ))

    def _previous_capture_id(self, action: NormalizedAction) -> str | None:
        previous: str | None = getattr(self, "_start_capture_id", None)
        for candidate in self._actions:
            if candidate.id == action.id:
                return previous
            if candidate.capture_after:
                previous = candidate.capture_after
        return previous

    def _screenshot(self) -> tuple[Image, dict[str, Any]]:
        try:
            image = self.adapter.screenshot()
        except ScreenshotError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ScreenshotError(
                f"Screenshot failed on {self.target.name}: {exc}",
                remediation="Reconnect the target and retry.",
                details=repr(exc),
            ) from exc
        metadata = getattr(self.adapter, "last_screen_metadata", None)
        return image, dict(metadata() if callable(metadata) else (metadata or {}))

    def _checkpoint(self) -> None:
        self._journal.write_snapshot({
            "id": self.id,
            "state": self._state.value,
            "mode": self.mode.value,
            "target": self.target.model_dump(mode="json"),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
            "event_count": len(self._raw),
            "action_count": len(self._actions),
        })

    def _guard(self, operation: str, fn: Any) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self._fail(operation, exc)
            raise

    def _fail(self, operation: str, exc: BaseException) -> None:
        remediation = getattr(exc, "remediation", None)
        details = getattr(exc, "details", None) or repr(exc)
        _log.warning("%s failed on %s: %s", operation, self.target.name, exc)
        self._events.publish(RecordingFailed(
            session_id=self.id, operation=operation, message=str(getattr(exc, "message", exc)),
            remediation=remediation, details=details,
        ))
