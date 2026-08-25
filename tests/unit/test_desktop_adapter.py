"""DesktopAdapter (pyautogui + subprocess) behaviour, verified through a fake backend."""

from __future__ import annotations

import sys
import time
from collections import deque
from typing import Any

import pytest
from PIL import Image

from argus.adapters.desktop import (
    DesktopAdapter,
    _host_platform,
    _ProcessHandle,
    _pyautogui_backend,
)
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceConnectionError, ScreenshotError

_CHILD = (
    "import sys, time\n"
    "print('child started', flush=True)\n"
    "print('warning: something', file=sys.stderr, flush=True)\n"
    "time.sleep(30)\n"
)


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestBackendImport:
    def test_missing_pyautogui_is_remediated(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyautogui", None)  # makes `import pyautogui` fail
        with pytest.raises(DeviceConnectionError, match="pyautogui") as info:
            _pyautogui_backend()
        assert 'argus[desktop]' in (info.value.remediation or "")


class TestProcessHandle:
    def test_captures_merged_output_and_stops(self):
        sink: deque[str] = deque(maxlen=100)
        handle = _ProcessHandle([sys.executable, "-c", _CHILD], cwd=None, env=None, sink=sink)
        try:
            assert handle.running
            assert _wait_for(lambda: len(sink) >= 2)
            assert list(sink) == ["child started", "warning: something"]
        finally:
            handle.stop(timeout=2.0)
        assert not handle.running

    @pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM semantics")
    def test_stop_kills_when_terminate_is_ignored(self):
        sink: deque[str] = deque(maxlen=100)
        stubborn = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(30)\n"
        )
        handle = _ProcessHandle([sys.executable, "-c", stubborn], cwd=None, env=None, sink=sink)
        assert _wait_for(lambda: "ready" in sink)
        started = time.monotonic()
        handle.stop(timeout=0.5)
        assert not handle.running
        assert time.monotonic() - started < 5.0

    def test_missing_executable_is_connection_error(self):
        with pytest.raises(DeviceConnectionError, match="not found"):
            _ProcessHandle(["/definitely/not/a/binary"], cwd=None, env=None, sink=deque())

    def test_stop_after_child_exits_on_its_own(self):
        sink: deque[str] = deque(maxlen=100)
        handle = _ProcessHandle(
            [sys.executable, "-c", "print('bye', flush=True)"], cwd=None, env=None, sink=sink
        )
        assert _wait_for(lambda: not handle.running)
        handle.stop(timeout=1.0)
        handle.stop(timeout=1.0)
        assert "bye" in sink


class FakeBackend:
    """Records pyautogui-style calls; screenshot/logical sizes are configurable."""

    def __init__(
        self,
        *,
        logical: tuple[int, int] = (800, 600),
        pixels: tuple[int, int] = (800, 600),
        fail_size: bool = False,
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.logical = logical
        self.pixels = pixels
        self.fail_size = fail_size
        self.fill = (10, 20, 30)

    def size(self) -> tuple[int, int]:
        if self.fail_size:
            raise RuntimeError("no display")
        return self.logical

    def screenshot(self) -> Image.Image:
        self.calls.append(("screenshot", ()))
        return Image.new("RGB", self.pixels, self.fill)

    def click(self, x: float, y: float) -> None:
        self.calls.append(("click", (x, y)))

    def mouseDown(self, x: float, y: float) -> None:  # noqa: N802
        self.calls.append(("mouseDown", (x, y)))

    def mouseUp(self) -> None:  # noqa: N802
        self.calls.append(("mouseUp", ()))

    def moveTo(self, x: float, y: float, duration: float = 0.0) -> None:  # noqa: N802
        self.calls.append(("moveTo", (x, y, duration)))

    def press(self, key: str) -> None:
        self.calls.append(("press", (key,)))

    def hotkey(self, *keys: str) -> None:
        self.calls.append(("hotkey", keys))


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def adapter(backend: FakeBackend) -> DesktopAdapter:
    return DesktopAdapter(
        "app",
        command=sys.executable,
        args=["-c", _CHILD],
        backend_factory=lambda: backend,
    )


class TestIdentity:
    @pytest.mark.parametrize(
        ("sys_platform", "expected"),
        [("win32", "windows"), ("darwin", "macos"), ("linux", "linux"), ("freebsd14", "linux")],
    )
    def test_platform_derived_from_host(self, monkeypatch, sys_platform, expected):
        monkeypatch.setattr(sys, "platform", sys_platform)
        assert _host_platform() == expected
        adapter = DesktopAdapter("app", command="x", backend_factory=FakeBackend)
        assert adapter.platform == expected

    def test_explicit_platform_wins(self):
        adapter = DesktopAdapter("app", command="x", platform="kiosk", backend_factory=FakeBackend)
        assert adapter.platform == "kiosk"

    def test_capabilities(self, adapter):
        caps = adapter.capabilities
        assert caps.supports_screenshot and caps.supports_tap and caps.supports_swipe
        assert caps.supports_long_press and caps.supports_drag and caps.supports_keyboard
        assert caps.supports_app_lifecycle and caps.supports_logs
        assert caps.supports_multi_touch is False


class TestConnection:
    def test_connect_requires_working_display(self, adapter, backend):
        adapter.connect()
        assert adapter.is_available() is True
        result = adapter.health_check()
        assert result.healthy
        assert result.details["screen"] == "800x600"
        assert result.details["app_running"] is False

    def test_connect_without_display_is_remediated(self, monkeypatch):
        backend = FakeBackend(fail_size=True)
        adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(DeviceConnectionError, match="no display") as info:
            adapter.connect()
        assert "DISPLAY" in (info.value.remediation or "")
        assert adapter.is_available() is False

    def test_macos_remediation_mentions_permissions(self, monkeypatch):
        backend = FakeBackend(fail_size=True)
        adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        monkeypatch.setattr(sys, "platform", "darwin")
        with pytest.raises(DeviceConnectionError) as info:
            adapter.connect()
        assert "Screen Recording" in (info.value.remediation or "")

    def test_operations_before_connect_raise(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter._require_backend()

    def test_probes_do_not_connect(self, adapter):
        assert adapter.is_available() is True
        assert adapter.health_check().healthy
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter._require_backend()


class TestConfig:
    def test_from_config(self, tmp_path):
        config = DeviceConfig.model_validate(
            {
                "type": "desktop",
                "command": "./build/App",
                "args": ["--fullscreen"],
                "cwd": str(tmp_path),
                "env": {"EXAMPLE": "1"},
                "startup_wait": "2s",
                "stop_timeout": "250ms",
                "reset_command": "./reset.sh",
                "region": [10, 20, 300, 200],
            }
        )
        adapter = DesktopAdapter.from_config("app", config)
        assert adapter._command == "./build/App"
        assert adapter._args == ["--fullscreen"]
        assert adapter._cwd == str(tmp_path)
        assert adapter._env == {"EXAMPLE": "1"}
        assert adapter._startup_wait == 2.0
        assert adapter._stop_timeout == 0.25
        assert adapter._reset_command == "./reset.sh"
        assert adapter._region == (10, 20, 300, 200)

    def test_from_config_uses_effective_platform(self):
        config = DeviceConfig.model_validate(
            {"type": "desktop", "command": "x", "platform": "linux"}
        )
        assert DesktopAdapter.from_config("app", config).platform == "linux"

    def test_from_config_requires_command(self):
        with pytest.raises(ConfigurationError, match="command"):
            DesktopAdapter.from_config("app", DeviceConfig.model_validate({"type": "desktop"}))

    def test_from_config_rejects_bad_region(self):
        config = DeviceConfig.model_validate({"type": "desktop", "command": "x", "region": [1, 2]})
        with pytest.raises(ConfigurationError, match="region"):
            DesktopAdapter.from_config("app", config)

    def test_from_config_rejects_non_positive_region(self):
        for region in ([1, 2, 0, 5], [1, 2, 5, -1]):
            config = DeviceConfig.model_validate(
                {"type": "desktop", "command": "x", "region": region}
            )
            with pytest.raises(ConfigurationError, match="region"):
                DesktopAdapter.from_config("app", config)


class TestLifecycle:
    def test_start_captures_logs_and_stop_terminates(self, adapter):
        adapter.connect()
        adapter.start_application()
        try:
            assert adapter.is_application_running()
            assert _wait_for(lambda: "warning: something" in adapter.get_logs())
            assert adapter.get_logs(1) == "warning: something"
            assert adapter.get_logs(0) == ""
        finally:
            adapter.stop_application()
        assert not adapter.is_application_running()

    def test_start_clears_previous_logs_and_waits(self, backend, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr("argus.adapters.desktop.time.sleep", sleeps.append)
        adapter = DesktopAdapter(
            "app", command=sys.executable, args=["-c", _CHILD], startup_wait=1.5,
            backend_factory=lambda: backend,
        )
        adapter.connect()
        adapter._logs.append("stale")
        adapter.start_application()
        try:
            assert "stale" not in adapter.get_logs()
            assert sleeps == [1.5]
        finally:
            adapter.stop_application()

    def test_start_twice_restarts(self, adapter):
        adapter.connect()
        adapter.start_application()
        first = adapter._process.pid
        adapter.start_application()
        try:
            assert adapter._process.pid != first
        finally:
            adapter.stop_application()

    def test_env_merges_over_os_environ(self, backend, monkeypatch):
        monkeypatch.setenv("ARGUS_BASE", "base")
        script = "import os; print(os.environ['ARGUS_BASE'], os.environ['ARGUS_EXTRA'], flush=True)"
        adapter = DesktopAdapter(
            "app", command=sys.executable, args=["-c", script], env={"ARGUS_EXTRA": "extra"},
            backend_factory=lambda: backend,
        )
        adapter.connect()
        adapter.start_application()
        assert _wait_for(lambda: "base extra" in adapter.get_logs())
        adapter.stop_application()

    def test_reset_runs_reset_command_between_stop_and_start(self, backend, tmp_path):
        marker = tmp_path / "reset.txt"
        reset = f"{sys.executable} -c \"open(r'{marker}', 'w').write('x')\""
        adapter = DesktopAdapter(
            "app", command=sys.executable, args=["-c", _CHILD], reset_command=reset,
            backend_factory=lambda: backend,
        )
        adapter.connect()
        adapter.start_application()
        first = adapter._process.pid
        adapter.reset_application()
        try:
            assert marker.exists()
            assert adapter._process.pid != first
            assert adapter.is_application_running()
        finally:
            adapter.stop_application()

    def test_reset_command_failure_is_connection_error(self, backend):
        adapter = DesktopAdapter(
            "app", command=sys.executable, args=["-c", _CHILD],
            reset_command=f"{sys.executable} -c \"import sys; sys.exit(3)\"",
            backend_factory=lambda: backend,
        )
        adapter.connect()
        with pytest.raises(DeviceConnectionError, match="exit 3"):
            adapter.reset_application()
        assert not adapter.is_application_running()

    def test_reset_command_timeout_is_connection_error(self, backend, monkeypatch):
        monkeypatch.setattr("argus.adapters.desktop._RESET_TIMEOUT", 0.2)
        adapter = DesktopAdapter(
            "app", command=sys.executable, args=["-c", _CHILD],
            reset_command=f"{sys.executable} -c \"import time; time.sleep(30)\"",
            stop_timeout=0.2,
            backend_factory=lambda: backend,
        )
        adapter.connect()
        with pytest.raises(DeviceConnectionError, match="timed out"):
            adapter.reset_application()
        assert not adapter.is_application_running()

    def test_stop_when_not_running_is_noop(self, adapter):
        adapter.connect()
        adapter.stop_application()
        assert not adapter.is_application_running()

    def test_disconnect_stops_running_app(self, adapter):
        adapter.connect()
        adapter.start_application()
        adapter.disconnect()
        assert not adapter.is_application_running()


class TestObservation:
    def test_screenshot_is_rgb_full_screen(self, adapter, backend):
        adapter.connect()
        img = adapter.screenshot()
        assert img.mode == "RGB" and img.size == (800, 600)

    def test_region_crops_screenshot(self, backend):
        adapter = DesktopAdapter(
            "app", command="x", region=(10, 20, 300, 200), backend_factory=lambda: backend
        )
        adapter.connect()
        assert adapter.screenshot().size == (300, 200)
        info = adapter.get_screen_info()
        assert (info.width, info.height) == (300, 200)

    def test_region_outside_screen_is_error(self, backend):
        adapter = DesktopAdapter(
            "app", command="x", region=(700, 500, 300, 200), backend_factory=lambda: backend
        )
        adapter.connect()
        with pytest.raises(ScreenshotError, match="region"):
            adapter.screenshot()

    def test_hidpi_ratio_from_screenshot_vs_logical(self):
        backend = FakeBackend(logical=(800, 600), pixels=(1600, 1200))
        adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        adapter.connect()
        assert adapter._pixel_ratio() == 2.0
        assert adapter._to_logical((200, 100)) == (100, 50)
        adapter._pixel_ratio()
        assert backend.calls.count(("screenshot", ())) == 1  # ratio is cached
        info = adapter.get_screen_info()
        assert (info.width, info.height, info.scale) == (1600, 1200, 2.0)

    def test_to_logical_adds_region_offset(self):
        backend = FakeBackend(logical=(800, 600), pixels=(1600, 1200))
        adapter = DesktopAdapter(
            "app", command="x", region=(100, 40, 400, 400), backend_factory=lambda: backend
        )
        adapter.connect()
        # pixel (10, 10) inside the region = pixel (110, 50) on screen = logical (55, 25)
        assert adapter._to_logical((10, 10)) == (55, 25)

    def test_black_screenshot_on_macos_mentions_permission(self, monkeypatch, backend):
        monkeypatch.setattr(sys, "platform", "darwin")
        backend.fill = (0, 0, 0)
        black_adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        with pytest.raises(DeviceConnectionError, match="Screen Recording"):
            black_adapter.connect()

        backend.fill = (10, 20, 30)
        adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        adapter.connect()
        backend.fill = (0, 0, 0)
        img = adapter.screenshot()
        assert img.mode == "RGB"
