"""Core: deterministic RNG, clocks, configuration, trace, models."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.config.models import AppConfig
from argus.exceptions import ConfigurationError
from argus.stress.clock import FakeClock, MonotonicClock
from argus.stress.config import ActionWeight, StressConfig, load_scenario
from argus.stress.models import (
    Failure,
    FailureCategory,
    FailureSeverity,
    Mutation,
    StressAction,
    Target,
    TraceEventType,
)
from argus.stress.rng import SEED_MAX, DeterministicRNG, new_seed
from argus.stress.trace import Trace, load_trace

# -- RNG ---------------------------------------------------------------------------------------


def test_same_seed_same_sequence():
    a, b = DeterministicRNG(84729163), DeterministicRNG(84729163)
    seq_a = [a.randint(0, 1000) for _ in range(50)] + [a.random() for _ in range(10)]
    seq_b = [b.randint(0, 1000) for _ in range(50)] + [b.random() for _ in range(10)]
    assert seq_a == seq_b
    assert DeterministicRNG(1).random() != DeterministicRNG(2).random()


def test_forked_streams_are_independent_and_stable():
    root = DeterministicRNG(7)
    timing = root.fork("timing")
    first = [timing.random() for _ in range(5)]
    # Drawing more from the root must not shift the forked stream.
    for _ in range(100):
        root.random()
    fresh = DeterministicRNG(7).fork("timing")
    again = [fresh.random() for _ in range(5)]
    assert first == again
    assert root.fork("a").random() != root.fork("b").random()


def test_weighted_choice_respects_weights():
    rng = DeterministicRNG(3)
    picks = [rng.weighted_choice(["a", "b", "c"], [0, 90, 10]) for _ in range(500)]
    assert "a" not in picks
    assert picks.count("b") > picks.count("c") * 3
    assert rng.weighted_choice(["x"], [0]) == "x"  # all-zero weights → uniform
    with pytest.raises(ValueError):
        rng.weighted_choice([], [])
    with pytest.raises(ValueError):
        rng.weighted_choice(["a"], [1, 2])


def test_chance_and_seed_helpers():
    rng = DeterministicRNG(11)
    assert not rng.chance(0) and rng.chance(1)
    hits = sum(rng.chance(0.25) for _ in range(2000))
    assert 380 < hits < 620
    assert 1 <= new_seed() <= SEED_MAX
    assert len(rng.token(8)) == 8 and rng.sample([1, 2, 3], 5) and rng.shuffle([1, 2, 3])


# -- clocks ----------------------------------------------------------------------------------------


def test_fake_clock_advances_only_when_told():
    clock = FakeClock(start=100.0)
    assert clock.monotonic() == 100.0
    clock.sleep(5)
    clock.advance(2.5)
    assert clock.monotonic() == 107.5 and clock.sleeps == [5]
    assert clock.now().isoformat().startswith("2026-01-01T00:00:07.5")
    real = MonotonicClock()
    assert real.monotonic() <= real.monotonic()


# -- configuration --------------------------------------------------------------------------------


def test_app_config_has_stress_section_with_defaults():
    config = AppConfig()
    assert config.stress.name == "stress"
    assert config.stress.monkey.actions["tap"].weight == 40
    assert config.stress.safety.allow_destructive_mutations is False
    assert config.stress.backend_mutations.enabled is False


def test_action_weights_accept_numbers_and_booleans():
    cfg = StressConfig.model_validate({"monkey": {"actions": {"tap": 5, "swipe": False,
                                                              "back": {"weight": 2}}}})
    assert cfg.monkey.actions["tap"] == ActionWeight(weight=5)
    assert cfg.monkey.actions["swipe"].enabled is False
    assert cfg.monkey.actions["back"].weight == 2


def test_shorthands_and_durations():
    cfg = StressConfig.model_validate({"duration": "10m", "max_actions": 50,
                                       "monkey": {"delay": {"min": "50ms", "max": "1s"}}})
    assert cfg.limits.duration_seconds == 600 and cfg.limits.max_actions == 50
    assert cfg.monkey.delay.min_seconds == 0.05 and cfg.monkey.delay.max_seconds == 1.0
    assert cfg.data_mutations.enabled == set()
    cfg2 = StressConfig.model_validate({"data_mutations": {"null": True, "empty": False,
                                                           "unicode": {"enabled": True}}})
    assert cfg2.data_mutations.enabled == {"null", "unicode"}


def test_load_scenario_file_with_overrides(tmp_path: Path):
    path = tmp_path / "chaos.yaml"
    path.write_text(
        "backend: {type: fake}\ndevices: {d: {type: fake}}\n"
        "stress:\n  seed: 42\n  monkey: {actions: {tap: 1}}\n", encoding="utf-8")
    scenario, overrides = load_scenario(path)
    assert scenario.name == "chaos" and scenario.seed == 42
    assert set(overrides) == {"backend", "devices"}
    flat = tmp_path / "flat.yaml"
    flat.write_text("name: flat\nseed: 5\n", encoding="utf-8")
    assert load_scenario(flat)[0].name == "flat"
    with pytest.raises(ConfigurationError):
        load_scenario(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("stress:\n  monkey:\n    bogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_scenario(bad)


# -- trace ---------------------------------------------------------------------------------------


def test_trace_streams_to_disk_and_keeps_bounded_tail(tmp_path: Path):
    trace = Trace(tmp_path / "trace.jsonl", tail=3)
    for i in range(5):
        trace.append(TraceEventType.ACTION, elapsed=float(i), timestamp="t",
                     action=StressAction(action_type="tap", target=Target(x=i, y=i)))
    trace.close()
    assert [e.sequence for e in trace.recent(10)] == [3, 4, 5]
    loaded = load_trace(tmp_path / "trace.jsonl")
    assert [e.sequence for e in loaded] == [1, 2, 3, 4, 5]
    assert loaded[0].action is not None and loaded[0].action.target.x == 0
    # a torn last line is ignored
    with (tmp_path / "trace.jsonl").open("a") as fh:
        fh.write('{"sequence": 6, "elapsed"')
    assert len(load_trace(tmp_path / "trace.jsonl")) == 5


def test_trace_in_memory_when_no_path():
    trace = Trace(None, tail=50)
    trace.append(TraceEventType.NOTE, elapsed=0.0, timestamp="t", metadata={"m": 1})
    assert trace.count == 1 and trace.recent(1)[0].metadata == {"m": 1}


# -- models ----------------------------------------------------------------------------------------


def test_models_describe_and_serialize():
    action = StressAction(action_type="swipe", target=Target(x=1, y=2, label="Row"),
                          parameters={"direction": "up"})
    assert action.describe() == "SWIPE Row"
    mutation = Mutation(mutation_type="delete", entity_type="movies", entity_id="123",
                        destructive=True)
    assert mutation.describe() == "DELETE movies/123"
    assert Mutation.model_validate(mutation.model_dump(mode="json")) == mutation
    failure = Failure(failure_id="f", category=FailureCategory.STALE_STATE,
                      severity=FailureSeverity.ERROR, message="m", detector="stale_entity",
                      step=3, timestamp="t")
    assert failure.signature == "stale_state:stale_entity"
    assert FailureCategory.INFRASTRUCTURE.is_application is False
    assert FailureSeverity.CRITICAL.rank > FailureSeverity.WARNING.rank
