"""Scheduling: split the selected tests into worker batches.

Each batch is one ``TestRunner.run`` call. Workers own disjoint device sets,
so two workers never share a device. ``sequential`` is one batch (exactly
the ``argus run`` behavior). ``balanced`` groups tests by feature (feature
setup/teardown then runs once per worker) and assigns groups to the least
loaded eligible worker. The interface is deliberately local-only but shaped
so a distributed scheduler can implement the same ``schedule`` contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from argus.config.models import AppConfig
from argus.engine.filters import TestFilter
from argus.models.test_definition import TestDefinition


@dataclass(frozen=True)
class ExecutionUnit:
    """One (test, platform) execution; ``platform`` is ``None`` for device-less tests."""

    test: TestDefinition
    platform: str | None
    #: Devices this unit must run on (``requires.devices``), if any.
    required_devices: tuple[str, ...] = ()


@dataclass
class Batch:
    """A group of units one worker executes with a single engine run."""

    worker: int
    platform: str | None
    test_ids: list[str] = field(default_factory=list)
    unit_keys: list[tuple[str, str | None]] = field(default_factory=list)

    def filters(self, base: TestFilter) -> TestFilter:
        """Engine filter for this batch (narrow ``base`` to its ids/platform)."""
        return TestFilter(
            test_ids=list(self.test_ids),
            features=list(base.features),
            tags=list(base.tags),
            platforms=[self.platform] if self.platform else list(base.platforms),
            tag_expression=base.tag_expression,
        )


@dataclass
class WorkerPlan:
    worker: int
    #: Device names this worker owns exclusively.
    devices: list[str] = field(default_factory=list)
    batches: list[Batch] = field(default_factory=list)

    @property
    def unit_count(self) -> int:
        return sum(len(b.unit_keys) for b in self.batches)


@dataclass
class Schedule:
    strategy: str
    workers: list[WorkerPlan]
    units: list[ExecutionUnit]
    notes: list[str] = field(default_factory=list)

    @property
    def worker_count(self) -> int:
        return len(self.workers)


def plan_units(
    config: AppConfig, tests: list[TestDefinition], filters: TestFilter
) -> list[ExecutionUnit]:
    """Expand tests into execution units the way the engine will run them."""
    units: list[ExecutionUnit] = []
    for test in tests:
        platforms: list[str | None] = list(test.platforms) or [None]
        if filters.platforms:
            platforms = [p for p in platforms if p in filters.platforms]
        runnable = [p for p in platforms if p is None or config.devices_for_platform(p)]
        if not runnable and not test.platforms:
            runnable = [None]
        for platform in runnable:
            units.append(
                ExecutionUnit(
                    test=test,
                    platform=platform,
                    required_devices=tuple(n for n in test.required_devices if n in config.devices),
                )
            )
    return units


class Scheduler(ABC):
    name: str = ""

    @abstractmethod
    def schedule(self, config: AppConfig, units: list[ExecutionUnit], workers: int) -> Schedule: ...


class SequentialScheduler(Scheduler):
    """One worker, one batch, suite order — identical to ``argus run``."""

    name = "sequential"

    def schedule(self, config: AppConfig, units: list[ExecutionUnit], workers: int) -> Schedule:
        devices = [n for n, d in sorted(config.devices.items()) if d.configured]
        batch = Batch(worker=1, platform=None)
        for unit in units:
            if unit.test.id not in batch.test_ids:
                batch.test_ids.append(unit.test.id)
            batch.unit_keys.append((unit.test.id, unit.platform))
        plan = WorkerPlan(worker=1, devices=devices, batches=[batch] if units else [])
        return Schedule(strategy=self.name, workers=[plan], units=units)


class BalancedScheduler(Scheduler):
    """Feature-grouped least-loaded assignment across device-partitioned workers."""

    name = "balanced"

    def schedule(self, config: AppConfig, units: list[ExecutionUnit], workers: int) -> Schedule:
        if workers <= 1:
            return SequentialScheduler().schedule(config, units, 1)
        notes: list[str] = []
        # 1. Partition devices: each device goes to the worker owning the fewest
        #    devices (deterministic: platforms and names are sorted).
        plans = [WorkerPlan(worker=i + 1) for i in range(workers)]
        owner: dict[str, int] = {}  # device name -> worker index (0-based)
        by_platform: dict[str, list[str]] = {}
        for name, dev in sorted(config.devices.items()):
            if not dev.configured:
                continue
            by_platform.setdefault(dev.effective_platform, []).append(name)
        for platform, names in sorted(by_platform.items()):
            for name in names:
                worker = min(range(workers), key=lambda w: (len(plans[w].devices), w))
                owner[name] = worker
                plans[worker].devices.append(name)
            if len(names) < workers:
                notes.append(
                    f"platform {platform!r}: {len(names)} device(s) for {workers} workers; "
                    f"its tests run on {len(names)} worker(s)"
                )
        # 2. Group units by (feature, platform) so feature lifecycles run once per worker.
        groups: dict[tuple[str, str | None], list[ExecutionUnit]] = {}
        order: list[tuple[str, str | None]] = []
        for unit in units:
            key = (unit.test.feature.strip().lower(), unit.platform)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(unit)
        # 3. Assign each group to the least loaded eligible worker.
        load = [0] * workers
        assigned: dict[tuple[str, str | None], int] = {}
        for key in sorted(order, key=lambda k: -len(groups[k])):  # largest first (LPT)
            eligible = self._eligible(config, groups[key], owner, workers)
            if not eligible:
                eligible = list(range(workers))  # device-less or unresolvable: anywhere
            worker = min(eligible, key=lambda w: (load[w], w))
            assigned[key] = worker
            load[worker] += len(groups[key])
        # 4. Materialize batches per worker in original suite order, one per platform.
        for key in order:
            plan = plans[assigned[key]]
            batch_platform = key[1]
            batch = next((b for b in plan.batches if b.platform == batch_platform), None)
            if batch is None:
                batch = Batch(worker=plan.worker, platform=batch_platform)
                plan.batches.append(batch)
            for unit in groups[key]:
                if unit.test.id not in batch.test_ids:
                    batch.test_ids.append(unit.test.id)
                batch.unit_keys.append((unit.test.id, unit.platform))
        active = [p for p in plans if p.batches]
        if len(active) < workers:
            notes.append(f"{workers - len(active)} worker(s) idle (not enough work or devices)")
        return Schedule(strategy=self.name, workers=active or plans[:1], units=units, notes=notes)

    @staticmethod
    def _eligible(
        config: AppConfig,
        group: list[ExecutionUnit],
        owner: dict[str, int],
        workers: int,
    ) -> list[int]:
        platform = group[0].platform
        required = {d for unit in group for d in unit.required_devices}
        if required:
            owners = {owner[d] for d in required if d in owner}
            return sorted(owners) if len(owners) == 1 else []
        if platform is None:
            return list(range(workers))
        names = config.devices_for_platform(platform)
        return sorted({owner[n] for n in names if n in owner})


def scheduler_for(strategy: str) -> Scheduler:
    if strategy == "sequential":
        return SequentialScheduler()
    if strategy == "balanced":
        return BalancedScheduler()
    raise ValueError(f"unknown scheduling strategy {strategy!r}")
