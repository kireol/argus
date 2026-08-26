"""Engine integration and end-to-end scenarios on the demo store (FakeClock throughout)."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.stress.conftest import demo_app_config

from argus.stress.clock import FakeClock
from argus.stress.config import StressConfig
from argus.stress.engine import StressComponents, StressEngine, StressRunResult
from argus.stress.faults import FakeFaultInjector
from argus.stress.minimize import Minimizer, any_application_failure, same_signature
from argus.stress.models import FailureCategory, FailureSeverity, TraceEventType
from argus.stress.trace import load_trace

pytestmark = pytest.mark.integration

PRODUCTS = {
    "operations": [
        "create", "update", "delete"],
    "state_key": "products", "current_key": "current_product",
    "fields": {"title": {"type": "string", "display": True},
               "price": {"type": "number", "min": 0, "max": 100},
               "stock": {"type": "integer", "min": 0, "max": 50},
               "status": {"type": "enum", "values": ["active", "disabled"]}},
    "disable": {"status": "disabled"},
}
FAST = {"min": 0.001, "max": 0.002}
SAFE_TEST = {"allow_destructive_mutations": True, "environment": "test",
             "allowed_entities": ["products"]}


def _engine(config, **kw) -> StressEngine:
    return StressEngine(config, clock=FakeClock(), **kw)


def _scenario(config, **overrides) -> StressConfig:
    data = config.stress.model_dump(mode="python")
    data.update(overrides)
    return StressConfig.model_validate(data)


def test_monkey_run_is_deterministic_and_persists_trace(tmp_path):
    config = demo_app_config(tmp_path, buggy=False, max_actions=40, duration="10m",
                             monkey={"delay": FAST, "burst_probability": 0.2})
    engine = _engine(config)
    first = engine.run(config.stress, seed=123)
    second = _engine(config).run(config.stress, seed=123)
    assert first.record.seed == 123 and first.record.summary.actions == 40
    describe = lambda r: [e.describe() for e in r.events if e.event_type == TraceEventType.ACTION]  # noqa: E731
    assert describe(first) == describe(second)
    assert describe(first) != describe(_engine(config).run(config.stress, seed=124))
    assert first.run_dir is not None and (first.run_dir / "trace.jsonl").is_file()
    record = json.loads((first.run_dir / "run.json").read_text())
    assert record["seed"] == 123 and record["status"] == "completed"
    assert record["argus_version"] and record["device"] == "store"
    trace = load_trace(first.run_dir / "trace.jsonl")
    assert trace[0].event_type == TraceEventType.RUN_STARTED
    assert trace[-1].event_type == TraceEventType.RUN_FINISHED
    assert record["summary"]["stop_reason"].startswith("max actions")


def test_limits_duration_uses_injected_clock(tmp_path):
    config = demo_app_config(tmp_path, buggy=False, duration="30s", max_actions=100000,
                             monkey={"delay": {"min": 1, "max": 1}})
    result = _engine(config).run(config.stress, seed=5)
    assert result.record.summary.stop_reason.startswith("duration reached")
    assert 25 <= result.record.summary.actions <= 35
    assert result.record.summary.duration >= 30


def test_chaos_finds_stale_checkout_and_crash_in_buggy_app(tmp_path):
    config = demo_app_config(
        tmp_path, buggy=True, max_actions=250, duration="10m",
        monkey={"delay": FAST,
                "actions": {"tap": 45, "back": 10, "type_text": 6, "wait": 2, "double_tap": 3},
                "targets": {"regions": [
                    {"name": "Add to cart / Checkout", "x": 40, "y": 1080, "width": 640,
                     "height": 80, "weight": 3},
                    {"name": "Back", "x": 40, "y": 1180, "width": 640, "height": 80}]},
                "typing": {"words": ["batman", "dune"]}},
        backend_mutations={"enabled": True, "probability": 0.15, "entities": {"products": PRODUCTS},
                           "operations": {"delete": {"enabled": True, "weight": 5},
                                          "update": {"weight": 50}, "create": {"weight": 30},
                                          "disable": {"enabled": True, "weight": 5}},
                           # The targeted race: while the cart is on screen, delete the
                           # product the user is about to check out.
                           "scheduled": [{"mutation": "delete", "entity": "products",
                                          "timing": "on_context", "when_text": "Your cart",
                                          "repeat": True}],
                           "reconcile_timeout": "0.001s"},
        failures={"max_failures": 50, "error_words": ["something went wrong", "not running"]},
        safety=SAFE_TEST,
    )
    result = _engine(config).run(config.stress, seed=84729163)
    categories = {f.category for f in result.application_failures}
    assert FailureCategory.UNEXPECTED_SUCCESS in categories, categories
    stale = next(f for f in result.application_failures
                 if f.category == FailureCategory.UNEXPECTED_SUCCESS)
    assert stale.mutation is not None and stale.mutation.mutation_type in ("delete", "disable")
    assert stale.evidence.get("after") and stale.evidence.get("history")
    assert result.record.summary.mutations > 0
    assert result.record.summary.reproducible_failures == len(result.application_failures)
    # No mutation ever targeted something other than the allow-listed entity.
    assert all(e.mutation.entity_type == "products" for e in result.events
               if e.event_type == TraceEventType.MUTATION)


def test_correct_app_has_no_stale_state_failures(tmp_path):
    config = demo_app_config(
        tmp_path, buggy=False, max_actions=150, duration="10m",
        monkey={"delay": FAST, "actions": {"tap": 50, "back": 10, "wait": 2},
                "targets": {"regions": [
                    {"name": "Checkout", "x": 40, "y": 1080, "width": 640, "height": 80,
                     "weight": 3}]}},
        backend_mutations={"enabled": True, "probability": 0.3, "entities": {"products": PRODUCTS},
                           "operations": {"delete": {"enabled": True, "weight": 50},
                                          "update": {"weight": 50}},
                           "reconcile_timeout": "0.001s"},
        failures={"max_failures": 50, "error_words": ["exception"]},
        safety=SAFE_TEST,
    )
    result = _engine(config).run(config.stress, seed=7)
    assert not [f for f in result.application_failures
                if f.category == FailureCategory.UNEXPECTED_SUCCESS]
    # Plain "still visible" sightings are heuristics: never more than warnings.
    assert all(f.severity == FailureSeverity.WARNING for f in result.application_failures
               if f.category == FailureCategory.STALE_STATE)
    assert result.record.summary.mutations > 0


def test_crash_is_detected_and_app_is_relaunched(tmp_path):
    config = demo_app_config(
        tmp_path, max_actions=80, duration="10m",
        monkey={"delay": FAST, "actions": {"type_text": 5, "tap": 10},
                "typing": {"words": ["<script>", "batman"]}},
        failures={"error_words": ["not running"]},
    )
    result = _engine(config, persist=False).run(config.stress, seed=21)
    crashes = [f for f in result.application_failures if f.category == FailureCategory.CRASH]
    assert crashes and crashes[0].severity == FailureSeverity.CRITICAL
    notes = [e for e in result.events if e.event_type == TraceEventType.NOTE
             and e.metadata.get("reason") == "restart_after_crash"]
    assert len(notes) == len(crashes)  # every crash was followed by a relaunch
    assert result.record.summary.actions == 80
    # With relaunch disabled the dead app just sits there: one crash, then a hang report.
    config.stress.failures.restart_after_crash = False
    result = _engine(config, persist=False).run(config.stress, seed=21)
    assert len([f for f in result.application_failures if f.category == FailureCategory.CRASH]) == 1  # noqa: E501
    assert any(f.category == FailureCategory.HANG for f in result.application_failures)


def test_dry_run_blocks_every_mutation_and_runs_no_lifecycle(tmp_path):
    config = demo_app_config(
        tmp_path, max_actions=30, duration="10m",
        monkey={"delay": FAST, "actions": {"tap": 5, "restart": 5, "back": 2}},
        backend_mutations={"enabled": True, "probability": 1.0, "entities": {"products": PRODUCTS},
                           "operations": {"delete": {"enabled": True, "weight": 1},
                                          "update": {"weight": 1}}},
        safety=SAFE_TEST,
    )
    engine = _engine(config, persist=False)
    result = engine.run(config.stress, seed=3, dry_run=True)
    assert result.record.dry_run and result.run_dir is None
    mutations = [e for e in result.events if e.event_type == TraceEventType.MUTATION]
    assert mutations and all(e.mutation_outcome.blocked for e in mutations)
    assert all(e.mutation_outcome.reason == "dry run" for e in mutations)
    assert result.record.summary.mutations == 0
    assert result.record.summary.mutations_blocked == len(mutations)
    assert result.record.summary.actions == 30


def test_destructive_mutations_blocked_without_opt_in_or_environment(tmp_path):
    base = dict(max_actions=25, duration="10m", monkey={"delay": FAST},
                backend_mutations={"enabled": True, "probability": 1.0,
                                   "entities": {"products": PRODUCTS},
                                   "operations": {"delete": {"enabled": True, "weight": 1}}})
    config = demo_app_config(tmp_path / "a", **base, safety={"allow_destructive_mutations": False,
                                                              "environment": "test"})
    result = _engine(config, persist=False).run(config.stress, seed=1)
    outcomes = [e.mutation_outcome for e in result.events if e.event_type == TraceEventType.MUTATION]  # noqa: E501
    assert outcomes and all(o.blocked and "destructive" in o.reason for o in outcomes)
    config = demo_app_config(tmp_path / "b", **base,
                             safety={"allow_destructive_mutations": True, "environment": "prod"})
    result = _engine(config, persist=False).run(config.stress, seed=1)
    outcomes = [e.mutation_outcome for e in result.events if e.event_type == TraceEventType.MUTATION]  # noqa: E501
    assert outcomes and all(o.blocked for o in outcomes)
    assert result.record.summary.mutations == 0


def test_replay_reproduces_same_logical_sequence_and_failures(tmp_path):
    config = demo_app_config(
        tmp_path, buggy=True, max_actions=120, duration="10m",
        monkey={"delay": FAST, "actions": {"tap": 40, "back": 10, "type_text": 6},
                "targets": {"regions": [
                    {"name": "Checkout", "x": 40, "y": 1080, "width": 640, "height": 80,
                     "weight": 3}]}, "typing": {"words": ["<script>"]}},
        backend_mutations={"enabled": True, "probability": 0.3, "entities": {"products": PRODUCTS},
                           "operations": {"delete": {"enabled": True, "weight": 1}},
                           "reconcile_timeout": "0.001s"},
        failures={"error_words": ["not running"]},
        safety=SAFE_TEST,
    )
    engine = _engine(config)
    original = engine.run(config.stress, seed=99)
    assert original.application_failures
    events = load_trace(original.run_dir / "trace.jsonl")
    replay = _engine(config).run(config.stress, seed=99, script=events,
                                 replay_of=original.record.run_id)
    assert replay.record.replay_of == original.record.run_id

    def actions(r):  # full traces from disk, not the bounded in-memory tail
        return [e.action.describe() for e in load_trace(r.run_dir / "trace.jsonl")
                if e.event_type == TraceEventType.ACTION]

    def mutations(r):
        return [e.mutation.describe() for e in load_trace(r.run_dir / "trace.jsonl")
                if e.event_type == TraceEventType.MUTATION]

    assert actions(replay) == actions(original)
    assert mutations(replay) == mutations(original)
    assert {f.signature for f in replay.application_failures} == {
        f.signature for f in original.application_failures}


def test_minimizer_reduces_sequence_and_keeps_failure(tmp_path):
    config = demo_app_config(
        tmp_path, buggy=True, max_actions=60, duration="10m",
        monkey={"delay": FAST, "actions": {"tap": 40, "type_text": 10},
                "typing": {"words": ["<script>"]}},
        failures={"error_words": ["not running"]},
    )
    engine = _engine(config)
    original = engine.run(config.stress, seed=11)
    crash = next(f for f in original.application_failures if f.category == FailureCategory.CRASH)
    events = load_trace(original.run_dir / "trace.jsonl")
    minimizer = Minimizer(engine, config.stress, seed=11, predicate=same_signature(crash.signature),
                          source_run_id=original.record.run_id, max_iterations=40)
    result = minimizer.minimize(events)
    assert result.reproduced
    assert result.minimized_steps < result.original_steps
    assert result.minimized_steps <= 3  # typing '<script>' is enough
    assert result.final_run is not None and result.final_run.record.minimized_from == original.record.run_id  # noqa: E501
    assert any(f.category == FailureCategory.CRASH for f in result.final_run.application_failures)
    assert result.replays <= result.iterations + 2  # cache prevents duplicate replays
    assert any_application_failure(result.final_run)


def test_minimizer_reports_when_nothing_reproduces(tmp_path):
    config = demo_app_config(tmp_path, buggy=False, max_actions=10, duration="10m",
                             monkey={"delay": FAST, "actions": {"tap": 1}})
    engine = _engine(config)
    original = engine.run(config.stress, seed=2)
    events = load_trace(original.run_dir / "trace.jsonl")
    result = Minimizer(engine, config.stress, seed=2, predicate=same_signature("crash:crash"),
                       max_iterations=5).minimize(events)
    assert not result.reproduced and result.final_run is None and result.iterations == 0


def test_stop_on_first_and_max_failures_policies(tmp_path):
    base = dict(max_actions=200, duration="10m",
                monkey={"delay": FAST, "actions": {"type_text": 10, "tap": 1},
                        "typing": {"words": ["<script>"]}})
    first = demo_app_config(tmp_path / "a", **base,
                            failures={"stop_on_first": True, "error_words": ["not running"]})
    result = _engine(first, persist=False).run(first.stress, seed=4)
    assert result.record.summary.stop_reason.startswith("stop-on-first")
    assert result.record.summary.actions < 200
    capped = demo_app_config(tmp_path / "b", **base,
                             failures={"max_failures": 2, "error_words": ["not running"]})
    result = _engine(capped, persist=False).run(capped.stress, seed=4)
    assert result.record.summary.stop_reason == "maximum failures reached (2)"


def test_cancellation_is_graceful_and_persists(tmp_path):
    config = demo_app_config(tmp_path, max_actions=10000, duration="1h",
                             monkey={"delay": FAST})
    engine = _engine(config)
    cancel = threading.Event()
    counter = {"n": 0}

    class Trip(FakeFaultInjector):
        pass

    original_observe = engine._safe_observe

    def observe_and_trip(context):
        counter["n"] += 1
        if counter["n"] == 15:
            cancel.set()
        return original_observe(context)

    engine._safe_observe = observe_and_trip  # type: ignore[method-assign]
    result = engine.run(config.stress, seed=8, cancel=cancel)
    assert result.record.status == "cancelled"
    assert result.record.summary.stop_reason == "cancelled"
    assert 10 <= result.record.summary.actions <= 20
    trace = load_trace(result.run_dir / "trace.jsonl")
    assert trace[-1].event_type == TraceEventType.RUN_FINISHED
    assert json.loads((result.run_dir / "run.json").read_text())["status"] == "cancelled"


def test_faults_are_applied_and_cleared_with_fake_injector(tmp_path):
    injector = FakeFaultInjector()
    config = demo_app_config(tmp_path, max_actions=40, duration="10m",
                             monkey={"delay": {"min": 0.5, "max": 0.5}},
                             faults={"enabled": True, "probability": 0.5,
                                     "duration_max": "1s"})
    engine = _engine(config, persist=False, components=StressComponents(fault_injector=injector))
    result = engine.run(config.stress, seed=6)
    applied = [e for e in result.events if e.event_type == TraceEventType.FAULT]
    cleared = [e for e in result.events if e.event_type == TraceEventType.FAULT_CLEARED]
    assert applied and len(cleared) == len(applied)
    assert injector.active() == [] and result.record.summary.faults == len(applied)


def test_unsupported_actions_are_skipped_not_crashed(tmp_path):
    config = demo_app_config(tmp_path, max_actions=20, duration="10m",
                             monkey={"delay": FAST, "actions": {"rotate": 10, "tap": 1}})
    result = _engine(config, persist=False).run(config.stress, seed=9)
    notes = [e for e in result.events if e.event_type == TraceEventType.NOTE]
    assert any("rotate excluded" in str(n.metadata.get("message")) for n in notes)
    assert all(e.action.action_type == "tap" for e in result.events
               if e.event_type == TraceEventType.ACTION)


def test_concurrent_runs_are_isolated(tmp_path):
    def one(seed: int) -> StressRunResult:
        config = demo_app_config(tmp_path / f"r{seed}", buggy=False, max_actions=30,
                                 duration="10m", monkey={"delay": FAST})
        return _engine(config).run(config.stress, seed=seed)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, [1, 2, 3, 1]))
    describe = lambda r: [e.describe() for e in r.events if e.event_type == TraceEventType.ACTION]  # noqa: E731
    assert describe(results[0]) == describe(results[3])
    assert describe(results[0]) != describe(results[1])
    assert len({r.record.run_id for r in results}) == 4
    assert all(r.run_dir is not None and (r.run_dir / "trace.jsonl").is_file() for r in results)


def test_long_synthetic_run_keeps_memory_bounded(tmp_path):
    config = demo_app_config(
        tmp_path, buggy=False, max_actions=3000, duration="2h",
        monkey={"delay": {"min": 1, "max": 2}, "actions": {"tap": 5, "back": 1, "wait": 1}},
        backend_mutations={"enabled": True, "probability": 0.2, "entities": {"products": PRODUCTS},
                           "operations": {"update": {"weight": 1}, "create": {"weight": 1}}},
        limits={"max_actions": 3000, "duration": "2h", "observe_every": 5},
        evidence={"history": 20, "observations": 2},
        safety={"environment": "test", "allowed_entities": ["products"]},
    )
    engine = _engine(config)
    captured = {}

    original_finish = engine._finish

    def finish(context, record, trace, run_dir):
        captured["actions"] = len(context.action_history)
        captured["mutations"] = len(context.mutation_history)
        captured["observations"] = len(context.observations)
        captured["tail"] = len(context.trace.recent(10**9))
        return original_finish(context, record, trace, run_dir)

    engine._finish = finish  # type: ignore[method-assign]
    result = engine.run(config.stress, seed=42)
    assert result.record.summary.actions == 3000
    assert captured["actions"] <= 20 and captured["mutations"] <= 20
    assert captured["observations"] <= 2 and captured["tail"] <= 20
    assert result.record.summary.duration > 3000  # fake clock advanced through delays
    trace_lines = sum(1 for _ in (result.run_dir / "trace.jsonl").open())
    assert trace_lines >= 3000
