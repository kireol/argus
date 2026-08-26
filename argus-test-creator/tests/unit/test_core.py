from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import pytest

from argus_test_creator.core.commands import Command, CommandStack
from argus_test_creator.core.errors import CreatorError, WorkerError
from argus_test_creator.core.events import Event, EventBus
from argus_test_creator.core.ids import new_id
from argus_test_creator.core.paths import atomic_write_json, atomic_write_text
from argus_test_creator.core.workers import CancellationToken, WorkerPool


@dataclass(frozen=True, kw_only=True)
class Ping(Event):
    n: int


@dataclass(frozen=True, kw_only=True)
class SubPing(Ping):
    pass


def test_event_bus_dispatches_by_type_and_base_class():
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(Ping, lambda e: seen.append(f"ping{e.n}"))
    bus.subscribe_all(lambda e: seen.append("all"))
    bus.publish(SubPing(n=1))
    assert seen == ["ping1", "all"] or seen == ["all", "ping1"]


def test_event_bus_isolates_subscriber_errors():
    errors = []
    bus = EventBus(on_error=lambda e, exc: errors.append(exc))
    bus.subscribe(Ping, lambda e: 1 / 0)
    ok: list[int] = []
    bus.subscribe(Ping, lambda e: ok.append(e.n))
    bus.publish(Ping(n=3))
    assert ok == [3]
    assert len(errors) == 1


def test_subscription_cancel():
    bus = EventBus()
    seen: list[int] = []
    sub = bus.subscribe(Ping, lambda e: seen.append(e.n))
    bus.publish(Ping(n=1))
    sub.cancel()
    bus.publish(Ping(n=2))
    assert seen == [1]


class Add(Command[list[int]]):
    label = "add"

    def __init__(self, value: int) -> None:
        self.value = value

    def apply(self, target: list[int]) -> None:
        target.append(self.value)

    def revert(self, target: list[int]) -> None:
        target.pop()


def test_command_stack_undo_redo_and_dirty():
    stack = CommandStack[list[int]]([])
    stack.execute(Add(1))
    stack.execute(Add(2))
    assert stack.target == [1, 2] and stack.dirty
    assert stack.undo() is not None and stack.target == [1]
    assert stack.redo() is not None and stack.target == [1, 2]
    stack.mark_clean()
    assert not stack.dirty
    stack.undo()
    assert stack.dirty
    stack.execute(Add(5))
    assert not stack.can_redo and stack.target == [1, 5]
    assert stack.undo_label == "add"


def test_command_stack_limit():
    stack = CommandStack[list[int]]([], limit=3)
    for i in range(10):
        stack.execute(Add(i))
    undone = 0
    while stack.undo():
        undone += 1
    assert undone == 3


def test_worker_pool_runs_and_cancels():
    with WorkerPool(max_workers=2) as pool:
        job = pool.submit("double", lambda x: x * 2, 21)
        assert job.result(5) == 42
        token = CancellationToken()
        started = threading.Event()

        def slow(token: CancellationToken) -> str:
            started.set()
            for _ in range(100):
                if token.cancelled:
                    return "cancelled"
                time.sleep(0.01)
            return "done"

        job2 = pool.submit("slow", slow, token=token)
        started.wait(2)
        job2.cancel()
        assert job2.cancelled


def test_worker_pool_timeout_raises_worker_error():
    with WorkerPool() as pool:
        job = pool.submit("sleep", time.sleep, 0.5)
        with pytest.raises(WorkerError):
            job.result(timeout=0.01)


def test_worker_pool_rejects_after_shutdown():
    pool = WorkerPool()
    pool.shutdown()
    with pytest.raises(WorkerError):
        pool.submit("x", lambda: None)


def test_ids_unique_and_prefixed():
    ids = {new_id("step") for _ in range(1000)}
    assert len(ids) == 1000
    assert all(i.startswith("step_") for i in ids)


def test_atomic_writes(tmp_path):
    path = tmp_path / "a" / "b.json"
    atomic_write_json(path, {"z": 1, "a": [1, 2]})
    assert path.read_text() == '{\n  "a": [\n    1,\n    2\n  ],\n  "z": 1\n}\n'
    atomic_write_text(path, "x")
    assert path.read_text() == "x"
    assert not list(path.parent.glob(".b.json.*"))


def test_creator_error_str_includes_remediation():
    err = CreatorError("boom", remediation="try again")
    assert "boom" in str(err) and "try again" in str(err)
