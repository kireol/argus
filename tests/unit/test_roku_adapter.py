"""RokuAdapter unit tests against pytest-httpserver (ECP + dev installer) and a fake console."""

from __future__ import annotations

import io
import socket
import threading
import time
from collections import deque

import pytest
from PIL import Image
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

from argus.adapters.registry import DeviceRegistry
from argus.adapters.roku import RokuAdapter, _DebugConsoleReader
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError
from argus.logging import get_logger

DEVICE_INFO = """<?xml version="1.0" encoding="UTF-8" ?>
<device-info>
  <model-name>Roku Ultra</model-name>
  <software-version>13.0.0</software-version>
  <ui-resolution>1080p</ui-resolution>
  <developer-enabled>true</developer-enabled>
</device-info>"""

ACTIVE_DEV = '<active-app><app id="dev" type="appl" version="1.0.0">My Channel</app></active-app>'
ACTIVE_HOME = "<active-app><app>Roku</app></active-app>"


def _png(size: tuple[int, int] = (64, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (9, 8, 7)).save(buf, format="PNG")
    return buf.getvalue()


def _digest_protected(body: bytes, content_type: str):
    """Handler that demands Digest auth once, then serves `body`."""

    def handler(request: Request) -> Response:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Digest "):
            return Response(
                "auth required",
                status=401,
                headers={"WWW-Authenticate": 'Digest realm="rokudev", nonce="abc123", qop="auth"'},
            )
        assert 'username="rokudev"' in auth
        return Response(body, status=200, content_type=content_type)

    return handler


class _FakeDebugConsole:
    """Minimal TCP server standing in for the BrightScript console on port 8085."""

    def __init__(self, lines: list[str]) -> None:
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        self._lines = lines
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._server.accept()
        except OSError:
            return
        with conn:
            for line in self._lines:
                conn.sendall((line + "\r\n").encode())
            time.sleep(0.3)

    def close(self) -> None:
        self._server.close()


@pytest.fixture
def roku(httpserver: HTTPServer) -> RokuAdapter:
    httpserver.expect_request("/query/device-info").respond_with_data(
        DEVICE_INFO, content_type="text/xml"
    )
    return RokuAdapter(
        "tv",
        host=httpserver.host,
        dev_password="secret",
        ecp_port=httpserver.port,
        installer_port=httpserver.port,
        debug_port=1,  # nothing listens; the reader just retries quietly
    )


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestIdentity:
    def test_capabilities_with_dev_password(self, roku):
        caps = roku.capabilities
        assert caps.supports_screenshot and caps.supports_keyboard
        assert caps.supports_app_lifecycle and caps.supports_logs
        assert not caps.supports_tap and not caps.supports_swipe
        assert roku.platform == "roku"

    def test_capabilities_without_dev_password(self):
        adapter = RokuAdapter("tv", host="10.0.0.5")
        assert not adapter.capabilities.supports_screenshot
        with pytest.raises(DeviceCapabilityError, match="screenshot"):
            adapter.screenshot()

    def test_tap_and_swipe_unsupported(self, roku):
        with pytest.raises(DeviceCapabilityError):
            roku.tap(1, 2)
        with pytest.raises(DeviceCapabilityError):
            roku.swipe(0, 0, 1, 1)


class TestConnection:
    def test_connect_and_health(self, roku):
        roku.connect()
        health = roku.health_check()
        assert health.healthy
        assert health.details["model"] == "Roku Ultra"
        assert roku.get_screen_info().size == (1920, 1080)
        roku.disconnect()

    def test_unreachable_host(self):
        adapter = RokuAdapter("tv", host="127.0.0.1", ecp_port=1, timeout=0.5)
        with pytest.raises(DeviceConnectionError, match="device-info"):
            adapter.connect()
        assert not adapter.health_check().healthy

    def test_operations_before_connect_raise(self, roku):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            roku.press_key("HOME")


class TestLifecycle:
    def test_start_stop_running(self, roku, httpserver: HTTPServer):
        # connect() first: it issues its own GET /query/device-info, and pytest-httpserver's
        # *ordered* handlers demand that the very next request (any path) match them, which
        # would collide with the interleaved launch/dev and keypress/Home calls below. Oneshot
        # handlers give the same "first call gets X, second gets Y" behavior without that
        # global ordering constraint.
        roku.connect()
        httpserver.expect_request("/launch/dev", method="POST").respond_with_data("")
        httpserver.expect_request("/keypress/Home", method="POST").respond_with_data("")
        httpserver.expect_oneshot_request("/query/active-app").respond_with_data(
            ACTIVE_DEV, content_type="text/xml"
        )
        httpserver.expect_oneshot_request("/query/active-app").respond_with_data(
            ACTIVE_HOME, content_type="text/xml"
        )
        roku.start_application()
        assert roku.is_application_running()
        roku.stop_application()
        assert not roku.is_application_running()
        assert any(r.path == "/launch/dev" for r, _ in httpserver.log)
        assert any(r.path == "/keypress/Home" for r, _ in httpserver.log)

    def test_sideload_on_connect(self, httpserver: HTTPServer, tmp_path):
        zip_path = tmp_path / "channel.zip"
        zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        httpserver.expect_request("/query/device-info").respond_with_data(
            DEVICE_INFO, content_type="text/xml"
        )
        httpserver.expect_request("/plugin_install", method="POST").respond_with_handler(
            _digest_protected(b"<html>Install Success.</html>", "text/html")
        )
        adapter = RokuAdapter(
            "tv",
            host=httpserver.host,
            dev_password="secret",
            channel_zip=zip_path,
            ecp_port=httpserver.port,
            installer_port=httpserver.port,
            debug_port=1,
        )
        adapter.connect()
        installs = [r for r, _ in httpserver.log if r.path == "/plugin_install"]
        assert installs, "expected a POST to /plugin_install"
        body = installs[-1].get_data()
        assert b'name="mysubmit"' in body and b"Install" in body
        assert b'filename="channel.zip"' in body
        adapter.disconnect()

    def test_sideload_requires_dev_password(self, tmp_path):
        with pytest.raises(ConfigurationError, match="dev_password"):
            RokuAdapter("tv", host="10.0.0.5", channel_zip=tmp_path / "x.zip")


class TestObservation:
    def test_screenshot_uses_digest_auth(self, roku, httpserver: HTTPServer):
        httpserver.expect_request("/plugin_inspect", method="POST").respond_with_handler(
            _digest_protected(b"<html>Screenshot ok</html>", "text/html")
        )
        httpserver.expect_request("/pkgs/dev.jpg").respond_with_handler(
            _digest_protected(_png((64, 32)), "image/png")
        )
        roku.connect()
        img = roku.screenshot()
        assert img.mode == "RGB" and img.size == (64, 32)
        inspect = [r for r, _ in httpserver.log if r.path == "/plugin_inspect"]
        assert b"Screenshot" in inspect[-1].get_data()

    def test_wrong_dev_password(self, roku, httpserver: HTTPServer):
        def always_401(request: Request) -> Response:
            return Response(
                "nope",
                status=401,
                headers={"WWW-Authenticate": 'Digest realm="rokudev", nonce="n", qop="auth"'},
            )

        httpserver.expect_request("/plugin_inspect", method="POST").respond_with_handler(always_401)
        roku.connect()
        with pytest.raises(DeviceConnectionError, match="developer password"):
            roku.screenshot()

    def test_logs_from_debug_console(self, httpserver: HTTPServer):
        console = _FakeDebugConsole(["BrightScript Debugger", "Player: state=PLAYING", "done"])
        httpserver.expect_request("/query/device-info").respond_with_data(
            DEVICE_INFO, content_type="text/xml"
        )
        adapter = RokuAdapter(
            "tv",
            host="127.0.0.1",
            ecp_port=httpserver.port,
            installer_port=httpserver.port,
            debug_port=console.port,
        )
        try:
            adapter.connect()
            assert _wait_for(lambda: "done" in adapter.get_logs())
            assert adapter.get_logs(lines=2).splitlines() == ["Player: state=PLAYING", "done"]
            assert adapter.get_logs(lines=0) == ""
        finally:
            adapter.disconnect()
            console.close()

    def test_logs_cleared_on_start(self, roku, httpserver: HTTPServer):
        httpserver.expect_request("/launch/dev", method="POST").respond_with_data("")
        roku.connect()
        roku._logs.append("stale")
        roku.start_application()
        assert roku.get_logs() == ""


class TestDebugConsoleReader:
    def test_stop_joins_promptly_when_port_unreachable(self):
        # Regression test: the reader's connect attempt used a 5s socket timeout while
        # disconnect() only waits 2s in join(), so stop() could leave a straggler thread
        # running (and able to write into a reused deque) well past disconnect(). A short
        # per-attempt connect timeout keeps the stop-event check responsive.
        reader = _DebugConsoleReader("127.0.0.1", 1, deque(), get_logger("test.roku"))
        reader.start()
        assert _wait_for(reader.is_alive)
        reader.stop()
        reader.join(timeout=3.0)
        assert not reader.is_alive()


class TestInput:
    @pytest.mark.parametrize(
        ("key", "ecp"),
        [
            ("KEYCODE_DPAD_LEFT", "Left"),
            ("enter", "Select"),
            ("BACK", "Back"),
            ("MEDIA_PLAY_PAUSE", "Play"),
            ("MEDIA_FAST_FORWARD", "Fwd"),
            ("Info", "Info"),
            ("a", "Lit_a"),
            # pytest-httpserver matches on the percent-decoded `request.path`, so the
            # expected path here is the decoded form of what's actually sent on the
            # wire (`Lit_%25`, i.e. a literal "%" character).
            ("%", "Lit_%"),
            ("InstantReplay", "InstantReplay"),
        ],
    )
    def test_press_key_mapping(self, roku, httpserver: HTTPServer, key, ecp):
        httpserver.expect_request(f"/keypress/{ecp}", method="POST").respond_with_data("")
        roku.connect()
        roku.press_key(key)
        assert httpserver.log[-1][0].path == f"/keypress/{ecp}"


class TestConfig:
    def test_from_config(self):
        config = DeviceConfig.model_validate(
            {
                "type": "roku",
                "host": "10.0.0.5",
                "dev_password": "pw",
                "ecp_port": 9060,
                "debug_port": 9085,
                "timeout": 3,
            }
        )
        adapter = RokuAdapter.from_config("tv", config)
        assert adapter._host == "10.0.0.5"
        assert adapter._dev_password == "pw"
        assert adapter._ecp_port == 9060 and adapter._debug_port == 9085
        assert adapter._timeout == 3.0

    def test_from_config_requires_host(self):
        with pytest.raises(ConfigurationError, match="host"):
            RokuAdapter.from_config("tv", DeviceConfig.model_validate({"type": "roku"}))

    def test_registered_as_roku(self):
        registry = DeviceRegistry()
        assert "roku" in registry.types()
        device = registry.create(
            "tv", DeviceConfig.model_validate({"type": "roku", "host": "10.0.0.5"})
        )
        assert isinstance(device, RokuAdapter)
