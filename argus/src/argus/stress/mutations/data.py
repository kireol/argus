"""Data-mutation strategies and valid-value generation.

Each :class:`DataMutationStrategy` declares which field types it supports, so
``very_long_string`` never lands on a boolean and ``negative`` never on an
enum. Strategies are registered by name (``argus.stress.data_mutations`` entry
points add more) and selected through the scenario's ``data_mutations:`` map.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from importlib import metadata
from typing import Any

from argus.stress.mutations.backend import MISSING, FieldSchema
from argus.stress.rng import DeterministicRNG

STRING_TYPES = frozenset({"string", "email"})
NUMBER_TYPES = frozenset({"number", "integer"})
ALL_TYPES = frozenset({"string", "email", "number", "integer", "boolean", "enum", "date", "id",
                       "object", "list"})


class DataMutationStrategy(ABC):
    name: str = "strategy"
    supported_types: frozenset[str] = ALL_TYPES
    #: Produces an *invalid* value (the app should reject it) vs. an edge-but-valid one.
    invalid: bool = True

    def supports(self, field: FieldSchema) -> bool:
        return field.type in self.supported_types

    @abstractmethod
    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any: ...


class NullStrategy(DataMutationStrategy):
    name = "null"

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        return None


class EmptyStrategy(DataMutationStrategy):
    name = "empty"
    supported_types = STRING_TYPES | {"list", "object"}

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        if field.type == "list":
            return []
        if field.type == "object":
            return {}
        return ""


class MissingStrategy(DataMutationStrategy):
    name = "missing"

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        return MISSING


class DuplicateStrategy(DataMutationStrategy):
    """Reuse a value that already exists elsewhere (uniqueness violations)."""

    name = "duplicate"
    supported_types = STRING_TYPES | {"id"} | NUMBER_TYPES

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        return value  # the caller substitutes a sibling entity's value when it can


class VeryLongStringStrategy(DataMutationStrategy):
    name = "very_long_string"
    supported_types = STRING_TYPES

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        length = rng.choice([256, 1024, 4096, 65536])
        return rng.token(min(length, 65536), alphabet="abcdefghijklmnopqrstuvwxyz ")


class SpecialCharactersStrategy(DataMutationStrategy):
    name = "special_characters"
    supported_types = STRING_TYPES
    _SAMPLES = ("<script>alert(1)</script>", "'; DROP TABLE users; --", "%00", "\n\r\t",
                "{{7*7}}", "${jndi:ldap://x}", "\\\\server\\share", "a\\x00b", "🧨\"'`")

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        return rng.choice(self._SAMPLES)


class UnicodeStrategy(DataMutationStrategy):
    name = "unicode"
    supported_types = STRING_TYPES
    invalid = False
    _SAMPLES = ("Ünïcödé", "日本語テキスト", "مرحبا بالعالم", "𝔘𝔫𝔦𝔠𝔬𝔡𝔢", "👨‍👩‍👧‍👦", "Zoë Ångström",
                "‮Reversed", "Ｆｕｌｌｗｉｄｔｈ")

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        return rng.choice(self._SAMPLES)


class ZeroStrategy(DataMutationStrategy):
    name = "zero"
    supported_types = NUMBER_TYPES
    invalid = False

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        return 0


class NegativeStrategy(DataMutationStrategy):
    name = "negative"
    supported_types = NUMBER_TYPES

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        magnitude = abs(float(value)) if isinstance(value, int | float) else rng.randint(1, 1000)
        result = -(magnitude or 1)
        return int(result) if field.type == "integer" else result


class MinimumStrategy(DataMutationStrategy):
    name = "minimum"
    supported_types = NUMBER_TYPES | {"date"}
    invalid = False

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        if field.type == "date":
            return "1970-01-01"
        low = field.min if field.min is not None else 0
        return int(low) if field.type == "integer" else float(low)


class MaximumStrategy(DataMutationStrategy):
    name = "maximum"
    supported_types = NUMBER_TYPES | {"date"}
    invalid = False

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        if field.type == "date":
            return "9999-12-31"
        high = field.max if field.max is not None else (2**31 - 1)
        return int(high) if field.type == "integer" else float(high)


class OutOfRangeStrategy(DataMutationStrategy):
    name = "out_of_range"
    supported_types = NUMBER_TYPES

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        if field.max is not None and rng.chance(0.5):
            result = field.max + rng.randint(1, 1000)
        elif field.min is not None:
            result = field.min - rng.randint(1, 1000)
        else:
            result = rng.choice([2**63, -(2**63), 1e308, float("inf")])
        return int(result) if field.type == "integer" and result not in (float("inf"),) else result


class InvalidEnumStrategy(DataMutationStrategy):
    name = "invalid_enum"
    supported_types = frozenset({"enum"})

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        return rng.choice(["__invalid__", "UNKNOWN", "", "0", "nulL"])


class PastDateStrategy(DataMutationStrategy):
    name = "past_date"
    supported_types = frozenset({"date"})
    invalid = False

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        days = rng.randint(1, 365 * 30)
        return (datetime(2026, 1, 1, tzinfo=UTC) - timedelta(days=days)).date().isoformat()


class FutureDateStrategy(DataMutationStrategy):
    name = "future_date"
    supported_types = frozenset({"date"})
    invalid = False

    def apply(self, value: Any, field: FieldSchema, rng: DeterministicRNG) -> Any:
        days = rng.randint(1, 365 * 30)
        return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=days)).date().isoformat()


BUILTIN_STRATEGIES: tuple[type[DataMutationStrategy], ...] = (
    NullStrategy, EmptyStrategy, MissingStrategy, DuplicateStrategy, VeryLongStringStrategy,
    SpecialCharactersStrategy, UnicodeStrategy, ZeroStrategy, NegativeStrategy, MinimumStrategy,
    MaximumStrategy, OutOfRangeStrategy, InvalidEnumStrategy, PastDateStrategy,
    FutureDateStrategy,
)


class DataMutationRegistry:
    ENTRY_POINT_GROUP = "argus.stress.data_mutations"

    def __init__(self, *, load_builtin: bool = True) -> None:
        self._strategies: dict[str, DataMutationStrategy] = {}
        if load_builtin:
            for cls in BUILTIN_STRATEGIES:
                self.register(cls())
            self._load_entry_points()

    def register(self, strategy: DataMutationStrategy) -> None:
        self._strategies[strategy.name] = strategy

    def get(self, name: str) -> DataMutationStrategy | None:
        return self._strategies.get(name)

    def names(self) -> list[str]:
        return sorted(self._strategies)

    def applicable(self, field: FieldSchema, enabled: set[str] | None = None) -> list[DataMutationStrategy]:  # noqa: E501
        return [
            s for name, s in sorted(self._strategies.items())
            if s.supports(field) and (enabled is None or name in enabled)
        ]

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


def generate_value(field: FieldSchema, rng: DeterministicRNG, *, label: str = "") -> Any:
    """A plausible *valid* value for a field (used by create/duplicate)."""
    match field.type:
        case "boolean":
            return rng.chance(0.5)
        case "integer":
            low = int(field.min) if field.min is not None else 0
            high = int(field.max) if field.max is not None else 1000
            return rng.randint(low, max(high, low))
        case "number":
            lo = float(field.min) if field.min is not None else 0.0
            hi = float(field.max) if field.max is not None else 100.0
            return round(rng.uniform(lo, max(hi, lo)), 2)
        case "enum":
            return rng.choice(list(field.values)) if field.values else "default"
        case "date":
            days = rng.randint(0, 3650)
            return (datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=days)).date().isoformat()
        case "email":
            return f"{label or 'stress'}-{rng.token(6)}@example.test"
        case "id":
            return rng.token(8)
        case "list":
            return []
        case "object":
            return {}
        case _:
            words = ["Alpha", "Beta", "Gamma", "Delta", "Omega", "Nova", "Orion", "Vega"]
            return f"{rng.choice(words)} {rng.token(4)}"


__all__ = [
    "ALL_TYPES", "BUILTIN_STRATEGIES", "DataMutationRegistry", "DataMutationStrategy",
    "generate_value",
]
