"""Pre-flight execution."""

from __future__ import annotations

import time

from argus.events.bus import EventBus
from argus.events.events import PreflightCheckCompleted, PreflightCompleted, PreflightStarted
from argus.models.results import PreflightResult
from argus.preflight.checks import PreflightCheck


def run_preflight(
    checks: list[PreflightCheck], events: EventBus
) -> tuple[list[PreflightResult], bool]:
    """Run all checks; returns (results, all_required_passed)."""
    events.publish(PreflightStarted(total_checks=len(checks)))
    results: list[PreflightResult] = []
    for check in checks:
        start = time.monotonic()
        try:
            result = check.run()
        except Exception as exc:  # noqa: BLE001 - a crashing check is a failing check
            result = PreflightResult(
                name=check.name,
                passed=False,
                required=check.required,
                target=check.target,
                error=f"Check raised an unexpected error: {exc}",
            )
        result.duration = time.monotonic() - start
        results.append(result)
        events.publish(PreflightCheckCompleted(result=result))

    passed = all(r.passed or not r.required for r in results)
    events.publish(PreflightCompleted(results=results, passed=passed))
    return results, passed
