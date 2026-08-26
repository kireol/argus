"""Worker scheduling: sequential preserves order; balanced partitions devices."""

from tests.conftest import make_test

from argus.ci.scheduler import BalancedScheduler, SequentialScheduler, plan_units, scheduler_for
from argus.config.models import AppConfig
from argus.engine.filters import TestFilter


def _config(**devices) -> AppConfig:
    return AppConfig.model_validate({"devices": devices})


def _dev(platform):
    return {"type": "fake", "platform": platform}


def test_plan_units_expands_platforms_like_the_engine():
    config = _config(a1=_dev("android"), y1=_dev("yocto"))
    tests = [
        make_test(id="T1", platforms=["android", "yocto"]),
        make_test(id="T2", platforms=["ios"]),  # no device -> not runnable
        make_test(id="T3", platforms=[]),  # device-less -> runs once
    ]
    units = plan_units(config, tests, TestFilter())
    assert [(u.test.id, u.platform) for u in units] == [
        ("T1", "android"),
        ("T1", "yocto"),
        ("T3", None),
    ]
    units = plan_units(config, tests, TestFilter(platforms=["yocto"]))
    assert [(u.test.id, u.platform) for u in units] == [("T1", "yocto"), ("T3", None)]


def test_sequential_is_one_batch_in_suite_order():
    config = _config(a1=_dev("android"))
    units = plan_units(
        config, [make_test(id=f"T{i}", platforms=["android"]) for i in range(3)], TestFilter()
    )
    schedule = SequentialScheduler().schedule(config, units, 4)
    assert schedule.worker_count == 1
    batch = schedule.workers[0].batches[0]
    assert batch.test_ids == ["T0", "T1", "T2"]
    assert schedule.workers[0].devices == ["a1"]


def test_balanced_partitions_devices_exclusively():
    config = _config(a1=_dev("android"), a2=_dev("android"), y1=_dev("yocto"))
    tests = [
        make_test(id=f"T{i}", feature=f"F{i}", platforms=["android", "yocto"]) for i in range(4)
    ]
    units = plan_units(config, tests, TestFilter())
    schedule = BalancedScheduler().schedule(config, units, 2)
    owned = [set(w.devices) for w in schedule.workers]
    assert owned[0].isdisjoint(owned[1])
    assert set().union(*owned) == {"a1", "a2", "y1"}
    # Every unit is scheduled exactly once, on a worker that owns a matching device.
    seen = []
    for worker in schedule.workers:
        for batch in worker.batches:
            for key in batch.unit_keys:
                seen.append(key)
                platform = key[1]
                assert any(config.devices[d].effective_platform == platform for d in worker.devices)
    assert sorted(seen) == sorted((u.test.id, u.platform) for u in units)


def test_balanced_keeps_feature_groups_together_and_balances_load():
    config = _config(a1=_dev("android"), a2=_dev("android"))
    tests = [make_test(id=f"A{i}", feature="Alpha", platforms=["android"]) for i in range(4)]
    tests += [make_test(id=f"B{i}", feature="Beta", platforms=["android"]) for i in range(2)]
    tests += [make_test(id=f"C{i}", feature="Gamma", platforms=["android"]) for i in range(2)]
    units = plan_units(config, tests, TestFilter())
    schedule = BalancedScheduler().schedule(config, units, 2)
    counts = sorted(w.unit_count for w in schedule.workers)
    assert counts == [4, 4]
    for worker in schedule.workers:
        features = {t[0][0] for b in worker.batches for t in b.unit_keys}
        # Alpha (4) alone on one worker, Beta + Gamma on the other.
        assert features in ({"A"}, {"B", "C"})


def test_balanced_required_device_pins_the_worker():
    config = _config(a1=_dev("android"), a2=_dev("android"))
    tests = [make_test(id="T1", platforms=["android"], requires={"devices": ["a2"]})]
    schedule = BalancedScheduler().schedule(config, plan_units(config, tests, TestFilter()), 2)
    worker = next(w for w in schedule.workers if w.batches)
    assert "a2" in worker.devices


def test_balanced_with_fewer_devices_than_workers_notes_it():
    config = _config(a1=_dev("android"))
    tests = [make_test(id=f"T{i}", feature=f"F{i}", platforms=["android"]) for i in range(3)]
    schedule = BalancedScheduler().schedule(config, plan_units(config, tests, TestFilter()), 3)
    assert schedule.worker_count == 1
    assert any("1 device(s) for 3 workers" in n for n in schedule.notes)


def test_scheduler_factory():
    assert scheduler_for("sequential").name == "sequential"
    assert scheduler_for("balanced").name == "balanced"
