"""StressEngine — the run loop shared by monkey, stress, chaos, replay and minimize.

    limits → plan step (random or scripted) → mutations(before) → action (+burst)
           → mutations(after) → faults → observe (sampled) → extract context
           → detectors → evidence → policy → delay

Randomness comes from ``context.rng`` only; time from ``context.clock``.
A scripted plan (replay/minimize) feeds recorded actions, mutations, faults
and delays through the *same* loop, so detectors and evidence behave identically.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus import __version__
from argus.artifacts.manager import TestArtifacts
from argus.config.models import AppConfig
from argus.engine.session import RunSession
from argus.events.bus import EventBus
from argus.exceptions import ConfigurationError, UTFError
from argus.logging import get_logger
from argus.stress.actions.base import StressActionRegistry
from argus.stress.capabilities import DeviceProbe
from argus.stress.clock import Clock, MonotonicClock
from argus.stress.config import StressConfig
from argus.stress.context import ObservationRecord, StressContext
from argus.stress.detectors import DetectorRegistry
from argus.stress.evidence import EvidenceCollector
from argus.stress.extractors import CompositeExtractor, OCRContextExtractor, StateContextExtractor
from argus.stress.faults import FaultInjector, FaultRegistry
from argus.stress.generator import ActionGenerator
from argus.stress.models import (
    ActionOutcome,
    Failure,
    FailureCategory,
    FailureSeverity,
    Fault,
    Mutation,
    StressAction,
    StressRunRecord,
    TraceEvent,
    TraceEventType,
)
from argus.stress.mutations.backend import (
    MutationBackend,
    RestMutationBackend,
    StateMutationBackend,
)
from argus.stress.mutations.data import DataMutationRegistry
from argus.stress.mutations.scheduler import MutationExecutor, MutationScheduler
from argus.stress.mutations.types import MutationRegistry
from argus.stress.rng import DeterministicRNG, new_seed
from argus.stress.runs import StressRunStore, new_run_id
from argus.stress.safety import SafetyPolicy
from argus.stress.targets import TargetProvider, TargetSelector
from argus.stress.trace import Trace

_log = get_logger("argus.stress.engine")

#: Consecutive infrastructure failures that abort a run (device gone, harness broken).
INFRASTRUCTURE_ABORT_STREAK = 3
#: In-memory trace tail when no run directory is used (dry-run/tests).
IN_MEMORY_TRACE = 20_000


@dataclass(frozen=True)
class StepPlan:
    """What one loop iteration does (random and scripted plans share this)."""

    action: StressAction | None
    before: tuple[Mutation, ...] = ()
    after: tuple[Mutation, ...] = ()
    faults: tuple[Fault, ...] = ()
    delay: float = 0.0
    burst: int = 0


@dataclass
class StressComponents:
    """Injectable collaborators (tests swap fakes in)."""

    actions: StressActionRegistry | None = None
    mutations: MutationRegistry | None = None
    data: DataMutationRegistry | None = None
    detectors: DetectorRegistry | None = None
    faults: FaultRegistry | None = None
    mutation_backend: MutationBackend | None = None
    fault_injector: FaultInjector | None = None
    target_providers: list[TargetProvider] = field(default_factory=list)


@dataclass
class StressRunResult:
    record: StressRunRecord
    run_dir: Path | None
    events: list[TraceEvent]

    @property
    def failures(self) -> list[Failure]:
        return self.record.failures

    @property
    def application_failures(self) -> list[Failure]:
        return [f for f in self.record.failures if f.category.is_application]


class StressEngine:
    def __init__(
        self,
        config: AppConfig,
        *,
        events: EventBus | None = None,
        clock: Clock | None = None,
        components: StressComponents | None = None,
        store: StressRunStore | None = None,
        persist: bool = True,
    ) -> None:
        self.config = config
        self.events = events or EventBus()
        self.clock: Clock = clock or MonotonicClock()
        self.components = components or StressComponents()
        self.persist = persist
        base = config.resolve_path(config.stress.results_dir)
        self.store = store or StressRunStore(base)

    # -- public API -------------------------------------------------------------------------

    def run(
        self,
        scenario: StressConfig,
        *,
        seed: int | None = None,
        dry_run: bool = False,
        device: str | None = None,
        cancel: threading.Event | None = None,
        script: list[TraceEvent] | None = None,
        replay_of: str | None = None,
        minimized_from: str | None = None,
        run_id: str | None = None,
    ) -> StressRunResult:
        seed = seed if seed is not None else (scenario.seed if scenario.seed else new_seed())
        run_id = run_id or new_run_id(self.clock.now(), suffix=f"{seed % 10000:04d}")
        dry_run = dry_run or scenario.safety.dry_run
        run_dir = self.store.run_dir(run_id, create=True) if self.persist else None
        run_id = run_dir.name if run_dir is not None else run_id
        trace = Trace(run_dir / "trace.jsonl" if run_dir else None,
                      tail=max(scenario.evidence.history, 20 if run_dir else IN_MEMORY_TRACE))
        record = StressRunRecord(
            run_id=run_id, seed=seed, argus_version=__version__, scenario_name=scenario.name,
            scenario=scenario.model_dump(mode="json"), started_at=self.clock.now().isoformat(),
            dry_run=dry_run, replay_of=replay_of, minimized_from=minimized_from,
            trace_path=str(trace.path) if trace.path else None,
            artifacts_dir=str(run_dir) if run_dir else None,
        )
        session = RunSession(self.config, self.events)
        context: StressContext | None = None
        try:
            context = self._build_context(session, scenario, seed=seed, run_id=run_id,
                                          dry_run=dry_run, device_name=device, trace=trace,
                                          run_dir=run_dir, cancel=cancel)
            record.device = context.device_name
            record.device_type = type(context.device).__name__ if context.device else None
            record.device_capabilities = DeviceProbe(context.device).summary()
            record.backend_id = (context.mutation_backend.identifier
                                 if context.mutation_backend else None)
            if run_dir is not None:
                self.store.save(record, run_dir)
            self._loop(context, scenario, script)
            record.status = "cancelled" if context.cancelled else "completed"
        except UTFError as exc:
            record.status = "errored"
            record.infrastructure_errors.append(str(exc))
            if context is None:
                trace.close()
                if run_dir is not None:
                    record.finished_at = self.clock.now().isoformat()
                    self.store.save(record, run_dir)
                raise
        finally:
            if context is not None:
                self._finish(context, record, trace, run_dir)
            with contextlib.suppress(Exception):
                session.close()
        events = trace.recent(IN_MEMORY_TRACE)
        return StressRunResult(record=record, run_dir=run_dir, events=events)

    # -- construction --------------------------------------------------------------------------

    def _build_context(
        self, session: RunSession, scenario: StressConfig, *, seed: int, run_id: str,
        dry_run: bool, device_name: str | None, trace: Trace, run_dir: Path | None,
        cancel: threading.Event | None,
    ) -> StressContext:
        name = device_name or scenario.device
        device = None
        if name is None:
            configured = [n for n, d in sorted(self.config.devices.items()) if d.configured]
            if len(configured) == 1:
                name = configured[0]
            elif len(configured) > 1:
                raise ConfigurationError(
                    "Several devices are configured; choose one for the stress run.",
                    remediation="Set stress.device or pass --device: "
                                + ", ".join(configured),
                )
        if name is not None:
            device = session.device(name)
        elif scenario.monkey.enabled:
            raise ConfigurationError(
                "Monkey testing needs a device but none is configured.",
                remediation="Configure a device (e.g. type: fake) or disable stress.monkey.",
            )
        backend = session.backend if session.backend_available else None
        comps = self.components
        mutation_backend = comps.mutation_backend
        if mutation_backend is None and backend is not None and scenario.backend_mutations.enabled:
            mutation_backend = self._mutation_backend(scenario, backend)
        ocr: Any = None
        if device is not None and callable(getattr(device, "screen_text", None)):
            # Instrumented devices expose their text layer — exact and free.
            from argus.stress.demo import DeviceTextOCRProvider

            ocr = DeviceTextOCRProvider(device)
        else:
            try:
                ocr = session.verifiers.ocr
                available, _reason = ocr.is_available()
                if not available:
                    ocr = None
            except Exception:  # noqa: BLE001 - OCR is optional
                ocr = None
        fault_injector = comps.fault_injector
        if fault_injector is None and scenario.faults.enabled:
            registry = comps.faults or FaultRegistry()
            fault_injector = registry.create(scenario.faults.injector, backend)
        artifacts = TestArtifacts(run_dir if run_dir else Path("."), save_enabled=run_dir is not None)  # noqa: E501
        context = StressContext(
            run_id=run_id, seed=seed, config=scenario, app_config=self.config,
            rng=DeterministicRNG(seed), artifacts=artifacts, trace=trace, clock=self.clock,
            events=self.events, device=device, device_name=name, backend=backend,
            mutation_backend=mutation_backend, fault_injector=fault_injector, ocr=ocr,
            dry_run=dry_run, cancel=cancel or threading.Event(),
        )
        return context

    def _mutation_backend(self, scenario: StressConfig, backend: Any) -> MutationBackend:
        cfg = scenario.backend_mutations
        style = cfg.style
        if style == "auto":
            rest = cfg.schema_endpoint is not None or any(e.path for e in cfg.entities.values())
            style = "rest" if rest else "state"
        env = scenario.safety.environment or None
        if style == "rest":
            return RestMutationBackend(backend, cfg.entities, schema_endpoint=cfg.schema_endpoint,
                                       environment=env)
        return StateMutationBackend(backend, cfg.entities, environment=env)

    def _assemble(self, context: StressContext, scenario: StressConfig) -> _Machinery:
        comps = self.components
        actions = comps.actions or StressActionRegistry()
        mutations = comps.mutations or MutationRegistry()
        data = comps.data or DataMutationRegistry()
        detectors = comps.detectors or DetectorRegistry(
            disabled=set(scenario.failures.disabled_detectors)
        )
        probe = DeviceProbe(context.device)
        targets = TargetSelector(scenario.monkey.targets)
        for provider in comps.target_providers:
            targets.add_provider(provider, first=True)
        generator = ActionGenerator(scenario.monkey, actions, probe, targets)
        for name, reason in generator.skipped.items():
            context.note(f"action {name} excluded: {reason}", action=name, reason=reason)
        scheduler = MutationScheduler(scenario.backend_mutations, context.mutation_backend,
                                      mutations, data,
                                      enabled_strategies=scenario.data_mutations.enabled)
        safety = SafetyPolicy(scenario.safety, dry_run=context.dry_run)
        executor = MutationExecutor(context.mutation_backend, mutations, safety, scheduler)
        extractors = CompositeExtractor([StateContextExtractor(scheduler),
                                         OCRContextExtractor(scheduler)])
        evidence = EvidenceCollector(scenario.evidence,
                                     Path(context.artifacts.directory) if
                                     context.artifacts._save_enabled else None)
        return _Machinery(actions, generator, scheduler, executor, extractors, detectors,
                          evidence, probe)

    # -- the loop --------------------------------------------------------------------------------

    def _loop(self, context: StressContext, scenario: StressConfig,
              script: list[TraceEvent] | None) -> None:
        m = self._assemble(context, scenario)
        limits = scenario.limits
        policy = scenario.failures
        context.trace.append(TraceEventType.RUN_STARTED, elapsed=0.0,
                             timestamp=context.timestamp(),
                             metadata={"seed": context.seed, "scenario": scenario.name,
                                       "device": context.device_name,
                                       "actions": m.generator.available,
                                       "dry_run": context.dry_run})
        self._safe_observe(context)
        m.extractors.update(context)
        plans: Iterator[StepPlan] = (_scripted_plans(script) if script is not None
                                     else _random_plans(context, scenario, m))
        consecutive = 0
        infra_streak = 0
        wall_started = time.monotonic()
        stop_reason = ""
        fault_expiry: list[tuple[float, Fault]] = []
        while True:
            stop_reason = self._check_limits(context, scenario, wall_started, script is not None)
            if stop_reason:
                break
            try:
                plan = next(plans)
            except StopIteration:
                stop_reason = "script complete" if script is not None else "no actions available"
                break

            for mutation in plan.before:
                self._mutate(context, m, mutation)
            before = context.last_observation
            outcome: ActionOutcome | None = None
            if plan.action is not None:
                outcome = self._act(context, m, plan.action, delay=plan.delay)
                for _ in range(plan.burst):
                    if context.cancelled:
                        break
                    self._act(context, m, plan.action, delay=0.0, burst=True)
            for mutation in plan.after:
                self._mutate(context, m, mutation)
            for fault in plan.faults:
                expiry = self._inject(context, fault)
                if expiry is not None:
                    fault_expiry.append((expiry, fault))
            fault_expiry = self._clear_expired(context, fault_expiry)

            after: ObservationRecord | None = None
            if plan.action is not None and context.step % limits.observe_every == 0:
                after = self._safe_observe(context)
                m.extractors.update(context)
            if plan.action is not None and outcome is not None:
                failures = m.detectors.run_after_action(context, plan.action, outcome, before,
                                                        after)
                infra_streak = (infra_streak + 1) if outcome.error_kind == "infrastructure" else 0
                if infra_streak >= INFRASTRUCTURE_ABORT_STREAK:
                    context.infrastructure_error(
                        f"{infra_streak} consecutive infrastructure failures — aborting")
                    self._report_failures(context, m, failures)
                    stop_reason = "fatal infrastructure error"
                    break
                stop_reason = self._report_failures(context, m, failures)
                if stop_reason:
                    break
                m.evidence.sample(context)

            consecutive += 1
            if consecutive >= limits.max_consecutive_actions and limits.cooldown_seconds > 0:
                context.record_wait(limits.cooldown_seconds, reason="cooldown")
                context.sleep(limits.cooldown_seconds)
                consecutive = 0
            if plan.delay > 0 and not context.cancelled:
                context.sleep(plan.delay)
            if policy.max_failures is not None and self._app_failure_count(context) >= policy.max_failures:  # noqa: E501
                stop_reason = f"maximum failures reached ({policy.max_failures})"
                break
        for _expiry, fault in fault_expiry:
            self._clear(context, fault)
        context.summary.stop_reason = stop_reason or ("cancelled" if context.cancelled else "")
        context.summary.dropped_history = max(context.trace.count - len(context.trace.recent(10**9)), 0)  # noqa: E501

    # -- steps -------------------------------------------------------------------------------------

    def _act(self, context: StressContext, m: _Machinery, action: StressAction, *, delay: float,
             burst: bool = False) -> ActionOutcome:
        action_type = m.actions.get(action.action_type)
        if context.dry_run and not _dry_run_safe(action):
            outcome = ActionOutcome(passed=True, message="dry run")
        else:
            outcome = action_type.execute(context, action)
        if burst:
            action = action.model_copy(update={"metadata": {**action.metadata, "burst": True}})
        context.record_action(action, outcome, delay=delay)
        return outcome

    def _mutate(self, context: StressContext, m: _Machinery, mutation: Mutation) -> None:
        outcome = m.executor.execute(context, mutation)
        failures = m.detectors.run_after_mutation(context, mutation, outcome)
        self._report_failures(context, m, failures)

    def _inject(self, context: StressContext, fault: Fault) -> float | None:
        injector = context.fault_injector
        if injector is None or context.dry_run:
            context.record_fault(fault)
            return None
        if not injector.supports(fault.fault_type):
            context.note(f"fault {fault.fault_type} unsupported by {injector.name}",
                         fault=fault.fault_type)
            return None
        try:
            injector.apply(fault)
        except Exception as exc:  # noqa: BLE001 - injector trouble is infrastructure
            context.infrastructure_error(f"fault injection failed: {exc}")
            return None
        context.record_fault(fault)
        return context.elapsed + (fault.duration or 0.0)

    def _clear(self, context: StressContext, fault: Fault) -> None:
        injector = context.fault_injector
        if injector is not None and not context.dry_run:
            with contextlib.suppress(Exception):
                injector.clear(fault)
        context.record_fault(fault, cleared=True)

    def _clear_expired(self, context: StressContext,
                       pending: list[tuple[float, Fault]]) -> list[tuple[float, Fault]]:
        remaining = []
        for expiry, fault in pending:
            if context.elapsed >= expiry:
                self._clear(context, fault)
            else:
                remaining.append((expiry, fault))
        return remaining

    def _safe_observe(self, context: StressContext) -> ObservationRecord | None:
        try:
            return context.observe()
        except UTFError as exc:
            context.infrastructure_error(f"observation failed: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            context.infrastructure_error(f"observation failed: {exc}")
        return None

    def _report_failures(self, context: StressContext, m: _Machinery,
                         failures: list[Failure]) -> str:
        policy = context.config.failures
        stop_rank = FailureSeverity(policy.stop_severity).rank
        for failure in failures:
            if failure.category.is_application:
                failure = m.evidence.collect(context, failure)
            context.record_failure(failure)
            if (policy.stop_on_first and failure.category.is_application
                    and failure.severity.rank >= stop_rank):
                return f"stop-on-first failure ({failure.failure_id})"
            if failure.category == FailureCategory.CRASH and policy.restart_after_crash:
                self._restart_after_crash(context)
        return ""

    def _restart_after_crash(self, context: StressContext) -> None:
        device = context.device
        if device is None or not device.capabilities.supports_app_lifecycle or context.dry_run:
            return
        try:
            device.start_application()
        except Exception as exc:  # noqa: BLE001 - relaunch trouble is infrastructure
            context.infrastructure_error(f"relaunch after crash failed: {exc}")
            return
        context.state.pop("crash_reported", None)
        context.state.pop("unchanged_streak", None)
        context.state.pop("frozen_reported", None)
        context.state.pop("error_screen_reported", None)
        context.note("application relaunched after crash", reason="restart_after_crash")

    @staticmethod
    def _app_failure_count(context: StressContext) -> int:
        return len([f for f in context.failures if f.category.is_application])

    def _check_limits(self, context: StressContext, scenario: StressConfig, wall_started: float,
                      scripted: bool) -> str:
        limits = scenario.limits
        if context.cancelled:
            return "cancelled"
        if not scripted:
            if limits.duration_seconds is not None and context.elapsed >= limits.duration_seconds:
                return f"duration reached ({limits.duration})"
            if limits.max_actions is not None and context.summary.actions >= limits.max_actions:
                return f"max actions reached ({limits.max_actions})"
            max_mut = limits.max_mutations
            if max_mut is not None and context.summary.mutations >= max_mut and (
                not scenario.monkey.enabled
            ):
                return f"max mutations reached ({max_mut})"
        if limits.max_runtime_seconds is not None and (
            time.monotonic() - wall_started >= limits.max_runtime_seconds
        ):
            return f"max runtime reached ({limits.max_runtime})"
        return ""

    # -- finish ------------------------------------------------------------------------------------

    def _finish(self, context: StressContext, record: StressRunRecord, trace: Trace,
                run_dir: Path | None) -> None:
        injector = context.fault_injector
        if injector is not None:
            with contextlib.suppress(Exception):
                injector.clear()
        with contextlib.suppress(Exception):
            trace.append(TraceEventType.RUN_FINISHED, elapsed=context.elapsed,
                         timestamp=context.timestamp(),
                         metadata={"status": record.status,
                                   "stop_reason": context.summary.stop_reason})
        trace.close()
        summary = context.summary
        summary.duration = context.elapsed
        summary.reproducible_failures = len([f for f in context.failures
                                             if f.category.is_application])
        record.summary = summary
        record.failures = list(context.failures)
        record.infrastructure_errors = list(dict.fromkeys(
            record.infrastructure_errors + context.infrastructure_errors))
        record.finished_at = self.clock.now().isoformat()
        if run_dir is not None:
            with contextlib.suppress(Exception):
                self.store.save(record, run_dir)
        if context.mutation_backend is not None:
            with contextlib.suppress(Exception):
                context.mutation_backend.close()


@dataclass
class _Machinery:
    actions: StressActionRegistry
    generator: ActionGenerator
    scheduler: MutationScheduler
    executor: MutationExecutor
    extractors: CompositeExtractor
    detectors: DetectorRegistry
    evidence: EvidenceCollector
    probe: DeviceProbe


def _dry_run_safe(action: StressAction) -> bool:
    """UI actions are non-destructive by nature; lifecycle ones are skipped in dry-run."""
    return action.action_type not in ("restart", "reload", "background", "home")


def _random_plans(context: StressContext, scenario: StressConfig, m: _Machinery) -> Iterator[StepPlan]:  # noqa: E501
    step_index = 0
    faults_cfg = scenario.faults
    while True:
        step_index += 1
        planned = m.scheduler.plan(context, step_index)
        action = m.generator.generate(context) if scenario.monkey.enabled else None
        if action is None and not planned:
            if not scenario.monkey.enabled and context.mutation_backend is not None:
                # Chaos-only scenario: idle between mutation opportunities.
                action = StressAction(action_type="wait", parameters={"seconds": 0.5})
            else:
                return
        faults: list[Fault] = []
        if faults_cfg.enabled and context.rng.chance(faults_cfg.probability):
            fault = _random_fault(context, faults_cfg)
            if fault is not None:
                faults.append(fault)
        delay = m.generator.next_delay(context)
        burst = m.generator.burst(context, action) if action is not None else 0
        yield StepPlan(
            action=action,
            before=tuple(p.mutation for p in planned if p.phase == "before"),
            after=tuple(p.mutation for p in planned if p.phase == "after"),
            faults=tuple(faults), delay=delay, burst=burst,
        )


def _random_fault(context: StressContext, cfg: Any) -> Fault | None:
    rng = context.rng
    kinds = [(k, w) for k, w in cfg.types.items() if w > 0]
    if not kinds:
        return None
    kind = rng.weighted_choice([k for k, _w in kinds], [w for _k, w in kinds])
    params: dict[str, Any] = {}
    if kind == "latency":
        params["seconds"] = round(rng.uniform(0.1, cfg.latency_max_seconds), 3)
    elif kind == "http_error":
        params["status"] = rng.choice(list(cfg.http_statuses))
    duration = round(rng.uniform(0.2, cfg.duration_max_seconds), 3)
    return Fault(fault_type=kind, parameters=params, duration=duration)


def _scripted_plans(events: list[TraceEvent]) -> Iterator[StepPlan]:
    """Turn a recorded trace back into step plans (actions carry their mutations/faults)."""
    before: list[Mutation] = []
    faults: list[Fault] = []
    pending_after: list[Mutation] = []
    last_action: StressAction | None = None
    last_delay = 0.0
    burst = 0
    for event in events:
        if event.event_type == TraceEventType.MUTATION and event.mutation is not None:
            if event.mutation_outcome is not None and event.mutation_outcome.blocked and (
                event.mutation_outcome.error_kind != "dry_run" and event.mutation_outcome.reason != "dry run"  # noqa: E501
            ):
                continue  # was refused by safety; replaying it would be refused again
            (pending_after if last_action is not None else before).append(event.mutation)
        elif event.event_type == TraceEventType.FAULT and event.fault is not None:
            faults.append(event.fault)
        elif event.event_type == TraceEventType.ACTION and event.action is not None:
            if event.action.metadata.get("burst"):
                burst += 1  # replayed as rapid repeats of the originating action
                continue
            if last_action is not None:
                yield StepPlan(action=last_action, before=tuple(before), after=tuple(pending_after),
                               faults=tuple(faults), delay=last_delay, burst=burst)
                before, pending_after, faults, burst = [], [], [], 0
            last_action = event.action
            last_delay = event.delay or 0.0
    if last_action is not None or before or pending_after or faults:
        yield StepPlan(action=last_action, before=tuple(before), after=tuple(pending_after),
                       faults=tuple(faults), delay=last_delay, burst=burst)


__all__ = ["StepPlan", "StressComponents", "StressEngine", "StressRunResult"]
