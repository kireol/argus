"""wait_until — condition-based synchronization (never bare sleeps).

Evaluates the condition immediately, then polls until success or timeout.
One observation is captured per poll and shared across every visual
sub-condition, so composite conditions cost one screenshot per cycle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from argus.conditions.base import Condition
from argus.engine.context import TestContext
from argus.models.results import VerificationResult


@dataclass
class WaitOutcome:
    passed: bool
    attempts: int
    elapsed: float
    last_result: VerificationResult | None

    @property
    def message(self) -> str:
        detail = self.last_result.message if self.last_result else "no evaluation"
        if self.passed:
            return f"Condition met after {self.elapsed:.2f}s ({self.attempts} checks): {detail}"
        return (
            f"Condition not met within {self.elapsed:.2f}s "
            f"({self.attempts} checks): {detail}"
        )


def wait_until(
    context: TestContext,
    condition: Condition,
    *,
    timeout: float,
    poll_interval: float,
) -> WaitOutcome:
    """Poll ``condition`` until it passes or ``timeout`` elapses."""
    start = time.monotonic()
    attempts = 0
    last_result: VerificationResult | None = None

    while True:
        observation = context.observe() if condition.needs_observation else None
        attempts += 1
        last_result = condition.evaluate(context, observation)
        elapsed = time.monotonic() - start
        if last_result.passed:
            return WaitOutcome(True, attempts, elapsed, last_result)
        remaining = timeout - elapsed
        if remaining <= 0:
            return WaitOutcome(False, attempts, elapsed, last_result)
        time.sleep(min(poll_interval, remaining))
