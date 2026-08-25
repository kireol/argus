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
from argus.exceptions import ConfigurationError, DeviceConnectionError

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
