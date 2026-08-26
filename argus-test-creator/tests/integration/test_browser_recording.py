"""Browser recording against the Creator's web demo, then an Argus run (real target).

Skipped when Playwright/Chromium or Argus are unavailable.
"""

from __future__ import annotations

import socket
import time

import pytest

from argus_test_creator.app import CreatorApp
from argus_test_creator.demo.web_server import serve
from argus_test_creator.models import Rect
from argus_test_creator.observation import TesseractOCRProvider

pytestmark = pytest.mark.integration

playwright = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def demo_server():
    port = _free_port()
    server = serve(port)
    yield f"http://127.0.0.1:{port}/"
    server.shutdown()


@pytest.fixture
def chromium_available():
    try:
        with playwright.sync_playwright() as p:
            path = p.chromium.executable_path
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"playwright not usable: {exc}")
    import os

    if not os.path.exists(path):
        pytest.skip("chromium is not installed (playwright install chromium)")


def test_browser_record_and_run_with_argus(tmp_path, demo_server, chromium_available,
                                           argus_executable):
    ocr = TesseractOCRProvider()
    available, reason = ocr.is_available()
    app = CreatorApp(ocr=ocr if available else None)
    app.config.argus.executable = argus_executable
    app.config.recording.settle_ms = 100
    try:
        project = app.create_project(tmp_path / "web")
        target = app.select_target("browser-chromium", {"url": demo_server, "headless": True})
        assert target.argus_device_options["url"] == demo_server
        app.new_test(test_id="WEB-1", name="Search finds Batman Begins in the browser",
                     feature="Movies")
        recorder = app.connect_target()
        app.start_recording()

        def interact(page) -> None:
            page.mouse.click(120, 130)          # Movies tile
            page.wait_for_timeout(200)
            page.mouse.click(120, 130)          # Search tile
            page.wait_for_timeout(200)
            page.mouse.click(300, 130)          # focus the input
            page.keyboard.type("Batman", delay=30)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1200)         # loading state → results

        recorder.run_in_page(interact)
        app.session.flush()
        steps = app.stop_recording()
        actions = [s.action for s in steps]
        assert actions[:3] == ["device.tap"] * 3
        assert actions.count("device.key") == 7  # B a t m a n + ENTER
        assert steps[3].name == "Type 'Batman'"
        assert steps[0].params["x"] == 120 and steps[0].params["y"] == 130
        assert all(s.provenance.source == "recording" for s in steps)
        assert any("element" in a.metadata for a in app.session.actions)  # DOM evidence kept

        capture = app.capture_screen().result(30)
        image = app.load_capture(capture)
        # First result row (see demo/web/index.html layout): crop it as the reference image.
        row = Rect(x=40, y=120, width=640, height=70)
        app.add_image_verification(capture, row, label="batman row", threshold=0.85)
        if available:
            obs = app.run_ocr(capture).result(60)
            assert obs is not None
            if any("Batman" in line for line in obs.lines()):
                app.add_text_verification("Batman Begins", capture=capture, wait=False)
        assert app.validate() == []
        path = app.save_test()
        assert path.read_text().count("action: device.tap") == 3
        config = project.read_argus_config()
        assert config["devices"]["web"]["type"] == "browser"
        assert config["devices"]["web"]["headless"] is True

        start = time.time()
        result = app.run_with_argus().result(600)
        assert result.status == "passed", result.output[-2000:]
        assert result.report is not None and result.html_report is not None
        assert time.time() - start < 600
        image.close()
    finally:
        app.shutdown()
