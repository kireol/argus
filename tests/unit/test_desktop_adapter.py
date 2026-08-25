"""DesktopAdapter (pyautogui + subprocess) behaviour, verified through a fake backend."""

from __future__ import annotations

import sys
import time
from collections import deque

import pytest

from argus.adapters.desktop import _ProcessHandle, _pyautogui_backend
from argus.exceptions import DeviceConnectionError

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
