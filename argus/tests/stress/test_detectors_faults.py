"""Failure detectors, fault injection, evidence collection, context extractors."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from tests.stress.conftest import make_context

from argus.adapters.fake import FakeBackend, FakeDevice
from argus.exceptions import BackendError
from argus.models.common import Region
from argus.ocr.base import OCRResult, OCRWord
from argus.stress.config import BackendMutationsConfig, EvidenceConfig, StressConfig
from argus.stress.detectors import (
    BlankScreenDetector,
    CrashDetector,
    DetectorRegistry,
    ErrorScreenDetector,
    FailureDetector,
    FrozenScreenDetector,
    StaleEntityDetector,
)
from argus.stress.evidence import EvidenceCollector
from argus.stress.extractors import CompositeExtractor, OCRContextExtractor, StateContextExtractor
from argus.stress.faults import BackendFaultInjector, FakeFaultInjector, FaultRegistry
from argus.stress.models import (
    ActionOutcome,
    FailureCategory,
    FailureSeverity,
    Fault,
    Mutation,
    MutationOutcome,
    StressAction,
)
from argus.stress.mutations.data import DataMutationRegistry
from argus.stress.mutations.scheduler import MutationScheduler
from argus.stress.mutations.types import MutationRegistry


class _TextOCR:
    name = "text"

    def __init__(self, text: str) -> None:
        self.text = text

    def is_available(self):
        return True, ""

    def extract_text(self, image):
        words = [OCRWord(text=w, region=Region(x=10 * i, y=5, width=9, height=9))
                 for i, w in enumerate(self.text.split())]
        return OCRResult(text=self.text, words=words)


def _tap() -> StressAction:
    return StressAction(action_type="tap")


def test_action_error_detector_separates_categories(tmp_path):
    context = make_context(tmp_path, persist=False)
    registry = DetectorRegistry()
    infra = registry.run_after_action(context, _tap(), ActionOutcome(
        passed=False, error_kind="infrastructure", message="adb gone"), None, None)
    assert [f.category for f in infra] == [FailureCategory.INFRASTRUCTURE]
    assert context.infrastructure_errors
    unsupported = registry.run_after_action(context, _tap(), ActionOutcome(
        passed=False, error_kind="unsupported", message="no"), None, None)
    assert unsupported[0].severity == FailureSeverity.INFO
    app = registry.run_after_action(context, _tap(), ActionOutcome(
        passed=False, error_kind="application", message="bad"), None, None)
    assert app[0].category == FailureCategory.APPLICATION and app[0].severity == FailureSeverity.ERROR  # noqa: E501


def test_crash_detector_reports_once(tmp_path):
    device = FakeDevice("d")
    device.app_running = False
    context = make_context(tmp_path, device=device, persist=False)
    detector = CrashDetector()
    first = detector.after_action(context, _tap(), ActionOutcome(), None, None)
    assert first and first[0].category == FailureCategory.CRASH
    assert first[0].severity == FailureSeverity.CRITICAL
    assert detector.after_action(context, _tap(), ActionOutcome(), None, None) == []
    device.app_running = True
    detector.after_action(context, _tap(), ActionOutcome(), None, None)
    device.app_running = False
    assert detector.after_action(context, _tap(), ActionOutcome(), None, None)
    # leaving the app on purpose is not a crash
    assert detector.after_action(context, StressAction(action_type="home"), ActionOutcome(),
                                 None, None) == []


def test_blank_and_frozen_screen_detectors(tmp_path):
    blank = Image.new("RGB", (100, 100), (0, 0, 0))
    busy = Image.new("RGB", (100, 100), (0, 0, 0))
    for x in range(100):
        busy.putpixel((x, x), (255, 255, 255))
    device = FakeDevice("d", screenshots=[blank])
    scenario = StressConfig.model_validate({"failures": {"frozen_after_actions": 3}})
    context = make_context(tmp_path, device=device, scenario=scenario, persist=False)
    after = context.observe()
    failures = BlankScreenDetector().after_action(context, _tap(), ActionOutcome(), None, after)
    assert failures and failures[0].category == FailureCategory.VISUAL
    assert BlankScreenDetector().after_action(context, _tap(), ActionOutcome(), None, after) == []  # noqa: E501
    frozen = FrozenScreenDetector()
    device._queue = [busy]  # every further screenshot is the identical busy frame
    prev = context.observe()
    reported = []
    for _ in range(4):
        current = context.observe()
        reported.extend(frozen.after_action(context, _tap(), ActionOutcome(), prev, current))
        prev = current
    assert len(reported) == 1 and reported[0].category == FailureCategory.HANG
    assert "3 consecutive" in reported[0].message


def test_error_screen_detector_uses_ocr(tmp_path):
    device = FakeDevice("d")
    context = make_context(tmp_path, device=device, ocr=_TextOCR("Oops something went wrong"),
                           persist=False)
    after = context.observe()
    failures = ErrorScreenDetector().after_action(context, _tap(), ActionOutcome(), None, after)
    assert failures and failures[0].details["matched"] == ["something went wrong"]
    assert ErrorScreenDetector().after_action(context, _tap(), ActionOutcome(), None, after) == []  # noqa: E501


def test_stale_entity_detector_and_unexpected_success(tmp_path, clock):
    device = FakeDevice("d")
    scenario = StressConfig.model_validate({"backend_mutations": {"reconcile_timeout": "1s"}})
    ocr = _TextOCR("Batman Begins Order confirmed")
    context = make_context(tmp_path, device=device, ocr=ocr, clock=clock, scenario=scenario,
                           persist=False)
    mutation = Mutation(mutation_type="delete", entity_type="products", entity_id="1",
                        metadata={"label": "Batman Begins"}, destructive=True)
    # Baseline: when the delete landed the cart showed the title but no success message.
    context.state["applied_mutations"] = [(context.step, context.elapsed, mutation,
                                           MutationOutcome(applied=True), "batman begins cart")]
    detector = StaleEntityDetector()
    after = context.observe()
    # Within the reconcile grace period nothing is reported.
    assert detector.after_action(context, _tap(), ActionOutcome(), None, after) == []
    clock.advance(2)
    failures = detector.after_action(context, _tap(), ActionOutcome(), None, after)
    assert len(failures) == 1
    assert failures[0].category == FailureCategory.UNEXPECTED_SUCCESS
    assert failures[0].severity == FailureSeverity.CRITICAL
    assert failures[0].mutation == mutation
    # reported once
    assert detector.after_action(context, _tap(), ActionOutcome(), None, after) == []
    # A success message that was already on screen when the mutation landed is not blamed.
    context.state["stale_reported"] = set()
    context.state["applied_mutations"] = [(context.step, context.elapsed - 5, mutation,
                                           MutationOutcome(applied=True),
                                           "batman begins order confirmed")]
    assert detector.after_action(context, _tap(), ActionOutcome(), None, after) == []
    # A label that re-appears (absent at mutation time) is a stale-state warning.
    ocr.text = "Interstellar"
    context.state["stale_reported"] = set()
    other = Mutation(mutation_type="disable", entity_type="products", entity_id="3",
                     metadata={"label": "Interstellar"}, destructive=True)
    context.state["applied_mutations"] = [(context.step, context.elapsed - 5, other,
                                           MutationOutcome(applied=True), "catalog")]
    after = context.observe()
    failures = detector.after_action(context, _tap(), ActionOutcome(), None, after)
    assert failures and failures[0].category == FailureCategory.STALE_STATE
    assert failures[0].severity == FailureSeverity.WARNING
    # After the window closes the mutation is forgotten.
    context._step += 100
    context.state["stale_reported"] = set()
    assert detector.after_action(context, _tap(), ActionOutcome(), None, after) == []


def test_custom_detector_and_disabled_detectors(tmp_path):
    class Always(FailureDetector):
        name = "always"

        def after_action(self, context, action, outcome, before, after):
            return [self.make(context, category=FailureCategory.APPLICATION,
                              severity=FailureSeverity.WARNING, message="always")]

    registry = DetectorRegistry(disabled={"crash", "always"})
    registry.register(Always())
    assert "always" in registry.names()
    assert all(d.name not in ("crash", "always") for d in registry.active())
    context = make_context(tmp_path, persist=False)
    assert registry.run_after_action(context, _tap(), ActionOutcome(), None, None) == []


def test_detector_exceptions_are_infrastructure_not_failures(tmp_path):
    class Broken(FailureDetector):
        name = "broken"

        def after_action(self, *a, **k):
            raise RuntimeError("bug in detector")

    registry = DetectorRegistry(load_builtin=False)
    registry.register(Broken())
    context = make_context(tmp_path, persist=False)
    assert registry.run_after_action(context, _tap(), ActionOutcome(), None, None) == []
    assert any("broken" in e for e in context.infrastructure_errors)


# -- faults ---------------------------------------------------------------------------------------


def test_fake_and_backend_fault_injectors():
    fake = FakeFaultInjector()
    fault = Fault(fault_type="latency", parameters={"seconds": 0.5}, duration=1)
    fake.apply(fault)
    assert fake.active() == [fault]
    fake.clear(fault)
    assert fake.active() == []
    backend = FakeBackend({"x": 1})
    slept: list[float] = []
    injector = BackendFaultInjector(backend, sleep=slept.append)
    assert injector.supports("http_error") and not injector.supports("nuke")
    injector.apply(Fault(fault_type="latency", parameters={"seconds": 0.3}))
    backend.request("GET", "/x")
    assert slept == [0.3]
    injector.clear()
    injector.apply(Fault(fault_type="http_error", parameters={"status": 503}))
    response = backend.request("GET", "/x")
    assert response.status_code == 503 and not response.is_success
    injector.clear()
    injector.apply(Fault(fault_type="timeout"))
    try:
        backend.request("GET", "/x")
    except BackendError as exc:
        assert "timed out" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a timeout")
    injector.clear()
    injector.apply(Fault(fault_type="malformed_response"))
    with pytest.raises(ValueError):
        backend.request("GET", "/x").json()
    injector.clear()
    assert backend.request("GET", "/x").status_code == 200  # original restored
    registry = FaultRegistry()
    assert isinstance(registry.create("fake", None), FakeFaultInjector)
    assert registry.create("backend", None) is None
    assert "backend" in registry.names()


# -- evidence -------------------------------------------------------------------------------------


def test_evidence_collector_writes_failure_directory(tmp_path):
    device = FakeDevice("d")
    backend = FakeBackend({"k": "v"})
    context = make_context(tmp_path, device=device, backend=backend, ocr=_TextOCR("hello"))
    context.observe()
    context.record_action(_tap(), ActionOutcome())
    context.observe()
    context.ocr_for(context.last_observation)
    collector = EvidenceCollector(EvidenceConfig(), Path(context.artifacts.directory))
    detector = CrashDetector()
    failure = detector.make(context, category=FailureCategory.CRASH,
                            severity=FailureSeverity.CRITICAL, message="boom", action=_tap())
    saved = collector.collect(context, failure)
    directory = Path(saved.evidence["dir"])
    for name in ("before.png", "after.png", "history.json", "logs.txt", "backend_state.json",
                 "failure.json", "ocr.txt"):
        assert (directory / name).is_file(), name
    history = (directory / "history.json").read_text()
    assert '"seed": 1' in history and '"recent_actions"' in history
    limited = EvidenceCollector(EvidenceConfig(max_failures_with_evidence=1),
                                Path(context.artifacts.directory))
    limited.collect(context, failure)
    assert limited.collect(context, failure).evidence == {}  # cap reached → unchanged


# -- extractors ---------------------------------------------------------------------------------


def test_state_and_ocr_extractors(tmp_path, fake_mutation_backend):
    backend = FakeBackend({"current_product": 2})
    device = FakeDevice("d")
    context = make_context(tmp_path, device=device, backend=backend,
                           mutation_backend=fake_mutation_backend,
                           ocr=_TextOCR("Now showing: Batman Begins"), persist=False)
    scheduler = MutationScheduler(BackendMutationsConfig(enabled=True), fake_mutation_backend,
                                  MutationRegistry(), DataMutationRegistry(),
                                  enabled_strategies=set())
    context.observe()
    composite = CompositeExtractor([StateContextExtractor(scheduler),
                                    OCRContextExtractor(scheduler)], load_entry_points=False)
    refs = composite.update(context)
    by_source = {r.source: r for r in refs}
    assert by_source["state"].entity_id == "2" and by_source["state"].label == "The Matrix"
    assert by_source["ocr"].entity_id == "1" and by_source["ocr"].label == "Batman Begins"
    assert "region" in by_source["ocr"].data
    assert context.entity_context == refs
