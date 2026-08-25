"""DesktopAdapter against the real display. Skipped without pyautogui or a display."""

from __future__ import annotations

import sys

import pytest

from argus.adapters.desktop import DesktopAdapter
from argus.exceptions import DeviceConnectionError

pytestmark = pytest.mark.integration


@pytest.fixture
def device():
    pytest.importorskip("pyautogui")
    adapter = DesktopAdapter(
        "app", command=sys.executable, args=["-c", "import time; time.sleep(10)"]
    )
    try:
        adapter.connect()
    except DeviceConnectionError as exc:
        pytest.skip(f"no desktop display: {exc}")
    yield adapter
    adapter.disconnect()


def test_screenshot_matches_screen_info(device: DesktopAdapter):
    img = device.screenshot()
    info = device.get_screen_info()
    assert img.size == (info.width, info.height)
    assert info.scale and info.scale >= 1.0
    device.start_application()
    assert device.is_application_running()
