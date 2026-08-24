"""TvosSimAdapter against a booted tvOS Simulator. Skipped unless configured."""

from __future__ import annotations

import os
import shutil

import pytest

from argus.adapters.tvos_sim import TvosSimAdapter
from argus.exceptions import DeviceConnectionError

pytestmark = pytest.mark.integration

BUNDLE_ID = os.environ.get("ARGUS_TVOS_SIM_BUNDLE_ID")


@pytest.fixture
def sim():
    if not BUNDLE_ID:
        pytest.skip("ARGUS_TVOS_SIM_BUNDLE_ID not set")
    if shutil.which("xcrun") is None:
        pytest.skip("xcrun not available")
    device = TvosSimAdapter(
        "sim", bundle_id=BUNDLE_ID, app_path=os.environ.get("ARGUS_TVOS_SIM_APP")
    )
    try:
        device.connect()
    except DeviceConnectionError as exc:
        device.disconnect()
        pytest.skip(f"tvOS simulator unavailable: {exc}")
    yield device
    device.disconnect()


def test_launch_screenshot_and_keys(sim: TvosSimAdapter):
    sim.start_application()
    assert sim.is_application_running()
    img = sim.screenshot()
    assert img.size == sim.get_screen_info().size
    sim.press_key("DPAD_RIGHT")
    sim.press_key("MENU")
    assert sim.health_check().healthy
