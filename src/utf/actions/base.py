"""Action abstraction and registry.

Every YAML step names an action. Actions receive the test context plus the
step's (variable-expanded) parameters and return an :class:`ActionResult`.
New actions register by name — the engine never changes for new capabilities.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib import metadata
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from utf.exceptions import ActionError
from utf.models.results import VerificationResult

if TYPE_CHECKING:
    from utf.engine.context import TestContext


class ActionResult(BaseModel):
    passed: bool = True
    message: str = ""
    failure_category: str | None = None
    verification: VerificationResult | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def ok(cls, message: str = "", **details: Any) -> ActionResult:
        return cls(passed=True, message=message, details=details)

    @classmethod
    def failed(
        cls,
        message: str,
        *,
        category: str | None = None,
        verification: VerificationResult | None = None,
        **details: Any,
    ) -> ActionResult:
        return cls(
            passed=False,
            message=message,
            failure_category=category,
            verification=verification,
            details=details,
        )


class Action(ABC):
    """A named, executable test step."""

    name: str = "action"

    @abstractmethod
    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        ...

    def require_param(self, params: dict[str, Any], key: str) -> Any:
        if key not in params:
            raise ActionError(
                f"Action {self.name!r} requires parameter {key!r}.",
                remediation=f"Add '{key}:' to the step in the test YAML.",
            )
        return params[key]


class ActionRegistry:
    """Maps action names to instances; extensible via ``utf.actions`` entry points."""

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}
        self._entry_points_loaded = False

    def register(self, action: Action) -> None:
        self._actions[action.name] = action

    def get(self, name: str) -> Action:
        self._load_entry_points()
        action = self._actions.get(name)
        if action is None:
            raise ActionError(
                f"Unknown action {name!r}.",
                remediation=f"Available actions: {', '.join(self.names())}.",
            )
        return action

    def names(self) -> list[str]:
        self._load_entry_points()
        return sorted(self._actions)

    def _load_entry_points(self) -> None:
        if self._entry_points_loaded:
            return
        self._entry_points_loaded = True
        from utf.actions.builtin import register as register_builtin

        register_builtin(self)
        for entry_point in metadata.entry_points(group="utf.actions"):
            if entry_point.name == "builtin":
                continue
            entry_point.load()(self)
