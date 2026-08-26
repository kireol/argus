"""Synthetic high-frequency getevent stream: several minutes of touch activity.

Measures parser and recognizer throughput, end-to-end queue latency through
the recorder thread, and that memory stays bounded (no raw stream retention).
Thresholds are deliberately loose — the point is "comfortably faster than a
human", not micro-benchmarks.
"""

from __future__ import annotations

import gc
import random
import time
import tracemalloc

import pytest

from argus_test_creator.adapters.android import (
    AndroidGestureRecognizer,
    AndroidRecorder,
    FakeAdbClient,
    FakeDevice,
    GetEventParser,
)
from argus_test_creator.recording.adapter import EventSink
from argus_test_creator.targets import builtin_targets

pytestmark = pytest.mark.performance

DEV = "/dev/input/event2"
REPORT_HZ = 240  # modern panels report at 120–240 Hz


def synthetic_stream(*, minutes: float, seed: int = 7) -> tuple[list[str], int]:
    """Realistic mix: taps, swipes, long presses, occasional two-finger gestures."""
    rng = random.Random(seed)
    lines: list[str] = []
    t = 100.0
    gestures = 0
    end = t + minutes * 60
    tracking = 1

    def emit(ts: float, code: str, value: str) -> None:
        lines.append(f"[{ts:12.6f}] {DEV}: EV_ABS       {code:<20} {value}\n")

    def syn(ts: float) -> None:
        lines.append(f"[{ts:12.6f}] {DEV}: EV_SYN       SYN_REPORT           00000000\n")

    while t < end:
        kind = rng.choices(["tap", "swipe", "long", "multi"], weights=[50, 35, 10, 5])[0]
        x, y = rng.randint(100, 4000), rng.randint(100, 4000)
        emit(t, "ABS_MT_SLOT", "00000000")
        emit(t, "ABS_MT_TRACKING_ID", f"{tracking:08x}")
        tracking += 1
        emit(t, "ABS_MT_POSITION_X", f"{x:08x}")
        emit(t, "ABS_MT_POSITION_Y", f"{y:08x}")
        syn(t)
        if kind == "tap":
            duration, frames = 0.08, 2
        elif kind == "long":
            duration, frames = 0.9, int(0.9 * REPORT_HZ)
        else:
            duration, frames = 0.35, int(0.35 * REPORT_HZ)
        if kind == "multi":
            emit(t, "ABS_MT_SLOT", "00000001")
            emit(t, "ABS_MT_TRACKING_ID", f"{tracking:08x}")
            tracking += 1
            emit(t, "ABS_MT_POSITION_X", f"{x + 500:08x}")
            emit(t, "ABS_MT_POSITION_Y", f"{y:08x}")
            syn(t)
        for i in range(1, frames + 1):
            ts = t + duration * i / frames
            if kind in ("swipe", "multi"):
                emit(ts, "ABS_MT_SLOT", "00000000")
                emit(ts, "ABS_MT_POSITION_Y", f"{max(y - 4 * i, 0):08x}")
                if kind == "multi":
                    emit(ts, "ABS_MT_SLOT", "00000001")
                    emit(ts, "ABS_MT_POSITION_X", f"{x + 500 + 3 * i:08x}")
            elif kind == "long":
                emit(ts, "ABS_MT_POSITION_X", f"{x + (i % 2):08x}")
            syn(ts)
        t += duration
        emit(t, "ABS_MT_SLOT", "00000000")
        emit(t, "ABS_MT_TRACKING_ID", "ffffffff")
        if kind == "multi":
            emit(t, "ABS_MT_SLOT", "00000001")
            emit(t, "ABS_MT_TRACKING_ID", "ffffffff")
        syn(t)
        gestures += 1
        t += rng.uniform(0.3, 1.5)  # think time
    return lines, gestures


def test_parser_and_recognizer_throughput():
    lines, expected = synthetic_stream(minutes=3)
    assert len(lines) > 15_000
    parser = GetEventParser()
    started = time.perf_counter()
    events = [e for e in (parser.parse_line(ln) for ln in lines) if e is not None]
    parse_s = time.perf_counter() - started
    assert parser.malformed == 0 and len(events) == len(lines)
    recognizer = AndroidGestureRecognizer(touch_device=DEV)
    started = time.perf_counter()
    gestures = [g for e in events for g in recognizer.feed(e)]
    recognize_s = time.perf_counter() - started
    assert len(gestures) == expected
    parse_rate = len(lines) / parse_s
    recognize_rate = len(events) / recognize_s
    print(f"\nparse: {parse_rate:,.0f} lines/s  recognize: {recognize_rate:,.0f} events/s "
          f"({len(lines):,} lines, {expected} gestures, 3 min of activity)")
    # A 240 Hz panel with ~4 events per frame is ~1k lines/s; demand 20× headroom.
    assert parse_rate > 20_000
    assert recognize_rate > 20_000


def test_recorder_end_to_end_latency_and_bounded_memory():
    lines, expected = synthetic_stream(minutes=2, seed=3)
    device = FakeDevice("PERF", width=1080, height=2400)
    fake = FakeAdbClient([device])
    fake.script_lines("PERF", lines)
    target = next(t for t in builtin_targets() if t.adapter == "android")
    recorder = AndroidRecorder(target, adb=fake)
    recorder.connect()
    sink = EventSink(maxsize=64)  # small on purpose: backpressure must not lose gestures
    gc.collect()
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    started = time.perf_counter()
    recorder.start_recording(sink)
    received = 0
    first_at: float | None = None
    deadline = time.monotonic() + 60
    while received < expected and time.monotonic() < deadline:
        event = sink.pop(timeout=0.5)
        if event is None:
            continue
        if first_at is None:
            first_at = time.perf_counter()
        received += 1
        sink.task_done()
    elapsed = time.perf_counter() - started
    recorder.stop_recording()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    snapshot = recorder.diagnostics.snapshot()
    print(f"\nend-to-end: {snapshot.raw_events:,} raw events → {received} gestures in "
          f"{elapsed:.2f}s; first gesture after {(first_at or started) - started:.3f}s; "
          f"peak memory +{(peak - baseline) / 1e6:.1f} MB")
    assert received == expected
    assert sink.dropped == 0
    assert snapshot.raw_events == len(lines)
    assert elapsed < 30
    # The recorder keeps counters and the current gesture only — never the raw stream.
    assert peak - baseline < 60 * 1_000_000
    assert current - baseline < 20 * 1_000_000


def test_large_recording_journal_stays_linear(tmp_path):
    """A long session's journal is one line per *semantic* event."""
    from argus_test_creator.core.events import EventBus
    from argus_test_creator.core.workers import WorkerPool
    from argus_test_creator.project import CreatorProject
    from argus_test_creator.recording import RecordingSession

    lines, expected = synthetic_stream(minutes=1.5, seed=11)
    fake = FakeAdbClient([FakeDevice("BIG", width=1080, height=2400)])
    fake.script_lines("BIG", lines)
    target = next(t for t in builtin_targets() if t.adapter == "android")
    recorder = AndroidRecorder(target, adb=fake)
    project = CreatorProject.create(tmp_path / "proj", name="perf")
    with WorkerPool(max_workers=2) as workers:
        session = RecordingSession(adapter=recorder, directory=project.sessions_dir / "big",
                                   events=EventBus(), workers=workers, settle_ms=0,
                                   capture_after_actions=False, suggest=False)
        session.start()
        deadline = time.monotonic() + 60
        while len(session.actions) < expected and time.monotonic() < deadline:
            time.sleep(0.05)
        actions = session.stop()
    assert len(actions) == expected
    journal = project.sessions_dir / "big" / "events.jsonl"
    assert journal.read_text().count("\n") == expected
