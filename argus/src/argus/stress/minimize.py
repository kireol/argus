"""Failure minimization — delta debugging over a recorded trace.

Given a run whose trace reproduces a failure, find a shorter sequence of
steps (action + its attached mutations/faults) that still reproduces the
*same* failure signature (category + detector by default). Classic ddmin:
try removing chunks, replay, keep the reduction when the predicate holds,
halve the chunk size when nothing can be removed, stop at granularity 1 or
the iteration budget. Replays are cached by step subset.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from argus.stress.engine import StepPlan, StressRunResult, _scripted_plans
from argus.stress.models import TraceEvent, TraceEventType

if TYPE_CHECKING:
    from argus.stress.config import StressConfig
    from argus.stress.engine import StressEngine

ReproductionPredicate = Callable[[StressRunResult], bool]


def same_signature(signature: str) -> ReproductionPredicate:
    def predicate(result: StressRunResult) -> bool:
        return any(f.signature == signature for f in result.application_failures)
    return predicate


def any_application_failure(result: StressRunResult) -> bool:
    return bool(result.application_failures)


@dataclass
class MinimizeResult:
    original_steps: int
    minimized_steps: int
    plans: list[StepPlan]
    events: list[TraceEvent]
    iterations: int
    replays: int
    reproduced: bool
    final_run: StressRunResult | None = None
    history: list[tuple[int, bool]] = field(default_factory=list)

    @property
    def reduction(self) -> float:
        if not self.original_steps:
            return 0.0
        return 1.0 - self.minimized_steps / self.original_steps


class Minimizer:
    def __init__(self, engine: StressEngine, scenario: StressConfig, *, seed: int,
                 predicate: ReproductionPredicate, max_iterations: int | None = None,
                 source_run_id: str | None = None, dry_run: bool = False) -> None:
        self._engine = engine
        self._scenario = scenario
        self._seed = seed
        self._predicate = predicate
        self._max_iterations = max_iterations or scenario.minimize_max_iterations
        self._source = source_run_id
        self._dry_run = dry_run
        self._cache: dict[tuple[int, ...], bool] = {}
        self.replays = 0
        self.on_progress: Callable[[int, int, bool], None] | None = None

    def minimize(self, events: list[TraceEvent]) -> MinimizeResult:
        plans = list(_scripted_plans(events))
        indices = list(range(len(plans)))
        if not plans:
            return MinimizeResult(0, 0, [], [], 0, 0, False)
        reproduced = self._reproduces(plans, indices)
        iterations = 0
        history: list[tuple[int, bool]] = [(len(indices), reproduced)]
        if reproduced:
            granularity = 2
            while len(indices) >= 2 and iterations < self._max_iterations:
                chunk = max(len(indices) // granularity, 1)
                chunks = [indices[i:i + chunk] for i in range(0, len(indices), chunk)]
                reduced = False
                for piece in chunks:
                    candidate = [i for i in indices if i not in piece]
                    if not candidate:
                        continue
                    iterations += 1
                    ok = self._reproduces(plans, candidate)
                    history.append((len(candidate), ok))
                    if self.on_progress is not None:
                        self.on_progress(iterations, len(candidate), ok)
                    if ok:
                        indices = candidate
                        granularity = max(granularity - 1, 2)
                        reduced = True
                        break
                    if iterations >= self._max_iterations:
                        break
                if not reduced:
                    if granularity >= len(indices):
                        break
                    granularity = min(granularity * 2, len(indices))
        final_plans = [plans[i] for i in indices]
        final_run = self._replay(final_plans, persist=True) if reproduced else None
        return MinimizeResult(
            original_steps=len(plans), minimized_steps=len(final_plans), plans=final_plans,
            events=_plans_to_events(final_plans), iterations=iterations, replays=self.replays,
            reproduced=reproduced, final_run=final_run, history=history,
        )

    def _reproduces(self, plans: list[StepPlan], indices: list[int]) -> bool:
        key = tuple(indices)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._replay([plans[i] for i in indices], persist=False)
        ok = self._predicate(result)
        self._cache[key] = ok
        return ok

    def _replay(self, plans: list[StepPlan], *, persist: bool) -> StressRunResult:
        self.replays += 1
        engine = self._engine
        previous = engine.persist
        engine.persist = persist and previous
        try:
            return engine.run(self._scenario, seed=self._seed, dry_run=self._dry_run,
                              script=_plans_to_events(plans),
                              replay_of=self._source if persist else None,
                              minimized_from=self._source if persist else None)
        finally:
            engine.persist = previous


def _plans_to_events(plans: list[StepPlan]) -> list[TraceEvent]:
    """Synthesize a minimal trace from plans (sequence numbers are re-issued)."""
    events: list[TraceEvent] = []
    seq = 0

    def add(event_type: TraceEventType, **fields: object) -> None:
        nonlocal seq
        seq += 1
        events.append(TraceEvent(sequence=seq, elapsed=0.0, timestamp="", event_type=event_type,
                                 **fields))  # type: ignore[arg-type]

    for plan in plans:
        for mutation in plan.before:
            add(TraceEventType.MUTATION, mutation=mutation)
        if plan.action is not None:
            add(TraceEventType.ACTION, action=plan.action, delay=plan.delay)
        for mutation in plan.after:
            add(TraceEventType.MUTATION, mutation=mutation)
        for fault in plan.faults:
            add(TraceEventType.FAULT, fault=fault)
    return events


__all__ = ["MinimizeResult", "Minimizer", "ReproductionPredicate", "any_application_failure",
           "same_signature"]
