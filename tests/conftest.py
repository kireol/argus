from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from argus_test_creator.adapters.fake import FakeRecorder
from argus_test_creator.core.events import EventBus
from argus_test_creator.core.workers import WorkerPool
from argus_test_creator.integrations.argus import discover_argus
from argus_test_creator.observation import FakeOCRProvider
from argus_test_creator.project import CreatorProject
from argus_test_creator.recording import RecordingSession
from argus_test_creator.targets import builtin_targets

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def events() -> EventBus:
    return EventBus()


@pytest.fixture
def workers() -> Iterator[WorkerPool]:
    pool = WorkerPool(max_workers=2)
    yield pool
    pool.shutdown(wait=True, timeout=5)


@pytest.fixture
def fake_target():
    return builtin_targets()[0]


@pytest.fixture
def recorder(fake_target) -> FakeRecorder:
    rec = FakeRecorder(fake_target)
    rec.connect()
    return rec


@pytest.fixture
def project(tmp_path: Path) -> CreatorProject:
    return CreatorProject.create(tmp_path / "proj", name="test")


@pytest.fixture
def session(recorder, project, events, workers) -> Iterator[RecordingSession]:
    sess = RecordingSession(
        adapter=recorder, directory=project.sessions_dir / "s1", events=events, workers=workers,
        ocr=FakeOCRProvider(), settle_ms=0,
    )
    yield sess
    if sess.state.value == "recording":
        sess.stop()


def wait_jobs(pool: WorkerPool, timeout: float = 30) -> None:
    for job in pool.active_jobs():
        job.result(timeout)


def settle(session: RecordingSession, timeout: float = 30) -> None:
    """Wait for the drain thread and analysis jobs (tests only)."""
    session.flush(timeout)


@pytest.fixture
def argus_executable() -> str:
    candidates = [
        os.environ.get("ARGUS_EXECUTABLE"),
        str(Path(__file__).resolve().parents[2] / "argus" / ".venv" / "bin" / "argus"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    info = discover_argus()
    if info is None:
        pytest.skip("argus executable not available")
    return str(info.executable)
