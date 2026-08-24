"""TvosSimAdapter unit tests with an injected command runner (no Xcode needed)."""

from __future__ import annotations

import io
import json
import time

import pytest
from PIL import Image

from argus.adapters.registry import DeviceRegistry
from argus.adapters.tvos_sim import CommandResult, TvosSimAdapter
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError

UDID = "AAAA-1111"
DEVICES_JSON = json.dumps(
    {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.tvOS-17-0": [
                {"udid": UDID, "name": "Apple TV 4K", "state": "Booted", "isAvailable": True},
                {"udid": "BBBB-2222", "name": "Apple TV", "state": "Shutdown", "isAvailable": True},
            ],
            "com.apple.CoreSimulator.SimRuntime.iOS-17-0": [
                {"udid": "CCCC-3333", "name": "iPhone 15", "state": "Booted", "isAvailable": True}
            ],
        }
    }
).encode()


def _png(size=(1920, 1080)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


class FakeRunner:
    """Records argv lists; answers by longest matching argv prefix."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: dict[tuple[str, ...], CommandResult] = {}
        self.responses[("xcrun", "simctl", "list", "devices", "-j")] = CommandResult(
            0, DEVICES_JSON, b""
        )
        self.responses[("xcrun", "simctl", "io")] = CommandResult(0, _png(), b"")

    def __call__(self, argv: list[str]) -> CommandResult:
        self.calls.append(list(argv))
        for prefix in sorted(self.responses, key=len, reverse=True):
            if tuple(argv[: len(prefix)]) == prefix:
                return self.responses[prefix]
        return CommandResult(0, b"", b"")

    def argv_with(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if tuple(c[: len(prefix)]) == prefix]


class FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = io.BytesIO("".join(line + "\n" for line in lines).encode())
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def process() -> FakeProcess:
    return FakeProcess(["app started", "Player: state=PLAYING"])


@pytest.fixture
def sim(runner: FakeRunner, process: FakeProcess) -> TvosSimAdapter:
    return TvosSimAdapter(
        "sim", bundle_id="com.example.tv", runner=runner, spawner=lambda argv: process
    )


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestIdentity:
    def test_capabilities(self, sim):
        caps = sim.capabilities
        assert caps.supports_screenshot and caps.supports_keyboard and caps.supports_logs
        assert caps.supports_app_lifecycle
        assert not caps.supports_tap and not caps.supports_swipe
        assert sim.platform == "tvos_sim"

    def test_tap_unsupported(self, sim):
        with pytest.raises(DeviceCapabilityError):
            sim.tap(1, 1)


class TestConnection:
    def test_connect_resolves_booted_tvos_device(self, sim, runner):
        sim.connect()
        assert sim._udid == UDID
        assert ["open", "-a", "Simulator"] in runner.calls
        spawn_free = runner.argv_with("xcrun", "simctl", "boot")
        assert spawn_free == []  # already booted
        assert sim.health_check().healthy

    def test_connect_boots_shutdown_device(self, runner, process):
        adapter = TvosSimAdapter(
            "sim",
            bundle_id="com.example.tv",
            udid="BBBB-2222",
            runner=runner,
            spawner=lambda argv: process,
        )
        adapter.connect()
        assert runner.argv_with("xcrun", "simctl", "boot", "BBBB-2222")
        assert runner.argv_with("xcrun", "simctl", "bootstatus", "BBBB-2222", "-b")

    def test_connect_fails_without_booted_tvos(self, runner, process):
        runner.responses[("xcrun", "simctl", "list", "devices", "-j")] = CommandResult(
            0, json.dumps({"devices": {}}).encode(), b""
        )
        adapter = TvosSimAdapter(
            "sim", bundle_id="com.example.tv", runner=runner, spawner=lambda argv: process
        )
        with pytest.raises(DeviceConnectionError, match="no booted tvOS simulator"):
            adapter.connect()

    def test_connect_installs_app_path(self, runner, process, tmp_path):
        app = tmp_path / "Demo.app"
        app.mkdir()
        adapter = TvosSimAdapter(
            "sim",
            bundle_id="com.example.tv",
            app_path=app,
            runner=runner,
            spawner=lambda argv: process,
        )
        adapter.connect()
        assert runner.argv_with("xcrun", "simctl", "install", UDID, str(app))

    def test_missing_xcrun(self, process):
        def no_xcrun(argv):
            raise FileNotFoundError("xcrun")

        adapter = TvosSimAdapter(
            "sim", bundle_id="com.example.tv", runner=no_xcrun, spawner=lambda argv: process
        )
        assert adapter.is_available() is False
        with pytest.raises(DeviceConnectionError, match="Xcode"):
            adapter.connect()

    def test_operations_before_connect_raise(self, sim):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            sim.screenshot()

    def test_connect_recovers_from_log_stream_failure(self, runner, process):
        def failing_spawn(argv):
            raise RuntimeError("spawn failed")

        adapter = TvosSimAdapter(
            "sim", bundle_id="com.example.tv", runner=runner, spawner=failing_spawn
        )
        with pytest.raises(DeviceConnectionError, match="spawn failed"):
            adapter.connect()
        assert adapter._udid is None
        assert not adapter.is_application_running()

        adapter._spawn = lambda argv: process
        adapter.connect()
        assert adapter._udid == UDID


class TestLifecycle:
    def test_start_stop_reset(self, sim, runner, tmp_path):
        sim.connect()
        sim.start_application()
        assert runner.argv_with("xcrun", "simctl", "launch", UDID, "com.example.tv")
        assert sim.is_application_running()
        sim.stop_application()
        assert runner.argv_with("xcrun", "simctl", "terminate", UDID, "com.example.tv")
        assert not sim.is_application_running()
        sim.reset_application()
        assert len(runner.argv_with("xcrun", "simctl", "launch")) == 2
        assert not runner.argv_with("xcrun", "simctl", "uninstall")

    def test_reset_reinstalls_when_app_path(self, runner, process, tmp_path):
        app = tmp_path / "Demo.app"
        app.mkdir()
        adapter = TvosSimAdapter(
            "sim",
            bundle_id="com.example.tv",
            app_path=app,
            runner=runner,
            spawner=lambda argv: process,
        )
        adapter.connect()
        adapter.reset_application()
        assert runner.argv_with("xcrun", "simctl", "uninstall", UDID, "com.example.tv")
        assert len(runner.argv_with("xcrun", "simctl", "install")) == 2

    def test_launch_failure_raises(self, sim, runner):
        runner.responses[("xcrun", "simctl", "launch")] = CommandResult(1, b"", b"no such app")
        sim.connect()
        with pytest.raises(DeviceConnectionError, match="no such app"):
            sim.start_application()

    def test_disconnect_stops_log_stream(self, sim, process):
        sim.connect()
        sim.disconnect()
        assert process.terminated
        assert not sim.is_application_running()


class TestObservation:
    def test_screenshot_and_screen_info(self, sim, runner):
        sim.connect()
        img = sim.screenshot()
        assert img.mode == "RGB" and img.size == (1920, 1080)
        assert runner.argv_with("xcrun", "simctl", "io", UDID, "screenshot", "--type", "png", "-")
        assert sim.get_screen_info().size == (1920, 1080)

    def test_logs_stream(self, sim):
        sim.connect()
        assert _wait_for(lambda: "PLAYING" in sim.get_logs())
        assert sim.get_logs().splitlines() == ["app started", "Player: state=PLAYING"]
        assert sim.get_logs(lines=1) == "Player: state=PLAYING"

    def test_log_predicate_uses_process_name(self, sim, runner):
        sim.connect()
        assert sim._spawned_argv[-1] == 'process == "tv"'


class TestInput:
    @pytest.mark.parametrize(
        ("key", "fragment"),
        [
            ("KEYCODE_DPAD_UP", "key code 126"),
            ("left", "key code 123"),
            ("ENTER", "key code 36"),
            ("BACK", "key code 53"),
            ("MEDIA_PLAY_PAUSE", "key code 49"),
            ("HOME", 'keystroke "h" using {command down, shift down}'),
            ("a", 'keystroke "a"'),
        ],
    )
    def test_press_key_scripts(self, sim, runner, key, fragment):
        sim.connect()
        sim.press_key(key)
        script = runner.calls[-1]
        assert script[0] == "osascript"
        assert 'tell application "Simulator" to activate' in script
        assert any(fragment in part for part in script)

    def test_unknown_key_raises(self, sim):
        sim.connect()
        with pytest.raises(DeviceCapabilityError, match="F13"):
            sim.press_key("F13")

    def test_accessibility_denied(self, sim, runner):
        runner.responses[("osascript",)] = CommandResult(
            1, b"", b"osascript is not allowed assistive access. (-1719)"
        )
        sim.connect()
        with pytest.raises(DeviceConnectionError, match="Accessibility"):
            sim.press_key("ENTER")

    def test_press_key_escapes_double_quote(self, sim, runner):
        sim.connect()
        sim.press_key('"')
        script = runner.calls[-1]
        assert any('keystroke "\\""' in part for part in script)

    def test_press_key_escapes_backslash(self, sim, runner):
        sim.connect()
        sim.press_key("\\")
        script = runner.calls[-1]
        assert any('keystroke "\\\\"' in part for part in script)


class TestConfig:
    def test_from_config(self, tmp_path):
        config = DeviceConfig.model_validate(
            {
                "type": "tvos_sim",
                "bundle_id": "com.example.tv",
                "udid": UDID,
                "boot": False,
                "process_name": "DemoTV",
                "timeout": 5,
            }
        )
        adapter = TvosSimAdapter.from_config("sim", config)
        assert adapter._bundle_id == "com.example.tv"
        assert adapter._requested_udid == UDID
        assert adapter._boot is False
        assert adapter._process_name == "DemoTV"
        assert adapter._timeout == 5.0

    def test_from_config_requires_bundle_id(self):
        with pytest.raises(ConfigurationError, match="bundle_id"):
            TvosSimAdapter.from_config("sim", DeviceConfig.model_validate({"type": "tvos_sim"}))

    def test_registered_as_tvos_sim(self):
        registry = DeviceRegistry()
        assert "tvos_sim" in registry.types()
        device = registry.create(
            "sim", DeviceConfig.model_validate({"type": "tvos_sim", "bundle_id": "com.x.y"})
        )
        assert isinstance(device, TvosSimAdapter)
