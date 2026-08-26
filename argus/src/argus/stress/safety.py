"""Safety boundaries for backend mutations.

The engine asks :meth:`SafetyPolicy.check` before *every* mutation. The
policy refuses rather than guesses: unknown environments, unknown schemas
and un-allow-listed entities all block destructive work. Dry-run blocks
everything and reports what would have happened.
"""

from __future__ import annotations

from dataclasses import dataclass

from argus.stress.config import SafetyConfig
from argus.stress.models import Mutation
from argus.stress.mutations.backend import BackendSchema

DESTRUCTIVE_OPERATIONS = frozenset({"delete", "archive", "disable"})


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""
    #: ``dry_run`` | ``unsafe`` | ``unsupported`` | ``unknown_environment`` | ``ok``
    code: str = "ok"


class SafetyPolicy:
    def __init__(self, config: SafetyConfig, *, dry_run: bool = False) -> None:
        self._config = config
        self.dry_run = dry_run or config.dry_run

    @property
    def config(self) -> SafetyConfig:
        return self._config

    def environment_known(self, schema: BackendSchema | None) -> bool:
        env = self._effective_environment(schema)
        return env is not None

    def _effective_environment(self, schema: BackendSchema | None) -> str | None:
        backend_env = schema.environment if schema is not None else None
        configured = self._config.environment or None
        if backend_env and configured and backend_env != configured:
            return None  # contradictory claims → unknown
        return backend_env or configured

    def check(self, mutation: Mutation, schema: BackendSchema | None) -> Verdict:
        cfg = self._config
        entity = mutation.entity_type
        operation = mutation.mutation_type
        destructive = mutation.destructive or operation in DESTRUCTIVE_OPERATIONS

        if entity in cfg.denied_entities:
            return Verdict(False, f"entity {entity!r} is deny-listed", "unsafe")
        if cfg.allowed_entities and entity not in cfg.allowed_entities:
            return Verdict(False, f"entity {entity!r} is not in safety.allowed_entities", "unsafe")
        if operation in cfg.denied_operations:
            return Verdict(False, f"operation {operation!r} is deny-listed", "unsafe")
        if cfg.allowed_operations and operation not in cfg.allowed_operations:
            return Verdict(False, f"operation {operation!r} is not in safety.allowed_operations",
                           "unsafe")

        if cfg.require_capabilities:
            if schema is None:
                return Verdict(False, "backend capabilities unknown (safety.require_capabilities)",
                               "unsupported")
            entity_schema = schema.entities.get(entity)
            if entity_schema is None:
                return Verdict(False, f"backend does not declare entity {entity!r}",
                               "unsupported")
            if not entity_schema.supports(operation):
                return Verdict(False, f"backend does not support {operation!r} on {entity!r}",
                               "unsupported")

        if destructive:
            if not cfg.allow_destructive_mutations:
                return Verdict(False, "destructive mutations disabled", "unsafe")
            env = self._effective_environment(schema)
            if env is None:
                return Verdict(False, "environment unknown — refusing destructive mutation "
                               "(set safety.environment or have the backend declare it)",
                               "unknown_environment")
            if env not in cfg.allowed_environments:
                return Verdict(False, f"environment {env!r} is not in "
                               f"safety.allowed_environments", "unsafe")

        if self.dry_run:
            return Verdict(False, "dry run", "dry_run")
        return Verdict(True)


__all__ = ["DESTRUCTIVE_OPERATIONS", "SafetyPolicy", "Verdict"]
