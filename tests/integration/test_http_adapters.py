"""Backend adapter and HTTP instrumentation against a real local HTTP server."""

import pytest
from pytest_httpserver import HTTPServer

from utf.adapters.backend import BackendAdapter
from utf.config.models import BackendConfig, InstrumentationConfig
from utf.exceptions import BackendError, InstrumentationError
from utf.instrumentation.client import HttpInstrumentationClient

pytestmark = pytest.mark.integration


@pytest.fixture
def backend(httpserver: HTTPServer):
    config = BackendConfig(
        base_url=httpserver.url_for("/"),
        token="secret-token",
        timeout="2s",
        retries=1,
    )
    adapter = BackendAdapter(config)
    yield adapter
    adapter.close()


class TestBackendAdapter:
    def test_get(self, backend, httpserver):
        httpserver.expect_request("/api/movies").respond_with_json({"movies": []})
        response = backend.get("/api/movies")
        assert response.status_code == 200
        assert response.json() == {"movies": []}

    def test_auth_header_sent(self, backend, httpserver):
        def handler(request):
            from werkzeug.wrappers import Response

            assert request.headers.get("Authorization") == "Bearer secret-token"
            return Response("{}", content_type="application/json")

        httpserver.expect_request("/api/auth-check").respond_with_handler(handler)
        backend.get("/api/auth-check")

    def test_set_state(self, backend, httpserver):
        httpserver.expect_request(
            "/api/state", method="POST", json={"movieId": 123}
        ).respond_with_json({"ok": True})
        assert backend.set_state({"movieId": 123}) == {"ok": True}

    def test_set_state_error_status_raises(self, backend, httpserver):
        httpserver.expect_request("/api/state", method="POST").respond_with_data(
            "nope", status=500
        )
        with pytest.raises(BackendError, match="500"):
            backend.set_state({"movieId": 1})

    def test_health_check(self, backend, httpserver):
        httpserver.expect_request("/health").respond_with_json({"status": "up"})
        assert backend.health_check().healthy

    def test_health_check_failure(self, backend, httpserver):
        httpserver.expect_request("/health").respond_with_data("down", status=503)
        assert not backend.health_check().healthy

    def test_unconfigured_backend_raises(self):
        with pytest.raises(BackendError, match="not configured"):
            BackendAdapter(BackendConfig())

    def test_unreachable_backend_raises_backend_error(self):
        config = BackendConfig(
            base_url="http://127.0.0.1:1", timeout=0.2, retries=0
        )
        adapter = BackendAdapter(config)
        try:
            with pytest.raises(BackendError):
                adapter.get("/anything")
        finally:
            adapter.close()


class TestHttpInstrumentation:
    @pytest.fixture
    def client(self, httpserver):
        config = InstrumentationConfig(base_url=httpserver.url_for("/"), timeout="2s")
        client = HttpInstrumentationClient(config)
        yield client
        client.close()

    def test_status(self, client, httpserver):
        httpserver.expect_request("/test/status").respond_with_json(
            {
                "application": "MyApp",
                "version": "2.14.3",
                "ready": True,
                "screen": "movie_details",
                "capabilities": ["status", "screen"],
            }
        )
        status = client.status()
        assert status.ready is True
        assert status.screen == "movie_details"
        assert status.capabilities == ["status", "screen"]

    def test_state(self, client, httpserver):
        httpserver.expect_request("/test/state").respond_with_json({"movie_id": 123})
        assert client.state() == {"movie_id": 123}

    def test_health(self, client, httpserver):
        httpserver.expect_request("/test/health").respond_with_json({"ok": True})
        assert client.health_check().healthy

    def test_unreachable_raises(self):
        config = InstrumentationConfig(base_url="http://127.0.0.1:1", timeout=0.2)
        client = HttpInstrumentationClient(config)
        try:
            with pytest.raises(InstrumentationError):
                client.status()
            assert not client.health_check().healthy
        finally:
            client.close()

    def test_partial_fields_allowed(self, client, httpserver):
        httpserver.expect_request("/test/status").respond_with_json({"ready": True})
        status = client.status()
        assert status.ready is True
        assert status.screen is None
