"""Action registry, capability probe, targets and the weighted generator."""

from __future__ import annotations

from typing import Any

from tests.stress.conftest import make_context

from argus.adapters.fake import FakeDevice
from argus.models.common import Region
from argus.ocr.base import OCRResult, OCRWord
from argus.stress.actions.base import StressActionRegistry, StressActionType
from argus.stress.actions.builtin import BUILTIN_ACTIONS
from argus.stress.capabilities import DeviceProbe
from argus.stress.config import MonkeyConfig, StressConfig, TargetRegion, TargetsConfig
from argus.stress.generator import ActionGenerator
from argus.stress.models import StressAction, TargetKind
from argus.stress.targets import TargetProvider, TargetSelector


class _FixedOCR:
    name = "fixed"

    def __init__(self, words: list[tuple[str, int, int]]) -> None:
        self._words = words

    def is_available(self):
        return True, ""

    def extract_text(self, image):
        return OCRResult(text=" ".join(w for w, _x, _y in self._words), words=[
            OCRWord(text=w, confidence=0.9, region=Region(x=x, y=y, width=60, height=20))
            for w, x, y in self._words
        ])


def test_registry_has_builtin_actions_and_rejects_unknown():
    registry = StressActionRegistry()
    names = registry.names()
    for expected in ("tap", "double_tap", "long_press", "swipe", "scroll", "back", "home",
                     "type_text", "clear_text", "reload", "restart", "background", "foreground",
                     "rotate", "wait"):
        assert expected in names
    assert len(BUILTIN_ACTIONS) == len([n for n in names])
    try:
        registry.get("teleport")
    except Exception as exc:  # noqa: BLE001
        assert "Unknown stress action" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected an error")


def test_custom_action_registers_without_touching_core():
    class Shake(StressActionType):
        name = "shake"
        requires = ("tap",)

        def generate(self, context, targets, params):
            return StressAction(action_type="shake")

        def perform(self, context, action):
            context.state["shaken"] = True

    registry = StressActionRegistry()
    registry.register(Shake())
    assert "shake" in registry.names()


def test_device_probe_flags_and_optional_methods():
    device = FakeDevice("d")
    probe = DeviceProbe(device)
    assert probe.has("tap") and probe.has("keyboard") and probe.has("app_lifecycle")
    assert not probe.has("rotate")
    device.rotate = lambda orientation: None  # type: ignore[attr-defined]
    assert DeviceProbe(device).has("rotate")
    assert DeviceProbe(None).has("tap") is False
    assert "rotate" in probe.summary() and probe.summary()["screenshot"] is True


def test_generator_skips_unsupported_and_disabled_actions(tmp_path):
    class NoKeys(FakeDevice):
        @property
        def capabilities(self):
            caps = super().capabilities
            return caps.__class__(**{**vars(caps), "supports_keyboard": False})

    device = NoKeys("d")
    config = MonkeyConfig.model_validate({"actions": {"tap": 10, "back": 5, "swipe": False,
                                                      "rotate": 3, "nope": 1}})
    registry = StressActionRegistry()
    generator = ActionGenerator(config, registry, DeviceProbe(device),
                                TargetSelector(config.targets))
    assert generator.available == ["tap"]
    assert generator.skipped["back"].startswith("device lacks keyboard")
    assert generator.skipped["swipe"] == "disabled"
    assert generator.skipped["rotate"].startswith("device lacks rotate")
    assert "unknown" in generator.skipped["nope"]
    context = make_context(tmp_path, device=device, persist=False)
    action = generator.generate(context)
    assert action is not None and action.action_type == "tap"


def test_generator_is_deterministic_for_a_seed(tmp_path):
    device = FakeDevice("d")
    config = MonkeyConfig()
    registry = StressActionRegistry()

    def sequence(seed: int) -> list[str]:
        gen = ActionGenerator(config, registry, DeviceProbe(device), TargetSelector(config.targets))
        context = make_context(tmp_path, seed=seed, device=device, persist=False)
        out = []
        for _ in range(40):
            action = gen.generate(context)
            assert action is not None
            out.append(action.describe() + f"|{gen.next_delay(context):.4f}")
        return out

    assert sequence(99) == sequence(99)
    assert sequence(99) != sequence(100)


def test_targets_prefer_known_regions_and_ocr_words(tmp_path):
    device = FakeDevice("d", screen_size=(400, 300))
    targets_cfg = TargetsConfig(prefer_known=1.0, regions=[
        TargetRegion(name="Save", x=10, y=10, width=100, height=40, weight=5,
                     actions=["tap"]),
    ], avoid_words=["Sign out"])
    ocr = _FixedOCR([("Movies", 50, 100), ("Sign", 200, 100), ("out", 260, 100), ("ab", 1, 1)])
    context = make_context(tmp_path, device=device, ocr=ocr, persist=False)
    context.observe()
    selector = TargetSelector(targets_cfg)
    known = selector.known_targets(context, "tap")
    labels = {t.label for t in known}
    assert "Save" in labels and "Movies" in labels
    assert "Sign" not in labels and "out" not in labels  # avoid phrase tokens
    assert "ab" in labels  # min_word_length is 2
    assert all(t.kind in (TargetKind.CONFIGURED, TargetKind.TEXT) for t in known)
    # A region restricted to "tap" is not offered for swipe.
    assert "Save" not in {t.label for t in selector.known_targets(context, "swipe")}
    picked = selector.pick(context, "tap")
    assert picked.kind != TargetKind.COORDINATE
    # Coordinate fallback stays inside the screen with the edge margin.
    fallback = TargetSelector(TargetsConfig(prefer_known=0.0, edge_margin=8))
    for _ in range(50):
        p = fallback.pick(context, "tap")
        assert 8 <= p.x <= 391 and 8 <= p.y <= 291 and p.kind == TargetKind.COORDINATE


def test_ocr_targets_are_cached_between_refreshes(tmp_path):
    device = FakeDevice("d")
    ocr = _FixedOCR([("Alpha", 10, 10)])
    context = make_context(tmp_path, device=device, ocr=ocr, persist=False)
    selector = TargetSelector(TargetsConfig(ocr_refresh_every=5))
    context.observe()
    selector.known_targets(context)
    calls_after_first = context.last_observation.ocr_attempted
    assert calls_after_first
    for _ in range(3):
        context.record_action(StressAction(action_type="wait"), __import__(
            "argus.stress.models", fromlist=["ActionOutcome"]).ActionOutcome())
        context.observe()
        selector.known_targets(context)
    # Only one OCR call happened for the intermediate observations (cache hit).
    attempted = sum(1 for r in context.observations if r.ocr_attempted)
    assert attempted <= 2


def test_custom_target_provider_comes_first(tmp_path):
    class Fixed(TargetProvider):
        name = "fixed"

        def targets(self, context):
            from argus.stress.models import Target

            return [Target(x=5, y=5, label="fixed", kind=TargetKind.CONFIGURED,
                           metadata={"weight": 100})]

    device = FakeDevice("d")
    context = make_context(tmp_path, device=device, persist=False)
    selector = TargetSelector(TargetsConfig(prefer_known=1.0))
    selector.add_provider(Fixed(), first=True)
    assert selector.providers[0].name == "fixed"
    assert selector.pick(context, "tap").label == "fixed"


def test_builtin_actions_execute_against_fake_device(tmp_path):
    device = FakeDevice("d", screen_size=(500, 500))
    device.rotate = lambda orientation: device.keys.append(f"ROTATE_{orientation}")  # type: ignore[attr-defined]
    device.start_application()
    scenario = StressConfig()
    context = make_context(tmp_path, device=device, scenario=scenario, persist=False)
    registry = StressActionRegistry()
    selector = TargetSelector(scenario.monkey.targets)
    executed: dict[str, Any] = {}
    for action_type in registry.all():
        action = action_type.generate(context, selector, {})
        assert action is not None, action_type.name
        outcome = action_type.execute(context, action)
        executed[action_type.name] = outcome
        assert outcome.passed, (action_type.name, outcome.message)
    assert device.taps and device.swipes and device.long_presses
    assert "BACK" in device.keys and "HOME" in device.keys
    assert any(k.startswith("ROTATE_") for k in device.keys)
    assert executed["wait"].passed and context.clock.monotonic() > 1000.0


def test_action_execute_classifies_errors(tmp_path):
    from argus.exceptions import DeviceCapabilityError, DeviceConnectionError

    class Broken(FakeDevice):
        def tap(self, x, y):
            raise DeviceConnectionError("adb gone")

        def swipe(self, *a, **k):
            raise DeviceCapabilityError("no swipe")

        def press_key(self, key):
            raise RuntimeError("boom")

    device = Broken("d")
    context = make_context(tmp_path, device=device, persist=False)
    registry = StressActionRegistry()
    selector = TargetSelector(TargetsConfig())
    tap = registry.get("tap")
    out = tap.execute(context, tap.generate(context, selector, {}))
    assert not out.passed and out.error_kind == "infrastructure"
    swipe = registry.get("swipe")
    out = swipe.execute(context, swipe.generate(context, selector, {}))
    assert out.error_kind == "unsupported"
    back = registry.get("back")
    out = back.execute(context, back.generate(context, selector, {}))
    assert out.error_kind == "infrastructure" and "boom" in out.message
