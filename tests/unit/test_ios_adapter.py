"""IosAdapter (WebDriverAgent) behaviour, verified through the requests it sends."""

from __future__ import annotations

import base64
import io
import time
from typing import Any

import pytest
from PIL import Image

from argus.adapters.ios import IosAdapter, _HttpWdaClient
from argus.config.models import DeviceConfig
from argus.exceptions import (
    ConfigurationError,
    DeviceCapabilityError,
    DeviceConnectionError,
    ScreenshotError,
)


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


class TestObservation:
    def test_screenshot_decodes_base64_png(self, adapter, wda):
        adapter.connect()
        img = adapter.screenshot()
        assert img.mode == "RGB" and img.size == (8, 12)

    def test_screenshot_bad_data_raises(self, adapter, wda):
        adapter.connect()
        wda.responses[("GET", "/screenshot")] = {"value": base64.b64encode(b"nope").decode()}
        with pytest.raises(ScreenshotError, match="PNG"):
            adapter.screenshot()

    def test_screen_info_is_points_times_scale(self, adapter, wda):
        adapter.connect()
        info = adapter.get_screen_info()
        assert (info.width, info.height) == (8, 12)
        assert adapter._pixel_scale() == 2.0
        adapter.get_screen_info()
        assert wda.paths("GET").count("/session/S1/wda/screen") == 1  # cached

    def test_scale_defaults_to_one_when_missing(self, adapter, wda):
        adapter.connect()
        wda.responses[("GET", "/session/S1/wda/screen")] = {"value": {}}
        assert adapter._pixel_scale() == 1.0
        assert adapter._to_points((10, 20)) == (10, 20)

    def test_to_points_divides_by_scale(self, adapter, wda):
        adapter.connect()
        assert adapter._to_points((101, 20)) == (50.5, 10)


def _sources(wda: FakeWda) -> list[dict[str, Any]]:
    body = wda.body("POST", "/session/S1/actions")
    assert body is not None
    return body["actions"]


class TestGestures:
    def test_tap_is_move_down_up_in_points(self, adapter, wda):
        adapter.connect()
        adapter.tap(100, 40)
        (finger,) = _sources(wda)
        assert finger["type"] == "pointer"
        assert finger["parameters"] == {"pointerType": "touch"}
        assert finger["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 50, "y": 20},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerUp", "button": 0},
        ]
        assert wda.paths()[-1] == "/session/S1/actions"
        assert wda.calls[-1][0] == "DELETE"  # pointer state released

    def test_swipe_moves_with_duration(self, adapter, wda):
        adapter.connect()
        adapter.swipe(0, 0, 200, 100, duration_ms=300)
        (finger,) = _sources(wda)
        assert finger["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 0, "y": 0},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "duration": 300, "x": 100, "y": 50},
            {"type": "pointerUp", "button": 0},
        ]

    def test_long_press_pauses(self, adapter, wda):
        adapter.connect()
        adapter.long_press(10, 10, duration_ms=1500)
        (finger,) = _sources(wda)
        assert finger["actions"][2] == {"type": "pause", "duration": 1500}
        assert [a["type"] for a in finger["actions"]] == [
            "pointerMove", "pointerDown", "pause", "pointerUp",
        ]

    def test_drag_holds_then_moves(self, adapter, wda):
        adapter.connect()
        adapter.drag(0, 0, 20, 20, hold_ms=600, duration_ms=250)
        (finger,) = _sources(wda)
        assert finger["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 0, "y": 0},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 600},
            {"type": "pointerMove", "duration": 250, "x": 10, "y": 10},
            {"type": "pointerUp", "button": 0},
        ]

    def test_multi_touch_one_source_per_finger_segment_durations(self, adapter, wda):
        adapter.connect()
        adapter.multi_touch([[(0, 0), (20, 0), (20, 20)], [(100, 100), (80, 80)]], 400)
        first, second = _sources(wda)
        assert first["id"] == "finger0" and second["id"] == "finger1"
        assert first["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 0, "y": 0},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "duration": 200, "x": 10, "y": 0},
            {"type": "pointerMove", "duration": 200, "x": 10, "y": 10},
            {"type": "pointerUp", "button": 0},
        ]
        assert second["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 50, "y": 50},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "duration": 400, "x": 40, "y": 40},
            {"type": "pointerUp", "button": 0},
        ]

    def test_pinch_produces_two_mirrored_fingers(self, adapter, wda):
        adapter.connect()
        adapter.pinch(200, 300, start_distance=100, end_distance=200, duration_ms=500)
        left, right = _sources(wda)
        assert left["actions"][0] == {"type": "pointerMove", "duration": 0, "x": 75, "y": 150}
        assert left["actions"][2] == {"type": "pointerMove", "duration": 500, "x": 50, "y": 150}
        assert right["actions"][0] == {"type": "pointerMove", "duration": 0, "x": 125, "y": 150}
        assert right["actions"][2] == {"type": "pointerMove", "duration": 500, "x": 150, "y": 150}

    def test_gesture_before_connect_raises(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter.tap(1, 1)


class TestKeys:
    @pytest.mark.parametrize(
        ("key", "path", "body"),
        [
            ("HOME", "/session/S1/wda/homescreen", {}),
            ("KEYCODE_HOME", "/session/S1/wda/homescreen", {}),
            ("VOLUME_UP", "/session/S1/wda/pressButton", {"name": "volumeUp"}),
            ("VOLUME_DOWN", "/session/S1/wda/pressButton", {"name": "volumeDown"}),
            ("LOCK", "/session/S1/wda/pressButton", {"name": "lock"}),
            ("ENTER", "/session/S1/wda/keys", {"value": ["\n"]}),
            ("DEL", "/session/S1/wda/keys", {"value": ["\b"]}),
            ("a", "/session/S1/wda/keys", {"value": ["a"]}),
            ("hello", "/session/S1/wda/keys", {"value": ["h", "e", "l", "l", "o"]}),
        ],
    )
    def test_press_key_mapping(self, adapter, wda, key, path, body):
        adapter.connect()
        adapter.press_key(key)
        assert wda.calls[-1] == ("POST", path, body)


class FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = io.BytesIO("".join(line + "\n" for line in lines).encode())
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestLogs:
    def test_get_logs_unsupported_without_log_command(self, adapter):
        adapter.connect()
        with pytest.raises(DeviceCapabilityError, match="get_logs"):
            adapter.get_logs()

    def test_log_command_is_spawned_and_pumped(self, wda):
        process = FakeProcess(["boot", "Player: state=PLAYING"])
        spawned: list[list[str]] = []

        def spawn(argv):
            spawned.append(argv)
            return process

        adapter = IosAdapter(
            "iphone",
            bundle_id="com.example.app",
            log_command="xcrun simctl spawn booted log stream --predicate 'process == \"Ex\"'",
            client_factory=lambda: wda,
            spawner=spawn,
        )
        adapter.connect()
        assert spawned == [
            ["xcrun", "simctl", "spawn", "booted", "log", "stream", "--predicate",
             'process == "Ex"']
        ]
        assert _wait_for(lambda: "Player: state=PLAYING" in adapter.get_logs())
        assert adapter.get_logs(1) == "Player: state=PLAYING"
        adapter.disconnect()
        assert process.terminated

    def test_missing_log_binary_raises_remediated(self, wda):
        def spawn(argv):
            raise FileNotFoundError(argv[0])

        adapter = IosAdapter(
            "iphone", bundle_id="com.example.app", log_command="idevicesyslog",
            client_factory=lambda: wda, spawner=spawn,
        )
        with pytest.raises(DeviceConnectionError, match="idevicesyslog"):
            adapter.connect()
        # connect failed after the session was created: it must be cleaned up
        assert ("DELETE", "/session/S1", None) in wda.calls

    def test_non_connection_spawn_error_is_wrapped_and_cleaned_up(self, wda):
        def spawn(argv):
            raise PermissionError("denied")

        adapter = IosAdapter(
            "iphone", bundle_id="com.example.app", log_command="idevicesyslog",
            client_factory=lambda: wda, spawner=spawn,
        )
        with pytest.raises(DeviceConnectionError, match="denied"):
            adapter.connect()
        assert ("DELETE", "/session/S1", None) in wda.calls

    def test_blank_log_command_behaves_as_unset(self, wda):
        adapter = IosAdapter(
            "iphone", bundle_id="com.example.app", log_command="",
            client_factory=lambda: wda,
        )
        assert adapter.capabilities.supports_logs is False
        adapter.connect()
        with pytest.raises(DeviceCapabilityError, match="get_logs"):
            adapter.get_logs()
