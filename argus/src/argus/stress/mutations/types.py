"""Mutation types: create / update / delete / duplicate / disable / archive.

A :class:`MutationType` builds an immutable :class:`Mutation` for an entity
(optionally the one currently on screen) and applies it to a
:class:`MutationBackend`. New types register by name — via
``registry.register`` or an ``argus.stress.mutations`` entry point.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib import metadata
from typing import TYPE_CHECKING, Any

from argus.exceptions import BackendError, UTFError
from argus.stress.models import EntityRef, Mutation, MutationOutcome, MutationTiming
from argus.stress.mutations.backend import MISSING, EntitySchema, MutationBackend
from argus.stress.mutations.data import DataMutationRegistry, generate_value

if TYPE_CHECKING:
    from argus.stress.context import StressContext


class MutationType(ABC):
    name: str = "mutation"
    destructive: bool = False
    #: Needs an existing entity (update/delete/...) vs. creates one.
    needs_entity: bool = True

    def applicable(self, entity: EntitySchema) -> bool:
        return entity.supports(self.name)

    @abstractmethod
    def build(self, context: StressContext, entity: EntitySchema, target: EntityRef | None,
              existing: dict[str, Any] | None, *, data: DataMutationRegistry,
              enabled_strategies: set[str], timing: MutationTiming, delay: float,
              extra: dict[str, Any] | None = None) -> Mutation | None: ...

    @abstractmethod
    def apply(self, backend: MutationBackend, mutation: Mutation) -> str | None:
        """Perform the mutation; returns the affected/created entity id."""


def _mutate_fields(
    context: StressContext, entity: EntitySchema, base: dict[str, Any],
    *, data: DataMutationRegistry, enabled: set[str], siblings: list[dict[str, Any]],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Apply 0..N data-mutation strategies to a copy of ``base``."""
    cfg = context.config.data_mutations
    rng = context.rng
    payload = dict(base)
    applied: list[str] = []
    if not enabled or not rng.chance(cfg.probability):
        return payload, ()
    candidates = [f for f in entity.fields.values() if f.name != entity.id_field]
    if not candidates:
        return payload, ()
    count = rng.randint(1, min(cfg.max_per_mutation, len(candidates)))
    for field in rng.sample(candidates, count):
        options = data.applicable(field, enabled)
        if not options:
            continue
        strategy = rng.choice(options)
        value = strategy.apply(payload.get(field.name), field, rng)
        if strategy.name == "duplicate":
            donors = [s.get(field.name) for s in siblings if field.name in s]
            if not donors:
                continue
            value = rng.choice(donors)
        # Mutations are JSON-serialised into the trace: encode the sentinel as text.
        payload[field.name] = "<MISSING>" if value is MISSING else value
        applied.append(f"{strategy.name}:{field.name}")
    return payload, tuple(applied)


class CreateMutation(MutationType):
    name = "create"
    needs_entity = False

    def build(self, context, entity, target, existing, *, data, enabled_strategies, timing, delay,
              extra=None):
        rng = context.rng
        payload = {
            f.name: generate_value(f, rng, label=entity.name)
            for f in entity.fields.values() if f.name != entity.id_field
        }
        payload.update(extra or {})
        siblings = context.state.get(f"entities:{entity.name}", [])
        payload, strategies = _mutate_fields(context, entity, payload, data=data,
                                             enabled=enabled_strategies, siblings=siblings)
        return Mutation(mutation_type=self.name, entity_type=entity.name, parameters=payload,
                        strategies=strategies, timing=timing, delay=delay,
                        destructive=self.destructive)

    def apply(self, backend, mutation):
        return backend.create(mutation.entity_type, _payload(mutation.parameters))


class UpdateMutation(MutationType):
    name = "update"

    def build(self, context, entity, target, existing, *, data, enabled_strategies, timing, delay,
              extra=None):
        if existing is None or target is None:
            return None
        rng = context.rng
        fields = [f for f in entity.fields.values() if f.name != entity.id_field]
        changes: dict[str, Any] = {}
        if fields:
            for field in rng.sample(fields, rng.randint(1, min(2, len(fields)))):
                changes[field.name] = generate_value(field, rng, label=entity.name)
        changes.update(extra or {})
        siblings = context.state.get(f"entities:{entity.name}", [])
        changes, strategies = _mutate_fields(context, entity, changes, data=data,
                                             enabled=enabled_strategies, siblings=siblings)
        return Mutation(mutation_type=self.name, entity_type=entity.name,
                        entity_id=target.entity_id, parameters=changes, strategies=strategies,
                        timing=timing, delay=delay, contextual=target.source != "random",
                        destructive=self.destructive)

    def apply(self, backend, mutation):
        assert mutation.entity_id is not None
        backend.update(mutation.entity_type, mutation.entity_id, _payload(mutation.parameters))
        return mutation.entity_id


class DeleteMutation(MutationType):
    name = "delete"
    destructive = True

    def build(self, context, entity, target, existing, *, data, enabled_strategies, timing, delay,
              extra=None):
        if target is None:
            return None
        label = target.label
        if label is None and existing is not None and entity.display_field:
            label = existing.get(entity.display_field)
        return Mutation(mutation_type=self.name, entity_type=entity.name,
                        entity_id=target.entity_id, timing=timing, delay=delay,
                        contextual=target.source != "random", destructive=True,
                        metadata={"label": str(label)} if label else {})

    def apply(self, backend, mutation):
        assert mutation.entity_id is not None
        backend.delete(mutation.entity_type, mutation.entity_id)
        return mutation.entity_id


class DuplicateMutation(MutationType):
    """Create a copy of an existing entity (uniqueness / list-rendering bugs)."""

    name = "duplicate"

    def build(self, context, entity, target, existing, *, data, enabled_strategies, timing, delay,
              extra=None):
        if existing is None or target is None:
            return None
        payload = {k: v for k, v in existing.items() if k != entity.id_field}
        payload.update(extra or {})
        return Mutation(mutation_type=self.name, entity_type=entity.name,
                        entity_id=target.entity_id, parameters=payload, timing=timing,
                        delay=delay, contextual=target.source != "random",
                        destructive=self.destructive)

    def apply(self, backend, mutation):
        return backend.create(mutation.entity_type, _payload(mutation.parameters))


class _StatusMutation(MutationType):
    destructive = True

    def build(self, context, entity, target, existing, *, data, enabled_strategies, timing, delay,
              extra=None):
        if target is None:
            return None
        changes = entity.status_update(self.name)
        changes.update(extra or {})
        if not changes:
            return None
        label = target.label
        if label is None and existing is not None and entity.display_field:
            label = existing.get(entity.display_field)
        return Mutation(mutation_type=self.name, entity_type=entity.name,
                        entity_id=target.entity_id, parameters=changes, timing=timing,
                        delay=delay, contextual=target.source != "random", destructive=True,
                        metadata={"label": str(label)} if label else {})

    def apply(self, backend, mutation):
        assert mutation.entity_id is not None
        backend.update(mutation.entity_type, mutation.entity_id, _payload(mutation.parameters))
        return mutation.entity_id


class DisableMutation(_StatusMutation):
    name = "disable"


class ArchiveMutation(_StatusMutation):
    name = "archive"


BUILTIN_MUTATIONS: tuple[type[MutationType], ...] = (
    CreateMutation, UpdateMutation, DeleteMutation, DuplicateMutation, DisableMutation,
    ArchiveMutation,
)


class MutationRegistry:
    ENTRY_POINT_GROUP = "argus.stress.mutations"

    def __init__(self, *, load_builtin: bool = True) -> None:
        self._types: dict[str, MutationType] = {}
        if load_builtin:
            for cls in BUILTIN_MUTATIONS:
                self.register(cls())
            self._load_entry_points()

    def register(self, mutation_type: MutationType) -> None:
        self._types[mutation_type.name] = mutation_type

    def get(self, name: str) -> MutationType:
        mutation_type = self._types.get(name)
        if mutation_type is None:
            raise UTFError(f"Unknown mutation type {name!r}.",
                           remediation=f"Available: {', '.join(self.names())}.")
        return mutation_type

    def names(self) -> list[str]:
        return sorted(self._types)

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


def apply_mutation(registry: MutationRegistry, backend: MutationBackend,
                   mutation: Mutation) -> MutationOutcome:
    """Apply a mutation and classify the outcome (never raises)."""
    mutation_type = registry.get(mutation.mutation_type)
    try:
        entity_id = mutation_type.apply(backend, mutation)
    except BackendError as exc:
        return MutationOutcome(applied=False, reason=str(exc.message), error_kind="backend")
    except UTFError as exc:
        return MutationOutcome(applied=False, reason=str(exc.message), error_kind="infrastructure")
    except Exception as exc:  # noqa: BLE001 - backend adapters must not crash the run
        return MutationOutcome(applied=False, reason=f"{type(exc).__name__}: {exc}",
                               error_kind="infrastructure")
    return MutationOutcome(applied=True, entity_id=entity_id)


def _payload(parameters: dict[str, Any]) -> dict[str, Any]:
    return {k: (MISSING if v == "<MISSING>" else v) for k, v in parameters.items()}


__all__ = [
    "BUILTIN_MUTATIONS", "MutationRegistry", "MutationType", "apply_mutation",
]
