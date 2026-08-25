"""IosAdapter (WebDriverAgent) behaviour, verified through the requests it sends."""

from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from PIL import Image

from argus.adapters.ios import IosAdapter, _HttpWdaClient
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceConnectionError


def _png(size: tuple[int, int] = (4, 6)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (9, 8, 7)).save(buf, format="PNG")
    return buf.getvalue()


class TestHttpClient:
    def test_get_and_post_json(self, httpserver):
        httpserver.expect_request("/status", method="GET").respond_with_json(
            {"value": {"ready": True}}
        )
        httpserver.expect_request(
            "/session", method="POST", json={"capabilities": {}}
        ).respond_with_json({"value": {"sessionId": "abc"}, "sessionId": "abc"})
        client = _HttpWdaClient(httpserver.url_for("/"), timeout=2.0)
        assert client.request("GET", "/status") == {"value": {"ready": True}}
        assert client.request("POST", "/session", {"capabilities": {}})["sessionId"] == "abc"

    def test_wda_error_payload_becomes_connection_error(self, httpserver):
        httpserver.expect_request("/session/x/actions", method="POST").respond_with_json(
            {"value": {"error": "invalid session id", "message": "Session does not exist"}},
            status=404,
        )
        client = _HttpWdaClient(httpserver.url_for("/"), timeout=2.0)
        with pytest.raises(DeviceConnectionError, match="invalid session id") as info:
            client.request("POST", "/session/x/actions", {})
        assert "Session does not exist" in str(info.value)
        assert "reconnect" in (info.value.remediation or "")

    def test_unreachable_server_is_connection_error(self):
        client = _HttpWdaClient("http://127.0.0.1:1", timeout=0.5)
        with pytest.raises(DeviceConnectionError, match="WebDriverAgent") as info:
            client.request("GET", "/status")
        assert "docs/ios.md" in (info.value.remediation or "")


class FakeWda:
    """Records (method, path, body); answers by exact path, then by prefix."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.responses: dict[tuple[str, str], Any] = {
            ("GET", "/status"): {"value": {"ready": True, "state": "success"}},
            ("POST", "/session"): {"sessionId": "S1", "value": {"sessionId": "S1"}},
            ("GET", "/screenshot"): {"value": base64.b64encode(_png((8, 12))).decode()},
            ("GET", "/session/S1/window/size"): {"value": {"width": 4, "height": 6}},
            ("GET", "/session/S1/wda/screen"): {"value": {"scale": 2, "statusBarSize": {}}},
            ("POST", "/session/S1/wda/apps/state"): {"value": 4},
        }
        self.fail_with: DeviceConnectionError | None = None

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, path, body))
        if self.fail_with is not None:
            raise self.fail_with
        return self.responses.get((method, path), {"value": None})

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.calls if method is None or m == method]

    def body(self, method: str, path: str) -> dict[str, Any] | None:
        return next(b for m, p, b in self.calls if (m, p) == (method, path))


@pytest.fixture
def wda() -> FakeWda:
    return FakeWda()


@pytest.fixture
def adapter(wda: FakeWda) -> IosAdapter:
    return IosAdapter("iphone", bundle_id="com.example.app", client_factory=lambda: wda)


class TestIdentity:
    def test_platform_and_capabilities(self, adapter):
        assert adapter.platform == "ios"
        caps = adapter.capabilities
        assert caps.supports_screenshot and caps.supports_app_lifecycle
        assert caps.supports_tap and caps.supports_swipe and caps.supports_keyboard
        assert caps.supports_long_press and caps.supports_drag and caps.supports_multi_touch
        assert caps.supports_logs is False

    def test_logs_capability_follows_log_command(self, wda):
        adapter = IosAdapter(
            "iphone", bundle_id="com.example.app", log_command="idevicesyslog",
            client_factory=lambda: wda,
        )
        assert adapter.capabilities.supports_logs is True


class TestConnection:
    def test_connect_creates_session_for_bundle(self, adapter, wda):
        adapter.connect()
        assert wda.paths() == ["/status", "/session"]
        assert wda.body("POST", "/session") == {
            "capabilities": {"alwaysMatch": {"bundleId": "com.example.app"}}
        }
        assert adapter._session_path("/actions") == "/session/S1/actions"

    def test_disconnect_deletes_session(self, adapter, wda):
        adapter.connect()
        adapter.disconnect()
        assert ("DELETE", "/session/S1", None) in wda.calls
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter._require_session()

    def test_connect_unreachable_raises(self, adapter, wda):
        wda.fail_with = DeviceConnectionError("Cannot reach WebDriverAgent", remediation="x")
        with pytest.raises(DeviceConnectionError, match="Cannot reach"):
            adapter.connect()

    def test_connect_without_session_id_raises(self, adapter, wda):
        wda.responses[("POST", "/session")] = {"value": {}}
        with pytest.raises(DeviceConnectionError, match="session"):
            adapter.connect()

    def test_is_available_and_health_check(self, adapter, wda):
        assert adapter.is_available() is True
        adapter.connect()
        result = adapter.health_check()
        assert result.healthy
        assert result.details["app_running"] is True
        wda.fail_with = DeviceConnectionError("down")
        assert adapter.is_available() is False
        assert adapter.health_check().healthy is False


class TestConfig:
    def test_from_config(self):
        config = DeviceConfig.model_validate(
            {
                "type": "ios",
                "bundle_id": "com.example.app",
                "url": "http://10.0.0.5:8100/",
                "timeout": 5,
                "log_command": "idevicesyslog -u 0001",
            }
        )
        adapter = IosAdapter.from_config("iphone", config)
        assert adapter._bundle_id == "com.example.app"
        assert adapter._url == "http://10.0.0.5:8100/"
        assert adapter._timeout == 5.0
        assert adapter._log_command == "idevicesyslog -u 0001"
        assert isinstance(adapter._client, _HttpWdaClient)

    def test_from_config_requires_bundle_id(self):
        with pytest.raises(ConfigurationError, match="bundle_id"):
            IosAdapter.from_config("iphone", DeviceConfig.model_validate({"type": "ios"}))


class TestLifecycle:
    def test_start_stop_reset(self, adapter, wda):
        adapter.connect()
        adapter.start_application()
        adapter.stop_application()
        adapter.reset_application()
        launches = [b for m, p, b in wda.calls if p == "/session/S1/wda/apps/launch"]
        terminates = [b for m, p, b in wda.calls if p == "/session/S1/wda/apps/terminate"]
        assert launches == [{"bundleId": "com.example.app"}] * 2
        assert terminates == [{"bundleId": "com.example.app"}] * 2
        # reset = terminate then launch, in that order
        order = [p for _, p, _ in wda.calls[-2:]]
        assert order == ["/session/S1/wda/apps/terminate", "/session/S1/wda/apps/launch"]

    def test_is_application_running_reads_state(self, adapter, wda):
        adapter.connect()
        assert adapter.is_application_running() is True
        wda.responses[("POST", "/session/S1/wda/apps/state")] = {"value": 1}
        assert adapter.is_application_running() is False

    def test_lifecycle_before_connect_raises(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter.start_application()
