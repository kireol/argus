"""ActionGenerator — deterministic, weighted, capability-aware action selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from argus.stress.actions.base import StressActionRegistry, StressActionType
from argus.stress.capabilities import DeviceProbe
from argus.stress.config import MonkeyConfig
from argus.stress.models import StressAction
from argus.stress.targets import TargetSelector

if TYPE_CHECKING:
    from argus.stress.context import StressContext


@dataclass(frozen=True)
class ActionChoice:
    action_type: StressActionType
    weight: float


class ActionGenerator:
    def __init__(
        self,
        config: MonkeyConfig,
        registry: StressActionRegistry,
        probe: DeviceProbe,
        targets: TargetSelector,
        *,
        safe_only: bool = False,
    ) -> None:
        self._config = config
        self._registry = registry
        self._probe = probe
        self._targets = targets
        self._choices: list[ActionChoice] = []
        self.skipped: dict[str, str] = {}  # action name → reason it is excluded
        for name, weight in config.actions.items():
            if not weight.enabled or weight.weight <= 0:
                self.skipped[name] = "disabled"
                continue
            try:
                action_type = registry.get(name)
            except Exception as exc:  # noqa: BLE001 - unknown name → reported, not fatal
                self.skipped[name] = f"unknown action ({exc})"
                continue
            if safe_only and not action_type.safe:
                self.skipped[name] = "unsafe in safe mode"
                continue
            if not action_type.supported(probe):
                missing = [r for r in action_type.requires if not probe.has(r)]
                self.skipped[name] = f"device lacks {', '.join(missing)}"
                continue
            self._choices.append(ActionChoice(action_type, weight.weight))

    @property
    def available(self) -> list[str]:
        return [c.action_type.name for c in self._choices]

    @property
    def targets(self) -> TargetSelector:
        return self._targets

    def next_delay(self, context: StressContext) -> float:
        delay = self._config.delay
        return context.rng.uniform(delay.min_seconds, delay.max_seconds)

    def generate(self, context: StressContext) -> StressAction | None:
        if not self._choices:
            return None
        rng = context.rng
        choice = rng.weighted_choice(self._choices, [c.weight for c in self._choices])
        params = dict(self._config.actions[choice.action_type.name].params)
        action = choice.action_type.generate(context, self._targets, params)
        if action is None:
            return None
        return action

    def burst(self, context: StressContext, action: StressAction) -> int:
        """How many extra rapid repetitions of ``action`` to fire (0 normally)."""
        if self._config.burst_probability <= 0:
            return 0
        if context.rng.chance(self._config.burst_probability):
            return context.rng.randint(1, self._config.burst_max)
        return 0


__all__ = ["ActionChoice", "ActionGenerator"]
