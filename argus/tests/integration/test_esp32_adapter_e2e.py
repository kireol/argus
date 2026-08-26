"""Esp32Adapter against the SSD1306 example in Wokwi (or a real board). Skips when unavailable."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from argus.adapters.esp32 import Esp32Adapter

pytestmark = pytest.mark.integration

EXAMPLE = Path(__file__).resolve().parents[2] / "agents/esp32/examples/ssd1306_menu"
PORT = os.environ.get("ARGUS_ESP32_PORT")


def _wait_for_log(device: Esp32Adapter, needle: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in device.get_logs():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def wokwi_board():
    if not os.environ.get("WOKWI_CLI_TOKEN"):
        pytest.skip("WOKWI_CLI_TOKEN not set")
    if shutil.which("wokwi-cli") is None:
        pytest.skip("wokwi-cli not on PATH")
    if not (EXAMPLE / "firmware.bin").is_file():
        pytest.skip(
            "example firmware.bin not built (see agents/esp32/examples/ssd1306_menu/BUILD.md)"
        )
    device = Esp32Adapter("sim", transport="wokwi", project_dir=EXAMPLE, boot_timeout=60.0)
    device.connect()
    yield device
    device.disconnect()


@pytest.fixture
def real_board():
    if not PORT:
        pytest.skip("ARGUS_ESP32_PORT not set")
    device = Esp32Adapter("board", transport="serial", port=PORT, boot_timeout=20.0)
    device.connect()
    name = device.health_check().details.get("name")
    if name != "ssd1306_menu":
        device.disconnect()
        pytest.skip(
            f"board at {PORT} is not running the ssd1306_menu firmware (got {name!r})"
        )
    yield device
    device.disconnect()


def _exercise(device: Esp32Adapter) -> None:
    assert device.health_check().healthy
    assert device.get_screen_info().size == (128, 64)
    assert _wait_for_log(device, "menu: selected=Play")
    img = device.screenshot()
    assert img.size == (128, 64)
    assert img.getpixel((123, 59)) == (255, 255, 255)  # marker block
    # ssd1306_menu layout (see agents/esp32/examples/ssd1306_menu/src/main.cpp): title text
    # spans rows 0-7, menu row 0 spans rows 20-27, menu row 1 spans rows 34-41, and the
    # marker block occupies rows 56-63/cols 120-127. Rows 8-19 and 42-55 are blank at every
    # column, so x=64 in either gap is a safe "definitely off" probe regardless of glyph ink.
    assert img.getpixel((64, 12)) == (0, 0, 0)  # gap between title and first menu row
    assert img.getpixel((64, 48)) == (0, 0, 0)  # gap between second menu row and marker block
    device.press_key("BTN_DOWN")
    assert _wait_for_log(device, "menu: selected=Settings")
    client = device.instrumentation_client()
    assert client is not None and client.state()["selected"] == 1
    device.reset_application()
    assert _wait_for_log(device, "menu: selected=Play")


def test_wokwi_example(wokwi_board: Esp32Adapter):
    _exercise(wokwi_board)


def test_real_board(real_board: Esp32Adapter):
    _exercise(real_board)
