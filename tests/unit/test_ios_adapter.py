"""IosAdapter (WebDriverAgent) behaviour, verified through the requests it sends."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from argus.adapters.ios import _HttpWdaClient
from argus.exceptions import DeviceConnectionError


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
