from __future__ import annotations

from argus_test_creator.argus_schema import ACTIONS, CONDITIONS
from argus_test_creator.argus_schema.actions import actions_for
from argus_test_creator.argus_schema.conditions import conditions_for
from argus_test_creator.targets import PLATFORM_CAPABILITIES, TargetCatalog, builtin_targets

EXPECTED_ACTIONS = {
    "backend.set", "backend.get", "backend.post", "backend.put", "backend.patch",
    "backend.delete", "device.start", "device.stop", "device.restart", "device.reset",
    "device.tap", "device.swipe", "device.long_press", "device.drag", "device.pinch",
    "device.multi_touch", "device.key", "wait_until", "verify", "wait", "screenshot", "log",
    "shell.run",
}
EXPECTED_CONDITIONS = {
    "image_present", "image_not_present", "screenshot_matches", "text_present",
    "text_not_present", "pixel_matches", "instrumentation_value", "application_state",
    "backend_value", "log_contains", "now_playing",
}


def test_catalog_matches_argus_1_1():
    assert set(ACTIONS) == EXPECTED_ACTIONS
    assert set(CONDITIONS) == EXPECTED_CONDITIONS
    assert ACTIONS["device.swipe"].required_params == ("from_x", "from_y", "to_x", "to_y")
    assert ACTIONS["shell.run"].dangerous
    assert CONDITIONS["log_contains"].one_of == (("text", "pattern"),)


def test_capability_filtering():
    roku = PLATFORM_CAPABILITIES["roku"]
    names = {a.name for a in actions_for(roku)}
    assert "device.key" in names and "device.tap" not in names and "device.pinch" not in names
    conds = {c.name for c in conditions_for(PLATFORM_CAPABILITIES["appletv"])}
    assert "now_playing" in conds and "image_present" not in conds
    web = {c.name for c in conditions_for(PLATFORM_CAPABILITIES["web"])}
    assert "text_present" in web and "now_playing" not in web


def test_builtin_targets_and_catalog():
    catalog = TargetCatalog()
    ids = [t.id for t in catalog.all()]
    assert "fake-movies" in ids and "browser-chromium" in ids
    assert catalog.get("fake-movies").argus_device_type == "fake"
    assert catalog.for_platform("web")[0].adapter == "browser"
    assert all(t.capabilities.limitations or t.adapter == "fake" for t in builtin_targets())
    assert PLATFORM_CAPABILITIES["ios"].supports_input_recording is False
