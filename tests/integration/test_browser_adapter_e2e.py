"""BrowserAdapter against real Chromium serving a local page. Skipped if unavailable."""

from __future__ import annotations

import pytest
from pytest_httpserver import HTTPServer

from argus.adapters.browser import BrowserAdapter

pytestmark = pytest.mark.integration

playwright = pytest.importorskip("playwright.sync_api")

PAGE = """<!doctype html>
<html><body style="margin:0;background:#ff0000">
<button id="go" style="position:absolute;left:100px;top:100px;width:200px;height:100px;
background:#0000ff;border:0;color:transparent"
onclick="document.body.style.background='#00ff00';console.log('clicked go')">Go</button>
<script>console.log('page ready')</script>
</body></html>"""


@pytest.fixture
def adapter(httpserver: HTTPServer):
    httpserver.expect_request("/").respond_with_data(PAGE, content_type="text/html")
    device = BrowserAdapter("web", url=httpserver.url_for("/"), viewport=(640, 480))
    try:
        device.connect()
    except Exception as exc:  # noqa: BLE001 - browser binaries missing
        pytest.skip(f"Chromium unavailable: {exc}")
    yield device
    device.disconnect()


def test_screenshot_click_and_console(adapter: BrowserAdapter):
    img = adapter.screenshot()
    assert img.size == (640, 480)
    assert img.getpixel((10, 10)) == (255, 0, 0)
    assert img.getpixel((200, 150)) == (0, 0, 255)
    assert "log: page ready" in adapter.get_logs()

    adapter.tap(200, 150)
    img = adapter.screenshot()
    assert img.getpixel((10, 10)) == (0, 255, 0)
    assert adapter.get_logs().splitlines()[-1] == "log: clicked go"

    assert adapter.health_check().healthy
    assert adapter.get_screen_info().size == (640, 480)


def test_reset_reloads_page(adapter: BrowserAdapter):
    adapter.tap(200, 150)
    adapter.reset_application()
    assert adapter.screenshot().getpixel((10, 10)) == (255, 0, 0)
    assert adapter.get_logs() == "log: page ready"
