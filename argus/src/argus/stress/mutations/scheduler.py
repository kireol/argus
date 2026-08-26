"""Mutation scheduling and execution.

The :class:`MutationScheduler` decides *whether*, *what* and *when*: random
mutations by probability (timed before/after the action or after a delay),
scenario-scheduled mutations (``after_action_index`` / ``on_context``), and
context-aware entity selection (prefer what is on screen). The
:class:`MutationExecutor` applies one mutation through the safety policy and
classifies the outcome. Timing is explicit in every trace record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from argus.stress.config import BackendMutationsConfig, ScheduledMutation
from argus.stress.models import EntityRef, Mutation, MutationOutcome, MutationTiming
from argus.stress.mutations.backend import BackendSchema, EntitySchema, MutationBackend
from argus.stress.mutations.data import DataMutationRegistry
from argus.stress.mutations.types import MutationRegistry, apply_mutation
from argus.stress.safety import SafetyPolicy
from argus.utilities.duration import parse_duration

if TYPE_CHECKING:
    from argus.stress.context import StressContext

_TIMING = {
    "before_action": MutationTiming.BEFORE_ACTION,
    "after_action": MutationTiming.AFTER_ACTION,
    "delayed": MutationTiming.DELAYED,
    "on_context": MutationTiming.ON_CONTEXT,
    "during_wait": MutationTiming.DURING_WAIT,
}

#: How many actions between refreshes of the per-entity listing cache.
ENTITY_CACHE_STEPS = 10


@dataclass(frozen=True)
class PlannedMutation:
    mutation: Mutation
    #: ``before`` | ``after`` — relative to the action of the current step.
    phase: str


class MutationScheduler:
    def __init__(
        self,
        config: BackendMutationsConfig,
        backend: MutationBackend | None,
        registry: MutationRegistry,
        data: DataMutationRegistry,
        *,
        enabled_strategies: set[str],
    ) -> None:
        self._config = config
        self._backend = backend
        self._registry = registry
        self._data = data
        self._strategies = enabled_strategies
        self._schema: BackendSchema | None = None
        self._schema_error: str | None = None
        self._scheduled_fired: dict[int, int] = {}
        self._entity_cache: dict[str, tuple[int, list[dict[str, Any]]]] = {}
        self.planned = 0

    @staticmethod
    def _text_on_screen(context: StressContext, phrase: str) -> bool:
        record = context.last_observation
        if record is None:
            return False
        result = context.ocr_for(record)
        return result is not None and phrase.lower() in result.text.lower()

    # -- schema -------------------------------------------------------------------------------

    def schema(self, context: StressContext) -> BackendSchema | None:
        if self._backend is None:
            return None
        if self._schema is None and self._schema_error is None:
            try:
                self._schema = self._backend.schema()
            except Exception as exc:  # noqa: BLE001 - discovery failure is infrastructure
                self._schema_error = str(exc)
                context.infrastructure_error(f"backend schema discovery failed: {exc}")
        return self._schema

    def entities(self, context: StressContext, entity_type: str) -> list[dict[str, Any]]:
        if self._backend is None:
            return []
        cached = self._entity_cache.get(entity_type)
        if cached is not None and context.step - cached[0] < ENTITY_CACHE_STEPS:
            return cached[1]
        try:
            items = self._backend.list_entities(entity_type)
        except Exception as exc:  # noqa: BLE001
            context.infrastructure_error(f"listing {entity_type} failed: {exc}")
            items = cached[1] if cached else []
        self._entity_cache[entity_type] = (context.step, items)
        context.state[f"entities:{entity_type}"] = items
        return items

    def invalidate(self, entity_type: str | None = None) -> None:
        if entity_type is None:
            self._entity_cache.clear()
        else:
            self._entity_cache.pop(entity_type, None)

    # -- planning --------------------------------------------------------------------------------

    def plan(self, context: StressContext, step_index: int) -> list[PlannedMutation]:
        """Mutations for this step: scenario-scheduled ones first, then random ones."""
        if self._backend is None or not self._config.enabled:
            return []
        if self._config.max_mutations is not None and (
            context.summary.mutations >= self._config.max_mutations
        ):
            return []
        schema = self.schema(context)
        if schema is None:
            return []
        planned: list[PlannedMutation] = []
        for index, spec in enumerate(self._config.scheduled):
            fired = self._scheduled_fired.get(index, 0)
            limit = spec.max_times if spec.max_times is not None else (None if spec.repeat else 1)
            if limit is not None and fired >= limit:
                continue
            if spec.when_text and not self._text_on_screen(context, spec.when_text):
                continue
            mutation = self._from_spec(context, schema, spec, step_index)
            if mutation is not None:
                self._scheduled_fired[index] = fired + 1
                planned.append(PlannedMutation(mutation, "before" if
                                               spec.timing == "before_action" else "after"))
        if context.rng.chance(self._config.probability):
            mutation = self.random_mutation(context, schema)
            if mutation is not None:
                phase = "before" if mutation.timing == MutationTiming.BEFORE_ACTION else "after"
                planned.append(PlannedMutation(mutation, phase))
        self.planned += len(planned)
        return planned

    def _from_spec(self, context: StressContext, schema: BackendSchema, spec: ScheduledMutation,
                   step_index: int) -> Mutation | None:
        if spec.after_action_index is not None and step_index < spec.after_action_index:
            return None
        entity = schema.entities.get(spec.entity)
        if entity is None:
            context.infrastructure_error(f"scheduled mutation names unknown entity {spec.entity!r}")
            return None
        target: EntityRef | None = None
        if spec.timing == "on_context":
            live = {str(i.get(entity.id_field)) for i in self.entities(context, spec.entity)}
            refs = [r for r in context.entity_context
                    if r.entity_type == spec.entity and r.entity_id in live]
            if not refs:
                return None
            target = refs[0]
        elif spec.entity_id is not None:
            target = EntityRef(entity_type=spec.entity, entity_id=str(spec.entity_id),
                               source="configured")
        else:
            target = self._pick_entity(context, entity, contextual=True)
        mutation_type = self._registry.get(spec.mutation)
        if mutation_type.needs_entity and target is None:
            return None
        existing = self._existing(context, entity, target)
        mutation = mutation_type.build(
            context, entity, target, existing, data=self._data,
            enabled_strategies=set(spec.strategies) or set(), timing=_TIMING.get(spec.timing, MutationTiming.SCHEDULED),  # noqa: E501
            delay=float(parse_duration(spec.delay)) if spec.delay else 0.0, extra=dict(spec.data),
        )
        if mutation is not None:
            mutation = mutation.model_copy(update={"metadata": {**mutation.metadata,
                                                                "scheduled": True}})
        return mutation

    def random_mutation(self, context: StressContext, schema: BackendSchema) -> Mutation | None:
        rng = context.rng
        operations = [(name, op) for name, op in self._config.operations.items()
                      if op.enabled and op.weight > 0]
        if not operations or not schema.entities:
            return None
        name, _op = rng.weighted_choice(operations, [op.weight for _n, op in operations])
        mutation_type = self._registry.get(name)
        candidates = [e for e in schema.entities.values() if mutation_type.applicable(e)]
        if not candidates:
            return None
        # Prefer entity types that are on screen right now.
        on_screen = {r.entity_type for r in context.entity_context}
        weights = [3.0 if e.name in on_screen else 1.0 for e in candidates]
        entity = rng.weighted_choice(candidates, weights)
        target = self._pick_entity(context, entity, contextual=True) if mutation_type.needs_entity else None  # noqa: E501
        if mutation_type.needs_entity and target is None:
            return None
        existing = self._existing(context, entity, target)
        timing_names = [t for t, w in self._config.timing.items() if w > 0]
        timing_name = rng.weighted_choice(timing_names, [self._config.timing[t] for t in timing_names]) if timing_names else "after_action"  # noqa: E501
        timing = _TIMING.get(timing_name, MutationTiming.AFTER_ACTION)
        delay = round(rng.uniform(0.05, self._config.delayed_max_seconds), 3) if (
            timing == MutationTiming.DELAYED
        ) else 0.0
        return mutation_type.build(context, entity, target, existing, data=self._data,
                                   enabled_strategies=self._strategies, timing=timing, delay=delay)

    def _pick_entity(self, context: StressContext, entity: EntitySchema,
                     *, contextual: bool) -> EntityRef | None:
        rng = context.rng
        items = self.entities(context, entity.name)
        if contextual:
            live_ids = {str(i.get(entity.id_field)) for i in items}
            # Context refs can outlive the entity (we may have just deleted it).
            refs = [r for r in context.entity_context
                    if r.entity_type == entity.name and r.entity_id in live_ids]
            if refs and rng.chance(self._config.contextual_probability):
                return rng.choice(refs)
        if not items:
            return None
        item = rng.choice(items)
        entity_id = item.get(entity.id_field)
        if entity_id is None:
            return None
        label = item.get(entity.display_field) if entity.display_field else None
        return EntityRef(entity_type=entity.name, entity_id=str(entity_id),
                         label=str(label) if label is not None else None, source="random",
                         data=dict(item))

    def _existing(self, context: StressContext, entity: EntitySchema,
                  target: EntityRef | None) -> dict[str, Any] | None:
        if target is None:
            return None
        if target.data and entity.id_field in target.data:
            return dict(target.data)
        for item in self.entities(context, entity.name):
            if str(item.get(entity.id_field)) == target.entity_id:
                return item
        if self._backend is not None:
            try:
                return self._backend.get_entity(entity.name, target.entity_id)
            except Exception:  # noqa: BLE001
                return None
        return None


class MutationExecutor:
    def __init__(self, backend: MutationBackend | None, registry: MutationRegistry,
                 safety: SafetyPolicy, scheduler: MutationScheduler) -> None:
        self._backend = backend
        self._registry = registry
        self._safety = safety
        self._scheduler = scheduler

    def execute(self, context: StressContext, mutation: Mutation) -> MutationOutcome:
        started = context.clock.monotonic()
        schema = self._scheduler.schema(context)
        verdict = self._safety.check(mutation, schema)
        if not verdict.allowed:
            outcome = MutationOutcome(applied=False, blocked=True, reason=verdict.reason,
                                      error_kind=None if verdict.code == "dry_run" else verdict.code,  # noqa: E501
                                      duration=context.clock.monotonic() - started)
            context.record_mutation(mutation, outcome)
            return outcome
        if self._backend is None:
            outcome = MutationOutcome(applied=False, reason="no mutation backend",
                                      error_kind="infrastructure")
            context.record_mutation(mutation, outcome)
            return outcome
        if mutation.delay > 0:
            context.record_wait(mutation.delay, reason=f"delay before {mutation.describe()}")
            context.sleep(mutation.delay)
        outcome = apply_mutation(self._registry, self._backend, mutation)
        outcome = outcome.model_copy(update={"duration": context.clock.monotonic() - started})
        self._scheduler.invalidate(mutation.entity_type)
        context.record_mutation(mutation, outcome)
        if outcome.applied:
            # What the screen said when the mutation landed: detectors compare against it
            # so text that was already there is never blamed on the mutation.
            baseline = ""
            last = context.last_observation
            if last is not None and context.ocr is not None:
                result = context.ocr_for(last)
                baseline = result.text.lower() if result is not None else ""
            context.state.setdefault("applied_mutations", []).append(
                (context.step, context.elapsed, mutation, outcome, baseline)
            )
            applied = context.state["applied_mutations"]
            if len(applied) > 200:
                del applied[:-200]
        return outcome


__all__ = ["ENTITY_CACHE_STEPS", "MutationExecutor", "MutationScheduler", "PlannedMutation"]
