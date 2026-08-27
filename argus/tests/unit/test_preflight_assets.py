"""TestAssetsCheck only requires images for tests that will actually run."""

from __future__ import annotations

from types import SimpleNamespace

from argus.config.models import DeviceConfig
from argus.models.test_definition import TestDefinition
from argus.preflight.checks import TestAssetsCheck


def _test(test_id: str, platforms: list[str], image: str) -> TestDefinition:
    return TestDefinition.model_validate(
        {
            "id": test_id,
            "name": test_id,
            "feature": "Demo",
            "platforms": platforms,
            "steps": [
                {
                    "action": "verify",
                    "condition": {"type": "image_present", "image": image},
                }
            ],
        }
    )


def _session(*, platforms: dict[str, str], present: set[str]):
    devices = {
        name: DeviceConfig.model_validate({"type": kind, "platform": kind, "command": "x"})
        if kind == "desktop"
        else DeviceConfig.model_validate({"type": kind, "platform": kind})
        for name, kind in platforms.items()
    }

    class _Config:
        def devices_for_platform(self, platform: str) -> dict[str, DeviceConfig]:
            return {
                n: d
                for n, d in devices.items()
                if d.effective_platform == platform and d.configured
            }

    return SimpleNamespace(
        config=_Config(),
        devices_for_platform=lambda p: sorted(_Config().devices_for_platform(p)),
        assets=SimpleNamespace(exists=lambda name: name in present),
        asset_paths=["/tmp/assets"],
    )


def test_skips_android_only_images_when_only_cpp_is_configured():
    session = _session(platforms={"fallback": "cpp"}, present=set())
    check = TestAssetsCheck(
        session,
        [
            _test("DEMO-001", ["android"], "speedometer_55.png"),
            _test("TS-001", ["android", "cpp"], "left_turn_signal_on.png"),
        ],
    )
    result = check.run()
    assert result.passed is False
    assert "left_turn_signal_on.png" in (result.error or "")
    assert "speedometer_55.png" not in (result.error or "")
    assert result.diagnostics["checked_tests"] == 1


def test_passes_when_runnable_assets_exist():
    session = _session(
        platforms={"fallback": "cpp"}, present={"left_turn_signal_on.png"}
    )
    check = TestAssetsCheck(
        session,
        [
            _test("DEMO-001", ["android"], "speedometer_55.png"),
            _test("TS-001", ["android", "cpp"], "left_turn_signal_on.png"),
        ],
    )
    result = check.run()
    assert result.passed is True
    assert result.diagnostics["checked_tests"] == 1
