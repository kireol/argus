"""Condition abstraction and factory.

A condition answers "is this true right now?" against a
:class:`TestContext` and (optionally) a shared :class:`Observation`.
Conditions compose with ``all`` / ``any`` / ``not``. A single observation is
shared across every visual condition in one evaluation pass, so composite
conditions never trigger multiple captures.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from utf.exceptions import ConditionError
from utf.models.common import Region
from utf.models.observation import Observation
from utf.models.results import VerificationResult
from utf.models.test_definition import ConditionSpec

if TYPE_CHECKING:
    from utf.engine.context import TestContext


class Condition(ABC):
    """A declarative predicate."""

    name: str = "condition"

    #: True when the condition inspects a screenshot; the evaluator captures
    #: one observation per pass and shares it among all such conditions.
    needs_observation: bool = False

    @abstractmethod
    def evaluate(
        self, context: "TestContext", observation: Observation | None
    ) -> VerificationResult:
        ...


ConditionBuilder = Callable[[dict[str, Any], "TestContext"], Condition]


class _AllCondition(Condition):
    name = "all"

    def __init__(self, children: list[Condition]) -> None:
        self.children = children
        self.needs_observation = any(c.needs_observation for c in children)

    def evaluate(
        self, context: "TestContext", observation: Observation | None
    ) -> VerificationResult:
        results = [c.evaluate(context, observation) for c in self.children]
        passed = all(r.passed for r in results)
        failed = [r for r in results if not r.passed]
        message = (
            "All conditions met"
            if passed
            else "Failed: " + "; ".join(r.message for r in failed)
        )
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            message=message,
            details={"children": [r.model_dump() for r in results]},
        )


class _AnyCondition(Condition):
    name = "any"

    def __init__(self, children: list[Condition]) -> None:
        self.children = children
        self.needs_observation = any(c.needs_observation for c in children)

    def evaluate(
        self, context: "TestContext", observation: Observation | None
    ) -> VerificationResult:
        results = [c.evaluate(context, observation) for c in self.children]
        passed = any(r.passed for r in results)
        message = (
            "At least one condition met"
            if passed
            else "None met: " + "; ".join(r.message for r in results)
        )
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            message=message,
            details={"children": [r.model_dump() for r in results]},
        )


class _NotCondition(Condition):
    name = "not"

    def __init__(self, child: Condition) -> None:
        self.child = child
        self.needs_observation = child.needs_observation

    def evaluate(
        self, context: "TestContext", observation: Observation | None
    ) -> VerificationResult:
        result = self.child.evaluate(context, observation)
        return VerificationResult(
            passed=not result.passed,
            verifier=self.name,
            message=f"NOT({result.message})",
            details={"child": result.model_dump()},
        )


class ConditionFactory:
    """Builds condition instances from :class:`ConditionSpec` trees.

    New condition types register by name — plugins never modify the engine.
    """

    def __init__(self) -> None:
        self._builders: dict[str, ConditionBuilder] = {}

    def register(self, name: str, builder: ConditionBuilder) -> None:
        self._builders[name] = builder

    def types(self) -> list[str]:
        return sorted(self._builders)

    def build(self, spec: ConditionSpec, context: "TestContext") -> Condition:
        if spec.all is not None:
            return _AllCondition([self.build(child, context) for child in spec.all])
        if spec.any is not None:
            return _AnyCondition([self.build(child, context) for child in spec.any])
        if spec.not_ is not None:
            return _NotCondition(self.build(spec.not_, context))

        assert spec.type is not None  # guaranteed by ConditionSpec validation
        builder = self._builders.get(spec.type)
        if builder is None:
            raise ConditionError(
                f"Unknown condition type {spec.type!r}.",
                remediation=f"Available types: {', '.join(self.types())}.",
            )
        params = context.expand(spec.params)
        return builder(params, context)


def resolve_region(
    value: Any, named_regions: dict[str, Region]
) -> Region | None:
    """Resolve a region parameter: inline mapping or configured region name."""
    if value is None:
        return None
    if isinstance(value, Region):
        return value
    if isinstance(value, dict):
        return Region.model_validate(value)
    if isinstance(value, str):
        region = named_regions.get(value)
        if region is None:
            raise ConditionError(
                f"Unknown named region {value!r}.",
                remediation=(
                    f"Define it under 'regions:' in configuration. "
                    f"Known regions: {', '.join(sorted(named_regions)) or '<none>'}."
                ),
            )
        return region
    raise ConditionError(f"Invalid region value: {value!r}")
