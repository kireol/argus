"""IosAdapter against a running WebDriverAgent. Skipped unless configured."""

from __future__ import annotations

import os

import pytest

from argus.adapters.ios import IosAdapter
from argus.exceptions import DeviceConnectionError

pytestmark = pytest.mark.integration

WDA_URL = os.environ.get("ARGUS_WDA_URL")
BUNDLE_ID = os.environ.get("ARGUS_IOS_BUNDLE_ID")


@pytest.fixture
def device():
    if not WDA_URL or not BUNDLE_ID:
        pytest.skip("ARGUS_WDA_URL / ARGUS_IOS_BUNDLE_ID not set")
    adapter = IosAdapter("iphone", bundle_id=BUNDLE_ID, url=WDA_URL, timeout=15)
    try:
        adapter.connect()
    except DeviceConnectionError as exc:
        pytest.skip(f"WebDriverAgent unavailable: {exc}")
    yield adapter
    adapter.disconnect()


def test_launch_screenshot_and_tap(device: IosAdapter):
    device.start_application()
    assert device.is_application_running()
    img = device.screenshot()
    info = device.get_screen_info()
    assert img.size == (info.width, info.height)
    device.tap(info.width // 2, info.height // 2)
    device.pinch(info.width // 2, info.height // 2, 100, 300)
