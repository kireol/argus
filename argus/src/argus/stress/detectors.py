"""Failure detectors — combine explicit, visual, behavioural and consistency signals.

Every detector is one component that inspects the context after an action
(or a mutation) and returns zero or more structured :class:`Failure`s with
its own severity and confidence. Infrastructure problems are classified as
such — never as application bugs. New detectors register through
``DetectorRegistry.register`` or the ``argus.stress.detectors`` entry-point
group.

Visual signals reuse Argus facilities: screenshots from the device, OCR from
the configured provider, image comparison from :mod:`argus.verifiers.image`.
"""

from __future__ import annotations

from importlib import metadata
from typing import TYPE_CHECKING, Any

import numpy as np

from argus.stress.models import (
    ActionOutcome,
    Failure,
    FailureCategory,
    FailureSeverity,
    Mutation,
    MutationOutcome,
    StressAction,
)

if TYPE_CHECKING:
    from argus.stress.context import ObservationRecord, StressContext


class FailureDetector:
    """Base class: override ``after_action`` and/or ``after_mutation``."""

    name: str = "detector"
    #: Detectors needing a fresh observation are only run when one was taken.
    needs_observation: bool = False

    def after_action(self, context: StressContext, action: StressAction,
                     outcome: ActionOutcome, before: ObservationRecord | None,
                     after: ObservationRecord | None) -> list[Failure]:
        return []

    def after_mutation(self, context: StressContext, mutation: Mutation,
                       outcome: MutationOutcome) -> list[Failure]:
        return []

    def make(self, context: StressContext, *, category: FailureCategory,
             severity: FailureSeverity, message: str, action: StressAction | None = None,
             mutation: Mutation | None = None, confidence: float = 1.0,
             **details: Any) -> Failure:
        return Failure(
            failure_id=context.new_failure_id(), category=category, severity=severity,
            message=message, detector=self.name, step=context.step, timestamp=context.timestamp(),
            elapsed=context.elapsed, action=action, mutation=mutation, confidence=confidence,
            details=details, recent_sequence=context.recent_sequence(20),
        )


# -- explicit -----------------------------------------------------------------------------


class ActionErrorDetector(FailureDetector):
    """An action's own outcome: application error vs. infrastructure vs. unsupported."""

    name = "action_error"

    def after_action(self, context, action, outcome, before, after):
        if outcome.passed:
            return []
        if outcome.error_kind == "unsupported":
            return [self.make(context, category=FailureCategory.UNSUPPORTED,
                              severity=FailureSeverity.INFO, action=action,
                              message=f"{action.action_type} unsupported: {outcome.message}")]
        if outcome.error_kind == "infrastructure":
            context.infrastructure_error(f"{action.describe()}: {outcome.message}")
            return [self.make(context, category=FailureCategory.INFRASTRUCTURE,
                              severity=FailureSeverity.WARNING, action=action,
                              message=f"{action.action_type} failed in the harness: "
                                      f"{outcome.message}")]
        return [self.make(context, category=FailureCategory.APPLICATION,
                          severity=FailureSeverity.ERROR, action=action,
                          message=f"{action.action_type} failed: {outcome.message}")]


class MutationErrorDetector(FailureDetector):
    """Backend refused an applied-allowed mutation → backend failure (not app)."""

    name = "mutation_error"

    def after_mutation(self, context, mutation, outcome):
        if outcome.applied or outcome.blocked:
            return []
        category = (FailureCategory.BACKEND if outcome.error_kind == "backend"
                    else FailureCategory.INFRASTRUCTURE)
        if category == FailureCategory.INFRASTRUCTURE:
            context.infrastructure_error(f"{mutation.describe()}: {outcome.reason}")
        return [self.make(context, category=category, severity=FailureSeverity.WARNING,
                          mutation=mutation,
                          message=f"{mutation.describe()} not applied: {outcome.reason}")]


class CrashDetector(FailureDetector):
    name = "crash"

    def after_action(self, context, action, outcome, before, after):
        device = context.device
        if device is None or not device.capabilities.supports_app_lifecycle:
            return []
        if action.action_type in ("background", "home", "restart", "reload"):
            return []  # leaving the app on purpose
        try:
            running = device.is_application_running()
        except Exception:  # noqa: BLE001 - not observable → no signal
            return []
        if running:
            context.state.pop("crash_reported", None)
            return []
        if context.state.get("crash_reported"):
            return []
        context.state["crash_reported"] = True
        return [self.make(context, category=FailureCategory.CRASH,
                          severity=FailureSeverity.CRITICAL, action=action,
                          message="Application is no longer running after "
                                  f"{action.describe()}")]


# -- visual ----------------------------------------------------------------------------------


def _mean_abs_diff(a: ObservationRecord, b: ObservationRecord) -> float:
    """Normalised mean absolute pixel difference in [0, 1] (0 = identical)."""
    ia = np.asarray(a.image.convert("L"), dtype=np.int16)
    ib = np.asarray(b.image.convert("L"), dtype=np.int16)
    if ia.shape != ib.shape:
        return 1.0
    return float(np.abs(ia - ib).mean() / 255.0)


class BlankScreenDetector(FailureDetector):
    name = "blank_screen"
    needs_observation = True

    def __init__(self, *, max_std: float = 2.0) -> None:
        self._max_std = max_std

    def after_action(self, context, action, outcome, before, after):
        if after is None or action.action_type in ("background", "home"):
            return []
        gray = np.asarray(after.image.convert("L"), dtype=np.float32)
        std = float(gray.std())
        if std > self._max_std:
            context.state.pop("blank_reported", None)
            return []
        if context.state.get("blank_reported"):
            return []
        context.state["blank_reported"] = True
        return [self.make(context, category=FailureCategory.VISUAL, severity=FailureSeverity.ERROR,
                          action=action, confidence=0.7,
                          message=f"Screen is blank/uniform after {action.describe()} "
                                  f"(std {std:.2f})", std=std)]


class FrozenScreenDetector(FailureDetector):
    """The screen has not changed across N consecutive change-expecting actions."""

    name = "frozen_screen"
    needs_observation = True

    def after_action(self, context, action, outcome, before, after):
        cfg = context.config.failures
        if after is None or before is None:
            return []
        if action.action_type in ("wait", "type_text", "clear_text", "enter"):
            return []
        diff = _mean_abs_diff(before, after)
        count = context.state.get("unchanged_streak", 0)
        if diff > cfg.unchanged_threshold:
            context.state["unchanged_streak"] = 0
            context.state.pop("frozen_reported", None)
            return []
        count += 1
        context.state["unchanged_streak"] = count
        if count < cfg.frozen_after_actions or context.state.get("frozen_reported"):
            return []
        context.state["frozen_reported"] = True
        return [self.make(context, category=FailureCategory.HANG, severity=FailureSeverity.ERROR,
                          action=action, confidence=0.6,
                          message=f"Screen unchanged after {count} consecutive actions — "
                                  "application may be hung or unresponsive",
                          unchanged_actions=count, diff=diff)]


class ErrorScreenDetector(FailureDetector):
    """OCR finds a known error phrase on screen."""

    name = "error_screen"
    needs_observation = True

    def after_action(self, context, action, outcome, before, after):
        if after is None or context.ocr is None:
            return []
        result = context.ocr_for(after)
        if result is None:
            return []
        text = result.text.lower()
        words = [w for w in context.config.failures.error_words if w.lower() in text]
        if not words:
            context.state.pop("error_screen_reported", None)
            return []
        if context.state.get("error_screen_reported") == words[0]:
            return []
        context.state["error_screen_reported"] = words[0]
        return [self.make(context, category=FailureCategory.APPLICATION,
                          severity=FailureSeverity.ERROR, action=action, confidence=0.75,
                          message=f"Error screen: found {words[0]!r} after {action.describe()}",
                          matched=words)]


# -- backend consistency --------------------------------------------------------------------


class StaleEntityDetector(FailureDetector):
    """A deleted/changed entity's label is still visible after the reconcile window."""

    name = "stale_entity"
    needs_observation = True

    def after_action(self, context, action, outcome, before, after):
        if after is None or context.ocr is None:
            return []
        applied = context.state.get("applied_mutations", [])
        if not applied:
            return []
        cfg = context.config.failures
        grace = context.config.backend_mutations.reconcile_timeout_seconds
        window = cfg.stale_window_actions
        failures: list[Failure] = []
        result = None
        remaining = []
        reported: set[str] = context.state.setdefault("stale_reported", set())
        for entry in applied:
            step, elapsed, mutation, m_outcome, baseline = (*entry, "")[:5]
            if mutation.mutation_type not in ("delete", "disable", "archive", "update"):
                continue
            if context.step - step > window:
                continue  # window over: whatever shows now is unrelated
            label = mutation.metadata.get("label") or _label_from(context, mutation)
            if not label or len(label) < 3:
                continue
            remaining.append((step, elapsed, mutation, m_outcome, baseline))
            if context.elapsed - elapsed < grace:
                continue
            key = f"{mutation.mutation_type}:{mutation.entity_type}/{mutation.entity_id}"
            if result is None:
                result = context.ocr_for(after)
                if result is None:
                    return []
            text = result.text.lower()
            lowered = label.lower()
            if mutation.mutation_type == "update":
                changed = {k for k, v in mutation.parameters.items()
                          if isinstance(v, str) and v and v != "<MISSING>"}
                if not changed or f"update:{key}" in reported:
                    continue
                if lowered in text and any(
                    str(mutation.parameters.get(f, "")).lower() not in text for f in changed
                ):
                    reported.add(f"update:{key}")
                    failures.append(self.make(
                        context, category=FailureCategory.STALE_STATE,
                        severity=FailureSeverity.WARNING, action=action, mutation=mutation,
                        confidence=0.6,
                        message=f"UI still shows old {mutation.entity_type} {label!r} "
                                f"{grace:.1f}s after backend update"))
                continue
            if lowered not in text:
                continue
            # A success message that was NOT on screen when the mutation landed but is
            # now — for the very entity that was removed — is an operation on stale state.
            success = next((w for w in cfg.success_words
                            if w.lower() in text and w.lower() not in baseline), None)
            if success is not None and f"success:{key}" not in reported:
                reported.add(f"success:{key}")
                failures.append(self.make(
                    context, category=FailureCategory.UNEXPECTED_SUCCESS,
                    severity=FailureSeverity.CRITICAL, action=action, mutation=mutation,
                    confidence=0.85,
                    message=f"{success!r} shown for {mutation.entity_type} {label!r} after "
                            f"backend {mutation.mutation_type} — operation succeeded on "
                            "stale state", success_phrase=success))
            elif success is None and f"stale:{key}" not in reported and lowered not in baseline:
                # The label re-appeared (it was not visible when the mutation landed).
                reported.add(f"stale:{key}")
                failures.append(self.make(
                    context, category=FailureCategory.STALE_STATE,
                    severity=FailureSeverity.WARNING, action=action, mutation=mutation,
                    confidence=0.5,
                    message=f"{mutation.entity_type} {label!r} is (still) visible "
                            f"{grace:.1f}s after backend {mutation.mutation_type}"))
        context.state["applied_mutations"] = remaining
        return failures


def _label_from(context: StressContext, mutation: Mutation) -> str | None:
    items = context.state.get(f"entities:{mutation.entity_type}", [])
    if mutation.entity_id is None:
        return None
    for item in items:
        if str(item.get("id")) == str(mutation.entity_id):
            for key in ("title", "name", "label"):
                if item.get(key):
                    return str(item[key])
    return None


# -- registry ------------------------------------------------------------------------------------


BUILTIN_DETECTORS: tuple[type[FailureDetector], ...] = (
    ActionErrorDetector, MutationErrorDetector, CrashDetector, BlankScreenDetector,
    FrozenScreenDetector, ErrorScreenDetector, StaleEntityDetector,
)


class DetectorRegistry:
    ENTRY_POINT_GROUP = "argus.stress.detectors"

    def __init__(self, *, load_builtin: bool = True, disabled: set[str] | None = None) -> None:
        self._detectors: dict[str, FailureDetector] = {}
        self._disabled = disabled or set()
        if load_builtin:
            for cls in BUILTIN_DETECTORS:
                self.register(cls())
            self._load_entry_points()

    def register(self, detector: FailureDetector) -> None:
        self._detectors[detector.name] = detector

    def names(self) -> list[str]:
        return sorted(self._detectors)

    def active(self) -> list[FailureDetector]:
        return [d for n, d in sorted(self._detectors.items()) if n not in self._disabled]

    def run_after_action(self, context: StressContext, action: StressAction,
                         outcome: ActionOutcome, before: ObservationRecord | None,
                         after: ObservationRecord | None) -> list[Failure]:
        failures: list[Failure] = []
        for detector in self.active():
            if detector.needs_observation and after is None:
                continue
            try:
                failures.extend(detector.after_action(context, action, outcome, before, after))
            except Exception as exc:  # noqa: BLE001 - a detector bug is infrastructure
                context.infrastructure_error(f"detector {detector.name} raised: {exc}")
        return failures

    def run_after_mutation(self, context: StressContext, mutation: Mutation,
                           outcome: MutationOutcome) -> list[Failure]:
        failures: list[Failure] = []
        for detector in self.active():
            try:
                failures.extend(detector.after_mutation(context, mutation, outcome))
            except Exception as exc:  # noqa: BLE001
                context.infrastructure_error(f"detector {detector.name} raised: {exc}")
        return failures

    def _load_entry_points(self) -> None:
        try:
            entry_points = list(metadata.entry_points(group=self.ENTRY_POINT_GROUP))
        except Exception:  # noqa: BLE001
            return
        for entry_point in entry_points:
            try:
                entry_point.load()(self)
            except Exception:  # noqa: BLE001
                continue


__all__ = [
    "BUILTIN_DETECTORS", "ActionErrorDetector", "BlankScreenDetector", "CrashDetector",
    "DetectorRegistry", "ErrorScreenDetector", "FailureDetector", "FrozenScreenDetector",
    "MutationErrorDetector", "StaleEntityDetector",
]
