"""StressContext — everything one stress run owns, dependency-injected.

One context per run: its own RNG, clock, trace, histories, failures and
artifacts. No globals, so independent runs can execute concurrently.
"""

from __future__ import annotations

import collections
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PIL.Image import Image

from argus import __version__
from argus.adapters.backend import BackendAdapter
from argus.adapters.base import Device
from argus.artifacts.manager import TestArtifacts
from argus.config.models import AppConfig
from argus.events.bus import EventBus
from argus.exceptions import UTFError
from argus.logging import ContextLogger, get_logger
from argus.models.observation import Observation
from argus.stress.clock import Clock, MonotonicClock
from argus.stress.config import StressConfig
from argus.stress.models import (
    ActionOutcome,
    EntityRef,
    Failure,
    Fault,
    Mutation,
    MutationOutcome,
    StressAction,
    StressSummary,
    TraceEvent,
    TraceEventType,
)
from argus.stress.rng import DeterministicRNG
from argus.stress.trace import Trace

if TYPE_CHECKING:
    from argus.ocr.base import OCRProvider, OCRResult
    from argus.stress.faults import FaultInjector
    from argus.stress.mutations.backend import MutationBackend


@dataclass
class ObservationRecord:
    """A retained observation (bounded ring) with lazily cached OCR."""

    step: int
    elapsed: float
    observation: Observation
    ocr: OCRResult | None = None
    ocr_attempted: bool = False

    @property
    def image(self) -> Image:
        return self.observation.image


@dataclass
class StressContext:
    run_id: str
    seed: int
    config: StressConfig
    app_config: AppConfig
    rng: DeterministicRNG
    artifacts: TestArtifacts
    trace: Trace
    clock: Clock = field(default_factory=MonotonicClock)
    events: EventBus = field(default_factory=EventBus)
    device: Device | None = None
    device_name: str | None = None
    backend: BackendAdapter | None = None
    mutation_backend: MutationBackend | None = None
    fault_injector: FaultInjector | None = None
    ocr: OCRProvider | None = None
    dry_run: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)
    logger: ContextLogger = field(default_factory=lambda: get_logger("argus.stress"))
    #: Entities believed to be on screen right now (context extractors fill this).
    entity_context: list[EntityRef] = field(default_factory=list)
    #: Free-form scratch space for components (e.g. a detector's baseline).
    state: dict[str, Any] = field(default_factory=dict)
    infrastructure_errors: list[str] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    summary: StressSummary = field(default_factory=StressSummary)
    argus_version: str = __version__

    def __post_init__(self) -> None:
        limit = self.config.evidence.history
        self.action_history: collections.deque[tuple[StressAction, ActionOutcome]] = (
            collections.deque(maxlen=limit)
        )
        self.mutation_history: collections.deque[tuple[Mutation, MutationOutcome]] = (
            collections.deque(maxlen=limit)
        )
        self.observations: collections.deque[ObservationRecord] = collections.deque(
            maxlen=self.config.evidence.observations
        )
        self.active_faults: list[Fault] = []
        self._started = self.clock.monotonic()
        self._step = 0
        self._failure_seq = 0
        self._screen_size: tuple[int, int] | None = None
        self.logger = self.logger.bind(run_id=self.run_id)

    # -- time / counters ---------------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return self.clock.monotonic() - self._started

    @property
    def step(self) -> int:
        return self._step

    def timestamp(self) -> str:
        return self.clock.now().isoformat()

    def sleep(self, seconds: float) -> None:
        """Cooperative sleep — returns early when cancelled."""
        if seconds <= 0:
            return
        if self.cancel.is_set():
            return
        # Real clocks wait in slices so Ctrl+C stays responsive; fake clocks jump.
        if isinstance(self.clock, MonotonicClock):
            deadline = self.clock.monotonic() + seconds
            while not self.cancel.is_set():
                remaining = deadline - self.clock.monotonic()
                if remaining <= 0:
                    break
                self.clock.sleep(min(remaining, 0.1))
        else:
            self.clock.sleep(seconds)

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    # -- device -----------------------------------------------------------------------------

    def require_device(self) -> Device:
        if self.device is None:
            raise UTFError(
                "The stress scenario needs a device but none is configured.",
                remediation="Set stress.device (or configure exactly one device).",
            )
        return self.device

    def screen_size(self) -> tuple[int, int]:
        if self._screen_size is None:
            size: tuple[int, int] | None = None
            if self.device is not None:
                try:
                    size = self.device.get_screen_size()
                except Exception:  # noqa: BLE001 - fall back to the last screenshot
                    size = None
            if size is None and self.observations:
                size = self.observations[-1].image.size
            self._screen_size = size or (1280, 720)
        return self._screen_size

    # -- observation -------------------------------------------------------------------------

    def observe(self) -> ObservationRecord | None:
        """Capture a screenshot into the bounded ring (never OCR here)."""
        if self.device is None or not self.device.capabilities.supports_screenshot:
            return None
        image = self.device.screenshot()
        observation = Observation(image=image, device=self.device.name)
        record = ObservationRecord(step=self._step, elapsed=self.elapsed, observation=observation)
        self.observations.append(record)
        self.summary.observations += 1
        self._screen_size = image.size
        return record

    @property
    def last_observation(self) -> ObservationRecord | None:
        return self.observations[-1] if self.observations else None

    def previous_observation(self) -> ObservationRecord | None:
        return self.observations[-2] if len(self.observations) >= 2 else None

    def ocr_for(self, record: ObservationRecord) -> OCRResult | None:
        """OCR a retained observation once (cached on the record)."""
        if record.ocr_attempted:
            return record.ocr
        record.ocr_attempted = True
        if self.ocr is None:
            return None
        try:
            record.ocr = self.ocr.extract_text(record.image)
        except Exception as exc:  # noqa: BLE001 - OCR is best-effort evidence
            self.logger.debug("OCR failed: %s", exc)
            record.ocr = None
        return record.ocr

    # -- trace -----------------------------------------------------------------------------

    def _append(self, event_type: TraceEventType, **fields: Any) -> TraceEvent:
        return self.trace.append(event_type, elapsed=self.elapsed, timestamp=self.timestamp(),
                                 **fields)

    def record_action(self, action: StressAction, outcome: ActionOutcome,
                      *, delay: float | None = None) -> TraceEvent:
        self._step += 1
        self.summary.actions += 1
        self.action_history.append((action, outcome))
        event = self._append(TraceEventType.ACTION, action=action, action_outcome=outcome,
                             delay=delay)
        self.logger.info("action %s %s", action.describe(), "ok" if outcome.passed else
                         f"failed: {outcome.message}", extra={"sequence": event.sequence})
        return event

    def record_mutation(self, mutation: Mutation, outcome: MutationOutcome) -> TraceEvent:
        if outcome.applied:
            self.summary.mutations += 1
        elif outcome.blocked:
            self.summary.mutations_blocked += 1
        self.mutation_history.append((mutation, outcome))
        event = self._append(TraceEventType.MUTATION, mutation=mutation,
                             mutation_outcome=outcome)
        self.logger.info("mutation %s %s", mutation.describe(),
                         "applied" if outcome.applied else f"not applied: {outcome.reason}",
                         extra={"sequence": event.sequence})
        return event

    def record_fault(self, fault: Fault, *, cleared: bool = False) -> TraceEvent:
        if not cleared:
            self.summary.faults += 1
            self.active_faults.append(fault)
        else:
            self.active_faults = [f for f in self.active_faults if f != fault]
        return self._append(TraceEventType.FAULT_CLEARED if cleared else TraceEventType.FAULT,
                            fault=fault)

    def record_wait(self, seconds: float, reason: str = "") -> TraceEvent:
        return self._append(TraceEventType.WAIT, delay=seconds, metadata={"reason": reason})

    def note(self, message: str, **metadata: Any) -> TraceEvent:
        return self._append(TraceEventType.NOTE, metadata={"message": message, **metadata})

    # -- failures ------------------------------------------------------------------------------

    def new_failure_id(self) -> str:
        self._failure_seq += 1
        return f"{self.run_id}-F{self._failure_seq:03d}"

    def record_failure(self, failure: Failure) -> TraceEvent:
        self.failures.append(failure)
        by_sev = self.summary.failures_by_severity
        by_sev[failure.severity.value] = by_sev.get(failure.severity.value, 0) + 1
        by_cat = self.summary.failures_by_category
        by_cat[failure.category.value] = by_cat.get(failure.category.value, 0) + 1
        event = self._append(TraceEventType.FAILURE, failure_id=failure.failure_id,
                             metadata={"category": failure.category.value,
                                       "severity": failure.severity.value,
                                       "detector": failure.detector,
                                       "message": failure.message})
        self.logger.warning("failure %s [%s/%s] %s", failure.failure_id,
                            failure.category.value, failure.severity.value, failure.message,
                            extra={"sequence": event.sequence})
        return event

    def infrastructure_error(self, message: str) -> None:
        self.infrastructure_errors.append(message)
        self.logger.error("infrastructure: %s", message)

    def recent_sequence(self, n: int | None = None) -> tuple[int, ...]:
        events = self.trace.recent(n or self.config.evidence.history)
        return tuple(e.sequence for e in events)


__all__ = ["ObservationRecord", "StressContext"]
