"""Scale checks: the CI layer must stay linear for 10k tests (spec §38, §39, §64)."""

import json
import time

from argus.ci.context import CIContext
from argus.ci.policy import evaluate_policy
from argus.ci.result import CIRunResult, CIRunStatus, CITestResult, TestOutcome, ci_result_to_dict
from argus.ci.scheduler import BalancedScheduler, plan_units
from argus.config.models import AppConfig, CIPolicyConfig
from argus.engine.filters import TestFilter
from argus.models.results import TestStatus
from argus.models.test_definition import TestDefinition

N = 10_000


def _tests(n: int) -> list[TestDefinition]:
    return [
        TestDefinition.model_validate(
            {
                "id": f"T-{i:05d}",
                "name": f"Test {i}",
                "feature": f"Feature {i % 40}",
                "tags": ["smoke"] if i % 3 == 0 else ["regression"],
                "platforms": ["android", "yocto"],
                "steps": [{"action": "log", "message": "x"}],
            }
        )
        for i in range(n)
    ]


def test_scheduling_10k_tests_is_fast():
    config = AppConfig.model_validate(
        {
            "devices": {f"a{i}": {"type": "fake", "platform": "android"} for i in range(4)}
            | {f"y{i}": {"type": "fake", "platform": "yocto"} for i in range(4)}
        }
    )
    tests = _tests(N)
    started = time.perf_counter()
    units = plan_units(config, tests, TestFilter())
    schedule = BalancedScheduler().schedule(config, units, 8)
    elapsed = time.perf_counter() - started
    assert len(units) == 2 * N
    assert sum(w.unit_count for w in schedule.workers) == 2 * N
    loads = [w.unit_count for w in schedule.workers]
    assert max(loads) - min(loads) <= 2 * N // 40  # balanced within one feature group
    assert elapsed < 5.0, elapsed


def test_policy_and_serialization_10k_results_is_fast():
    tests = [
        CITestResult(
            test_id=f"T-{i:05d}",
            name=f"Test {i}",
            feature=f"Feature {i % 40}",
            platform="android",
            status=TestStatus.PASSED if i % 50 else TestStatus.FAILED,
            outcome=TestOutcome.PASSED if i % 50 else TestOutcome.FAILED,
            flaky=i % 97 == 0,
            duration=0.1,
        )
        for i in range(N)
    ]
    result = CIRunResult(
        run_id="perf",
        status=CIRunStatus.FAILED,
        provider="local",
        context=CIContext(provider="local", display_name="Local"),
        tests=tests,
    )
    started = time.perf_counter()
    policy = evaluate_policy(
        CIPolicyConfig(required=["smoke"]),
        tests,
        required_selection={"smoke": {t.test_id for t in tests[: N // 3]}},
    )
    result.policy = policy
    payload = json.dumps(ci_result_to_dict(result))
    elapsed = time.perf_counter() - started
    assert policy.status == "failed"
    assert len(payload) > N * 100
    assert result.summary_dict()["failed"] == N // 50
    assert elapsed < 5.0, elapsed
