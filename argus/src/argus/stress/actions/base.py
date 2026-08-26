"""Stress action types and their registry.

A :class:`StressActionType` is one focused component: it says which device
capabilities it needs, *generates* an immutable :class:`StressAction` from the
context (using the context's RNG and target selector) and *executes* one.
Adding an action = subclass + ``registry.register(...)`` (or an
``argus.stress.actions`` entry point). The engine never grows a conditional.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib import metadata
from typing import TYPE_CHECKING, Any

from argus.exceptions import (
    DeviceCapabilityError,
    DeviceConnectionError,
    ScreenshotError,
    TimeoutExceededError,
    UTFError,
)
from argus.stress.models import ActionOutcome, StressAction

if TYPE_CHECKING:
    from argus.stress.capabilities import DeviceProbe
    from argus.stress.context import StressContext
    from argus.stress.targets import TargetSelector


class StressActionType(ABC):
    """One kind of randomized UI action."""

    name: str = "action"
    #: ``DeviceCapabilities`` flags (without ``supports_``) or method names the
    #: device must offer; unsupported actions are skipped, never crash.
    requires: tuple[str, ...] = ()
    #: Actions that only make sense with a target on screen.
    targeted: bool = False
    #: Lifecycle actions are "disruptive": the engine expects the screen to change.
    expects_change: bool = True
    #: Safe-mode scenarios may exclude actions flagged unsafe (e.g. home/restart).
    safe: bool = True

    def supported(self, probe: DeviceProbe) -> bool:
        return all(probe.has(req) for req in self.requires)

    @abstractmethod
    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        """Describe one action (or ``None`` when nothing sensible is available)."""

    @abstractmethod
    def perform(self, context: StressContext, action: StressAction) -> None:
        """Execute the described action against ``context.device``."""

    def execute(self, context: StressContext, action: StressAction) -> ActionOutcome:
        """Run ``perform`` and classify what happened (never raises)."""
        started = context.clock.monotonic()
        try:
            self.perform(context, action)
        except DeviceCapabilityError as exc:
            return ActionOutcome(passed=False, message=str(exc.message), error_kind="unsupported",
                                 duration=context.clock.monotonic() - started)
        except (DeviceConnectionError, ScreenshotError, TimeoutExceededError) as exc:
            return ActionOutcome(passed=False, message=str(exc.message),
                                 error_kind="infrastructure",
                                 duration=context.clock.monotonic() - started)
        except UTFError as exc:
            return ActionOutcome(passed=False, message=str(exc.message), error_kind="application",
                                 duration=context.clock.monotonic() - started,
                                 details={"exception": type(exc).__name__})
        except Exception as exc:  # noqa: BLE001 - harness must survive adapter crashes
            return ActionOutcome(passed=False, message=f"{type(exc).__name__}: {exc}",
                                 error_kind="infrastructure",
                                 duration=context.clock.monotonic() - started)
        return ActionOutcome(passed=True, duration=context.clock.monotonic() - started)


class StressActionRegistry:
    """Maps action-type names to instances; extensible via ``argus.stress.actions``."""

    ENTRY_POINT_GROUP = "argus.stress.actions"

    def __init__(self, *, load_builtin: bool = True) -> None:
        self._types: dict[str, StressActionType] = {}
        self._loaded = False
        self._load_builtin = load_builtin

    def register(self, action_type: StressActionType) -> None:
        self._types[action_type.name] = action_type

    def get(self, name: str) -> StressActionType:
        self._load()
        action_type = self._types.get(name)
        if action_type is None:
            raise UTFError(
                f"Unknown stress action {name!r}.",
                remediation=f"Available: {', '.join(self.names())}.",
            )
        return action_type

    def names(self) -> list[str]:
        self._load()
        return sorted(self._types)

    def all(self) -> list[StressActionType]:
        self._load()
        return [self._types[n] for n in sorted(self._types)]

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._load_builtin:
            from argus.stress.actions.builtin import register as register_builtin

            register_builtin(self)
        try:
            entry_points = list(metadata.entry_points(group=self.ENTRY_POINT_GROUP))
        except Exception:  # noqa: BLE001 - metadata can be unavailable in frozen apps
            entry_points = []
        for entry_point in entry_points:
            try:
                entry_point.load()(self)
            except Exception:  # noqa: BLE001 - a broken plugin must not break the engine
                continue


__all__ = ["StressActionRegistry", "StressActionType"]
