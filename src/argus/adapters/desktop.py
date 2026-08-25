"""Desktop application adapter (Windows / Linux / macOS).

Launches the application under test as a local process, screenshots the
display and drives it with mouse and keyboard through ``pyautogui``
(optional dependency: ``pip install "argus[desktop]"``). The process's
stdout/stderr become the device logs. Coordinates in tests are screenshot
pixels; the adapter converts them to logical (HiDPI-scaled) coordinates.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities, Point
from argus.config.models import DeviceConfig
from argus.exceptions import (
    ConfigurationError,
    DeviceCapabilityError,
    DeviceConnectionError,
    ScreenshotError,
)
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo
from argus.utilities.duration import parse_duration

_MAX_LOG_LINES = 5000
_INSTALL = 'pip install "argus[desktop]"'


class DesktopBackend(Protocol):
    """The slice of pyautogui the adapter relies on (fakeable)."""

    def size(self) -> tuple[int, int]: ...
    def screenshot(self) -> Image: ...
    def click(self, x: float, y: float) -> None: ...
    def mouseDown(self, x: float, y: float) -> None: ...  # noqa: N802 - pyautogui names
    def mouseUp(self) -> None: ...  # noqa: N802
    def moveTo(self, x: float, y: float, duration: float = 0.0) -> None: ...  # noqa: N802
    def press(self, key: str) -> None: ...
    def hotkey(self, *keys: str) -> None: ...


BackendFactory = Callable[[], DesktopBackend]


def _pyautogui_backend() -> DesktopBackend:
    try:
        import pyautogui
    except ImportError as exc:
        raise DeviceConnectionError(
            "pyautogui is not installed (required for desktop devices).",
            remediation=f"Install desktop support: {_INSTALL}",
        ) from exc
    pyautogui.FAILSAFE = False  # a corner-of-screen mouse must not abort a test run
    pyautogui.PAUSE = 0  # gestures pace themselves; no per-call sleep
    return pyautogui


class _LogPump(threading.Thread):
    """Copies a process's stdout lines into a bounded deque."""

    def __init__(self, process: Any, sink: deque[str]) -> None:
        super().__init__(daemon=True, name="desktop-app-log")
        self._process = process
        self._sink = sink

    def run(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            self._sink.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))


class _ProcessHandle:
    """A launched application: Popen with stdout+stderr pumped into ``sink``."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path | None,
        env: dict[str, str] | None,
        sink: deque[str],
    ) -> None:
        try:
            self._process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            raise DeviceConnectionError(
                f"Application executable not found: {argv[0]!r}.",
                remediation="Check devices.<name>.command (and cwd) point at the built app.",
            ) from exc
        except OSError as exc:
            raise DeviceConnectionError(
                f"Unable to launch {argv[0]!r}: {exc}",
                remediation="Check the file is executable and the platform matches.",
            ) from exc
        self._pump = _LogPump(self._process, sink)
        self._pump.start()

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def running(self) -> bool:
        return self._process.poll() is None

    def stop(self, timeout: float) -> None:
        if self.running:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=timeout)
        self._pump.join(timeout=2.0)
        # Only close the stream once the pump has actually exited: if join()
        # timed out (e.g. a child that inherited the pipe to its own
        # grandchildren keeps it open) the pump thread may still be blocked
        # in stream.readline(), and closing the stream from this thread
        # while that read is in flight would race with it.
        if not self._pump.is_alive() and self._process.stdout is not None:
            self._process.stdout.close()
