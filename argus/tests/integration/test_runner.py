"""End-to-end runner behavior against fake adapters."""

from pathlib import Path

import pytest
import yaml
from tests.conftest import make_artwork

from argus.config.models import AppConfig
from argus.engine.filters import TestFilter
from argus.engine.runner import FailurePolicy, RunOptions, TestRunner
from argus.events.bus import EventBus
from argus.events.events import TestFailed, TestPassed
from argus.models.results import RunStatus, TestStatus

pytestmark = pytest.mark.integration


def build_config(tmp_path: Path, *, movie_id_initial=None) -> AppConfig:
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    make_artwork((30, 60, 130), (240, 200, 60)).save(assets / "movie_123.png")
    make_artwork((130, 30, 40), (60, 220, 180)).save(assets / "movie_456.png")

    config = AppConfig.model_validate(
        {
            "backend": {"type": "fake", "initial_state": {"movieId": movie_id_initial}},
            "devices": {
                "fake_android": {
                    "type": "fake",
                    "platform": "android",
                    "render": {
                        "state_image": {
                            "key": "movieId",
                            "template": "movie_{value}.png",
                            "search_dirs": [str(assets)],
                            "position": [100, 100],
                        }
                    },
                    "instrumentation": {
                        "type": "fake",
                        "status": {"ready": True, "screen": "movie_details"},
                    },
                },
            },
            "test_paths": [str(tmp_path / "suites")],
            "asset_paths": [str(assets)],
            "results": {"dir": str(tmp_path / "results")},
            "wait": {"default_timeout": "2s", "default_poll_interval": "50ms"},
        }
    )
    config.root_dir = str(tmp_path)
    return config


def write_suite(tmp_path: Path, tests: list[dict], filename: str = "suite.yaml") -> None:
    suites = tmp_path / "suites"
    suites.mkdir(exist_ok=True)
    (suites / filename).write_text(yaml.safe_dump({"tests": tests}))


def passing_test(test_id="P-001", **overrides):
    test = {
        "id": test_id,
        "name": f"Passing {test_id}",
        "feature": "Feature",
        "platforms": ["android"],
        "steps": [
            {"action": "backend.set", "data": {"movieId": 123}},
            {
                "action": "wait_until",
                "condition": {"type": "image_present", "image": "movie_123.png"},
                "timeout": "2s",
            },
        ],
    }
    test.update(overrides)
    return test


def failing_test(test_id="F-001", **overrides):
    test = {
        "id": test_id,
        "name": f"Failing {test_id}",
        "feature": "Feature",
        "platforms": ["android"],
        "steps": [
            {"action": "backend.set", "data": {"movieId": 123}},
            {
                "action": "verify",
                "condition": {"type": "image_present", "image": "movie_456.png"},
            },
        ],
    }
    test.update(overrides)
    return test


def test_full_passing_run(tmp_path):
    config = build_config(tmp_path)
    write_suite(tmp_path, [passing_test()])
    result = TestRunner(config).run()
    assert result.status == RunStatus.PASSED
    assert result.passed_count == 1
    assert result.preflight  # preflight ran


def test_verification_failure_produces_artifacts(tmp_path):
    config = build_config(tmp_path)
    write_suite(tmp_path, [failing_test()])
    result = TestRunner(config).run()
    assert result.status == RunStatus.STOPPED  # stop-on-failure default
    test_result = result.tests[0]
    assert test_result.status == TestStatus.FAILED
    assert test_result.failure_category == "assertion"
    assert test_result.instrumentation_state is not None
    artifact_dir = Path(test_result.artifact_dir)
    assert (artifact_dir / "actual.png").exists()
    assert (artifact_dir / "expected.png").exists()
    assert (artifact_dir / "diff.png").exists()
    assert (artifact_dir / "instrumentation.json").exists()
    assert (artifact_dir / "metadata.json").exists()
    assert (artifact_dir / "logs.txt").exists()


def test_save_comparison_images_keeps_passing_verifies(tmp_path):
    config = build_config(tmp_path)
    config.results.save_comparison_images = True
    write_suite(tmp_path, [passing_test()])
    result = TestRunner(config).run()
    assert result.status == RunStatus.PASSED
    test_result = result.tests[0]
    assert test_result.artifact_dir is not None
    artifact_dir = Path(test_result.artifact_dir)
    assert (artifact_dir / "actual.png").exists()
    assert (artifact_dir / "expected.png").exists()
    assert (artifact_dir / "diff.png").exists()


def test_stop_on_failure_skips_remaining(tmp_path):
    config = build_config(tmp_path)
    write_suite(tmp_path, [failing_test("F-001"), passing_test("P-002")])
    result = TestRunner(config).run(
        RunOptions(failure_policy=FailurePolicy(stop_on_failure=True))
    )
    assert result.stopped_early
    statuses = {t.test_id: t.status for t in result.tests}
    assert statuses["F-001"] == TestStatus.FAILED
    assert statuses["P-002"] == TestStatus.SKIPPED


def test_continue_on_failure_runs_everything(tmp_path):
    config = build_config(tmp_path)
    write_suite(tmp_path, [failing_test("F-001"), passing_test("P-002")])
    result = TestRunner(config).run(
        RunOptions(failure_policy=FailurePolicy(stop_on_failure=False))
    )
    assert not result.stopped_early
    assert result.status == RunStatus.FAILED
    assert result.passed_count == 1
    assert result.failed_count == 1


def test_max_failures(tmp_path):
    config = build_config(tmp_path)
    write_suite(
        tmp_path,
        [failing_test("F-001"), failing_test("F-002"), failing_test("F-003")],
    )
    result = TestRunner(config).run(
        RunOptions(
            failure_policy=FailurePolicy(stop_on_failure=False, max_failures=2)
        )
    )
    assert result.stopped_early
    assert result.failed_count == 2
    assert result.skipped_count == 1


def test_filtering_by_feature(tmp_path):
    config = build_config(tmp_path)
    write_suite(
        tmp_path,
        [
            passing_test("M-001", feature="Movies"),
            passing_test("S-001", feature="Settings"),
        ],
    )
    result = TestRunner(config).run(
        RunOptions(filters=TestFilter(features=["movies"]))
    )
    assert [t.test_id for t in result.tests] == ["M-001"]


def test_teardown_runs_after_failure(tmp_path):
    config = build_config(tmp_path)
    test = failing_test("F-001")
    test["teardown"] = [{"action": "backend.set", "data": {"movieId": None}}]
    write_suite(tmp_path, [test])
    result = TestRunner(config).run()
    steps = result.tests[0].steps
    assert steps[-1].action == "backend.set"
    assert steps[-1].passed  # teardown executed and succeeded


def test_retry_on_timeout_category(tmp_path):
    config = build_config(tmp_path)
    test = {
        "id": "R-001",
        "name": "Retried timeout",
        "feature": "Feature",
        "platforms": ["android"],
        "retry": {"count": 2, "only": ["timeout"]},
        "steps": [
            {
                "action": "wait_until",
                "condition": {"type": "image_present", "image": "movie_456.png"},
                "timeout": "100ms",
                "poll_interval": "50ms",
            }
        ],
    }
    write_suite(tmp_path, [test])
    result = TestRunner(config).run()
    assert result.tests[0].status == TestStatus.FAILED
    assert result.tests[0].attempts == 3  # 1 + 2 retries


def test_assertion_failure_not_retried(tmp_path):
    config = build_config(tmp_path)
    test = failing_test("R-002")
    test["retry"] = {"count": 2, "only": ["timeout"]}
    write_suite(tmp_path, [test])
    result = TestRunner(config).run()
    assert result.tests[0].attempts == 1


def test_preflight_failure_blocks_execution(tmp_path):
    config = build_config(tmp_path)
    write_suite(tmp_path, [passing_test()])
    # Reference a missing asset so the required Test assets check fails.
    test = passing_test("P-404")
    test["steps"][1]["condition"]["image"] = "does_not_exist.png"
    write_suite(tmp_path, [test], filename="extra.yaml")

    events = EventBus()
    seen = []
    events.subscribe(seen.append)
    result = TestRunner(config, events).run()

    assert result.status == RunStatus.PREFLIGHT_FAILED
    assert result.tests == []  # nothing executed
    assert not any(isinstance(e, (TestPassed, TestFailed)) for e in seen)


def test_events_published_in_order(tmp_path):
    config = build_config(tmp_path)
    write_suite(tmp_path, [passing_test()])
    events = EventBus()
    seen = []
    events.subscribe(seen.append)
    TestRunner(config, events).run()
    names = [type(e).__name__ for e in seen]
    assert names[0] == "TestRunStarted"
    assert names[-1] == "TestRunCompleted"
    assert "TestStarted" in names
    assert "TestPassed" in names


def test_artifacts_discarded_on_success_by_default(tmp_path):
    config = build_config(tmp_path)
    write_suite(tmp_path, [passing_test()])
    result = TestRunner(config).run()
    assert result.tests[0].artifact_dir is None


def test_variables_expand_in_steps(tmp_path):
    config = build_config(tmp_path)
    test = {
        "id": "V-001",
        "name": "Variables",
        "feature": "Feature",
        "platforms": ["android"],
        "parameters": {"movie_id": 123, "movie_image": "movie_123.png"},
        "steps": [
            {"action": "backend.set", "data": {"movieId": "${movie_id}"}},
            {
                "action": "verify",
                "condition": {"type": "image_present", "image": "${movie_image}"},
            },
        ],
    }
    write_suite(tmp_path, [test])
    result = TestRunner(config).run()
    assert result.tests[0].status == TestStatus.PASSED


def test_unknown_action_is_error_not_crash(tmp_path):
    config = build_config(tmp_path)
    test = passing_test("E-001")
    test["steps"] = [{"action": "nonsense.action"}]
    write_suite(tmp_path, [test])
    result = TestRunner(config).run()
    assert result.tests[0].status in (TestStatus.FAILED, TestStatus.ERROR)
    assert "Unknown action" in (result.tests[0].error or "")


def test_skip_to_runs_from_ordinal(tmp_path):
    from argus.events.events import TestRunStarted
    from argus.exceptions import TestDefinitionError

    config = build_config(tmp_path)
    write_suite(
        tmp_path,
        [passing_test("P-001"), passing_test("P-002"), passing_test("P-003")],
    )
    events = EventBus()
    started: list[TestRunStarted] = []
    events.subscribe(started.append, TestRunStarted)
    result = TestRunner(config, events).run(RunOptions(skip_to=2))
    assert [t.test_id for t in result.tests] == ["P-002", "P-003"]
    assert started[0].total_tests == 3
    assert started[0].start_index == 2
    assert started[0].filters["skip_to"] == 2

    with pytest.raises(TestDefinitionError, match="past the end"):
        TestRunner(config).run(RunOptions(skip_to=4))


# -- feature-level setup / teardown ------------------------------------------------------


def write_suite_with_features(tmp_path: Path, tests: list[dict], features: dict) -> None:
    suites = tmp_path / "suites"
    suites.mkdir(exist_ok=True)
    (suites / "suite.yaml").write_text(yaml.safe_dump({"features": features, "tests": tests}))


def _feature_events(events: list) -> list[str]:
    return [
        f"{type(e).__name__}:{e.feature}"
        for e in events
        if type(e).__name__.startswith("Feature")
    ]


def test_feature_setup_runs_once_before_first_test_and_teardown_after_last(tmp_path):
    config = build_config(tmp_path)
    features = {
        "Feature": {
            "setup": [{"action": "backend.set", "data": {"movieId": 123}}],
            "teardown": [{"action": "backend.set", "data": {"movieId": None}}],
        }
    }
    write_suite_with_features(tmp_path, [passing_test("P-001"), passing_test("P-002")], features)
    events = EventBus()
    seen = []
    events.subscribe(seen.append)
    result = TestRunner(config, events).run()
    assert result.status == RunStatus.PASSED
    names = [type(e).__name__ for e in seen]
    assert names.index("FeatureSetupCompleted") < names.index("TestStarted")
    assert names.count("FeatureSetupStarted") == 1
    assert names.count("FeatureTeardownCompleted") == 1
    last_pass = len(names) - 1 - names[::-1].index("TestPassed")
    assert names.index("FeatureTeardownCompleted") > last_pass
    assert _feature_events(seen)[0] == "FeatureSetupStarted:Feature"


def test_feature_setup_is_case_insensitive(tmp_path):
    config = build_config(tmp_path)
    features = {"FEATURE": {"setup": [{"action": "backend.set", "data": {"movieId": 123}}]}}
    write_suite_with_features(tmp_path, [passing_test("P-001", feature="feature")], features)
    events = EventBus()
    seen = []
    events.subscribe(seen.append)
    TestRunner(config, events).run()
    assert "FeatureSetupCompleted" in [type(e).__name__ for e in seen]


def test_feature_setup_failure_fails_remaining_tests_without_running_them(tmp_path):
    config = build_config(tmp_path)
    features = {
        "Feature": {
            "setup": [
                {
                    "action": "verify",
                    "condition": {"type": "image_present", "image": "movie_456.png"},
                }
            ],
            "teardown": [{"action": "backend.set", "data": {"movieId": None}}],
        }
    }
    write_suite_with_features(tmp_path, [passing_test("P-001"), passing_test("P-002")], features)
    events = EventBus()
    seen = []
    events.subscribe(seen.append)
    result = TestRunner(config, events).run(
        RunOptions(failure_policy=FailurePolicy(stop_on_failure=False))
    )
    assert result.status == RunStatus.FAILED
    assert [t.status for t in result.tests] == [TestStatus.FAILED, TestStatus.FAILED]
    assert all("Feature setup failed" in (t.error or "") for t in result.tests)
    assert all(t.steps == [] for t in result.tests)  # never executed
    names = [type(e).__name__ for e in seen]
    assert "ActionStarted" in names  # the setup step itself ran
    assert "FeatureTeardownCompleted" in names  # teardown still runs after a failed setup


def test_feature_teardown_runs_when_run_stops_early(tmp_path):
    config = build_config(tmp_path)
    features = {"Feature": {"teardown": [{"action": "backend.set", "data": {"movieId": None}}]}}
    write_suite_with_features(
        tmp_path, [failing_test("F-001"), passing_test("P-002")], features
    )
    events = EventBus()
    seen = []
    events.subscribe(seen.append)
    result = TestRunner(config, events).run(
        RunOptions(failure_policy=FailurePolicy(stop_on_failure=True))
    )
    assert result.stopped_early
    assert "FeatureTeardownCompleted" in [type(e).__name__ for e in seen]


def test_feature_without_selected_tests_never_runs_setup(tmp_path):
    config = build_config(tmp_path)
    features = {"Other": {"setup": [{"action": "backend.set", "data": {"movieId": 123}}]}}
    write_suite_with_features(tmp_path, [passing_test("P-001")], features)
    events = EventBus()
    seen = []
    events.subscribe(seen.append)
    TestRunner(config, events).run()
    assert not _feature_events(seen)


def test_feature_teardown_failure_does_not_fail_tests(tmp_path):
    config = build_config(tmp_path)
    features = {
        "Feature": {
            "teardown": [
                {
                    "action": "verify",
                    "condition": {"type": "image_present", "image": "movie_456.png"},
                }
            ]
        }
    }
    write_suite_with_features(tmp_path, [passing_test("P-001")], features)
    result = TestRunner(config).run()
    assert result.status == RunStatus.PASSED
