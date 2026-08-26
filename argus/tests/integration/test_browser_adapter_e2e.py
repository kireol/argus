"""BrowserAdapter against real Chromium serving a local page. Skipped if unavailable."""

from __future__ import annotations

import time

import pytest
from pytest_httpserver import HTTPServer

from argus.adapters.browser import BrowserAdapter
from argus.exceptions import DeviceConnectionError

pytestmark = pytest.mark.integration

playwright = pytest.importorskip("playwright.sync_api")

PAGE = """<!doctype html>
<html><body style="margin:0;background:#ff0000">
<button id="go" style="position:absolute;left:100px;top:100px;width:200px;height:100px;
background:#0000ff;border:0;color:transparent"
onclick="document.body.style.background='#00ff00';console.log('clicked go')">Go</button>
<script>console.log('page ready')</script>
</body></html>"""


def _wait_for_log(adapter: BrowserAdapter, needle: str, timeout: float = 2.0) -> None:
    """Poll get_logs() until `needle` appears.

    Console messages arrive asynchronously over CDP, so a screenshot or tap can return
    before the corresponding console event has been delivered to the page's "console"
    handler; polling avoids a race against that delivery.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in adapter.get_logs():
            return
        time.sleep(0.05)


@pytest.fixture
def adapter(httpserver: HTTPServer):
    httpserver.expect_request("/").respond_with_data(PAGE, content_type="text/html")
    device = BrowserAdapter("web", url=httpserver.url_for("/"), viewport=(640, 480))
    try:
        device.connect()
    except DeviceConnectionError as exc:
        # Raised by BrowserAdapter._open_page for environment problems only (playwright not
        # installed, or the browser binary can't be launched/opened) - any other failure
        # should fail the test rather than be swallowed here.
        device.disconnect()
        pytest.skip(f"Chromium unavailable: {exc}")
    yield device
    device.disconnect()


def test_screenshot_click_and_console(adapter: BrowserAdapter):
    img = adapter.screenshot()
    assert img.size == (640, 480)
    assert img.getpixel((10, 10)) == (255, 0, 0)
    assert img.getpixel((200, 150)) == (0, 0, 255)
    _wait_for_log(adapter, "log: page ready")
    assert "log: page ready" in adapter.get_logs()

    adapter.tap(200, 150)
    img = adapter.screenshot()
    assert img.getpixel((10, 10)) == (0, 255, 0)
    _wait_for_log(adapter, "log: clicked go")
    assert adapter.get_logs().splitlines()[-1] == "log: clicked go"

    assert adapter.health_check().healthy
    assert adapter.get_screen_info().size == (640, 480)


def test_reset_reloads_page(adapter: BrowserAdapter):
    adapter.tap(200, 150)
    adapter.reset_application()
    assert adapter.screenshot().getpixel((10, 10)) == (255, 0, 0)
    _wait_for_log(adapter, "log: page ready")
    assert adapter.get_logs() == "log: page ready"
