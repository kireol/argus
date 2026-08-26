"""Quality policy engine — provider-neutral quality gates.

Input: classified CI test results + policy configuration. Output: a
:class:`PolicyResult` with one violation per triggered rule. The engine knows
nothing about CI providers or exit codes; the runner maps the result.
"""

from __future__ import annotations

from collections.abc import Mapping

from argus.ci.categories import FailureCategory
from argus.ci.result import CITestResult, PolicyResult, PolicyViolation, TestOutcome
from argus.config.models import CIPolicyConfig, CIPolicyRule


def evaluate_policy(
    policy: CIPolicyConfig,
    tests: list[CITestResult],
    *,
    required_selection: Mapping[str, set[str]] | None = None,
    run_failed: str | None = None,
) -> PolicyResult:
    """Evaluate every rule.

    ``required_selection`` maps each required suite name to the ids of tests it
    selects (already resolved by the runner). ``run_failed`` carries a run-level
    failure description (preflight/setup/cancel) so required suites are never
    reported as satisfied when nothing executed.
    """
    violations: list[PolicyViolation] = []

    def add(rule: str, cfg: CIPolicyRule, message: str, ids: list[str]) -> None:
        if cfg.action == "ignore":
            return
        violations.append(PolicyViolation(rule=rule, action=cfg.action, message=message, tests=ids))

    regressions = [
        t
        for t in tests
        if t.outcome in (TestOutcome.FAILED, TestOutcome.ERROR)
        and t.failure_category == FailureCategory.VISUAL_REGRESSION
    ]
    other_failures = [
        t
        for t in tests
        if t.outcome in (TestOutcome.FAILED, TestOutcome.ERROR) and t not in regressions
    ]
    if other_failures:
        add(
            "failures",
            policy.failures,
            f"{len(other_failures)} test(s) failed",
            _labels(other_failures),
        )
    if regressions:
        add(
            "visual_regression",
            policy.visual_regression,
            f"{len(regressions)} visual regression(s)",
            _labels(regressions),
        )
    known = [t for t in tests if t.outcome == TestOutcome.KNOWN_FAILURE]
    if known:
        add(
            "known_failure",
            policy.known_failure,
            f"{len(known)} known failure(s) still failing",
            _labels(known),
        )
    flaky = [t for t in tests if t.flaky]
    if flaky:
        add("flaky", policy.flaky, f"{len(flaky)} flaky test(s)", _labels(flaky))

    by_id: dict[str, list[CITestResult]] = {}
    for test in tests:
        by_id.setdefault(test.test_id, []).append(test)
    fail_rule = CIPolicyRule(action="fail")
    for suite_name in policy.required:
        selected = (required_selection or {}).get(suite_name)
        if selected is None:
            continue  # unknown suite is rejected at configuration time
        present = [t for tid in sorted(selected) for t in by_id.get(tid, [])]
        if run_failed and not any(t.outcome != TestOutcome.NOT_RUN for t in present):
            add(
                "required",
                fail_rule,
                f"required suite {suite_name!r} did not execute ({run_failed})",
                sorted(selected),
            )
            continue
        if not present:
            continue  # suite not part of this run's selection
        bad = [t for t in present if not t.succeeded]
        if bad:
            add(
                "required",
                fail_rule,
                f"required suite {suite_name!r}: {len(bad)} test(s) did not pass",
                _labels(bad),
            )

    if any(v.action == "fail" for v in violations):
        status = "failed"
    elif violations:
        status = "warned"
    else:
        status = "passed"
    return PolicyResult(status=status, violations=violations)


def _labels(tests: list[CITestResult]) -> list[str]:
    return [t.test_id if t.platform is None else f"{t.test_id} [{t.platform}]" for t in tests]
