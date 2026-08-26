from __future__ import annotations

import time
import tracemalloc
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from argus_test_creator.authoring import AuthoringService
from argus_test_creator.core.events import EventBus
from argus_test_creator.models import (
    ConditionDraft,
    Point,
    RecordingEvent,
    RecordingEventType,
    RecordingMode,
    StepDraft,
)
from argus_test_creator.observation import CaptureStore, compare_images
from argus_test_creator.recording import EventNormalizer, actions_to_steps
from argus_test_creator.serialization import document_to_yaml
from argus_test_creator.validation import validate_document

pytestmark = pytest.mark.performance
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _events(n: int) -> list[RecordingEvent]:
    out = []
    for i in range(n):
        kind = [RecordingEventType.POINTER_DOWN, RecordingEventType.POINTER_MOVE,
                RecordingEventType.POINTER_MOVE, RecordingEventType.POINTER_UP][i % 4]
        out.append(RecordingEvent(event_type=kind, sequence=i,
                                  timestamp=T0 + timedelta(milliseconds=100 * i),
                                  position=Point(x=i % 500, y=(i * 7) % 500), button="left"))
    return out


def test_normalize_10k_lightweight_events_fast():
    events = _events(10_000)
    start = time.perf_counter()
    actions = EventNormalizer().normalize(events, RecordingMode.SMART)
    elapsed = time.perf_counter() - start
    assert len(actions) == 2_500
    assert elapsed < 3.0, elapsed


def test_1000_recorded_events_to_yaml_and_validation():
    actions = EventNormalizer().normalize(_events(1_000), RecordingMode.SMART)
    steps, _ = actions_to_steps(actions, session_id="s")
    svc = AuthoringService(EventBus())
    svc.set_metadata(id="P-1", name="Performance test", feature="Perf")
    start = time.perf_counter()
    svc.add_steps(steps)
    text = document_to_yaml(svc.document)
    issues = validate_document(svc.document)
    elapsed = time.perf_counter() - start
    assert len(svc.document.steps) == 250 and text.count("action:") == 250
    assert issues == [] and elapsed < 2.0, elapsed


def test_repeated_undo_redo_is_cheap():
    svc = AuthoringService(EventBus())
    for i in range(2_000):
        svc.add_step("device.tap", {"x": i, "y": i})
    start = time.perf_counter()
    for _ in range(3):
        while svc.undo():
            pass
        while svc.redo():
            pass
    elapsed = time.perf_counter() - start
    assert len(svc.document.steps) == 2_000 and elapsed < 3.0, elapsed


def test_hundreds_of_screenshots_do_not_stay_in_memory(tmp_path):
    store = CaptureStore(tmp_path, thumbnail_cache=8)
    tracemalloc.start()
    frame = Image.new("RGB", (1280, 720), (10, 20, 30))
    for i in range(200):
        frame.putpixel((i, i), (255, 255, 255))
        store.save(frame)
    for capture in store.all()[:50]:
        store.thumbnail(capture)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(store) == 200
    # 200 raw 1280x720 RGB frames would be ~550 MB; we must stay far below that.
    assert peak < 120 * 1024 * 1024, peak


def test_large_screenshot_diff_is_fast():
    a = Image.new("RGB", (3840, 2160), (0, 0, 0))
    b = a.copy()
    b.paste((255, 255, 255), (100, 100, 400, 400))
    start = time.perf_counter()
    diff = compare_images(a, b)
    elapsed = time.perf_counter() - start
    assert diff.changed_region is not None and elapsed < 1.5, elapsed


def test_validation_scales_with_conditions():
    svc = AuthoringService(EventBus())
    svc.set_metadata(id="P-2", name="Many verifications", feature="Perf")
    steps = [StepDraft(action="verify", condition=ConditionDraft(
        type="text_present", params={"text": f"t{i}"})) for i in range(3_000)]
    svc.add_steps(steps)
    start = time.perf_counter()
    validate_document(svc.document)
    assert time.perf_counter() - start < 2.0
