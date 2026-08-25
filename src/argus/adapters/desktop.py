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
_RESET_TIMEOUT = 30.0
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


def _host_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _display_remediation() -> str:
    if sys.platform == "darwin":
        return (
            "Grant your terminal Screen Recording and Accessibility permission "
            "(System Settings > Privacy & Security), then re-run."
        )
    if sys.platform == "win32":
        return (
            "Run from an interactive desktop session (not a service), "
            "at the app's integrity level."
        )
    return (
        "Desktop devices need an X11 display: set DISPLAY (or run under Xvfb / XWayland) "
        "and install scrot + python3-tk for pyautogui."
    )


def _validate_region(name: str, region: tuple[int, int, int, int] | None) -> None:
    if region is not None and (region[2] <= 0 or region[3] <= 0):
        raise ConfigurationError(
            f"Desktop device {name!r}: region width and height must be positive.",
            remediation="Example: region: [0, 0, 1920, 1080]",
        )


class DesktopAdapter(Device):
    """Controls a native desktop application through pyautogui and a subprocess."""

    def __init__(
        self,
        name: str,
        *,
        command: str,
        args: Sequence[str] = (),
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        startup_wait: float = 0.0,
        stop_timeout: float = 5.0,
        reset_command: str | None = None,
        region: tuple[int, int, int, int] | None = None,
        platform: str | None = None,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        super().__init__(name)
        self._command = command
        self._args = list(args)
        self._cwd = cwd
        self._env = dict(env) if env else None
        self._startup_wait = float(startup_wait)
        self._stop_timeout = float(stop_timeout)
        self._reset_command = reset_command
        self._region = region
        _validate_region(name, region)
        self._platform = platform or _host_platform()
        self._backend_factory: BackendFactory = backend_factory or _pyautogui_backend
        self._backend: DesktopBackend | None = None
        self._process: _ProcessHandle | None = None
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._ratio: float | None = None
        self._screen_info: ScreenInfo | None = None
        self._log = get_logger("argus.desktop", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> DesktopAdapter:
        options: dict[str, Any] = config.options
        command = options.get("command")
        if not command:
            raise ConfigurationError(
                f"Desktop device {name!r} needs a command.",
                remediation="Set devices.<name>.command to the application executable.",
            )
        region: tuple[int, int, int, int] | None = None
        raw_region = options.get("region")
        if raw_region is not None:
            if not isinstance(raw_region, list | tuple) or len(raw_region) != 4:
                raise ConfigurationError(
                    f"Desktop device {name!r}: region must be [x, y, width, height].",
                    remediation="Example: region: [0, 0, 1920, 1080]",
                )
            region = (
                int(raw_region[0]),
                int(raw_region[1]),
                int(raw_region[2]),
                int(raw_region[3]),
            )
        env = options.get("env")
        return cls(
            name,
            command=str(command),
            args=[str(a) for a in options.get("args", [])],
            cwd=options.get("cwd"),
            env={str(k): str(v) for k, v in env.items()} if env else None,
            startup_wait=parse_duration(options.get("startup_wait", "0s")),
            stop_timeout=parse_duration(options.get("stop_timeout", "5s")),
            reset_command=options.get("reset_command"),
            region=region,
            platform=config.effective_platform if config.platform else None,
        )

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=True,
            supports_tap=True,
            supports_swipe=True,
            supports_long_press=True,
            supports_drag=True,
            supports_multi_touch=False,
            supports_keyboard=True,
            supports_app_lifecycle=True,
            supports_logs=True,
        )

    @property
    def platform(self) -> str:
        return self._platform

    # -- connection -------------------------------------------------------------------------

    def _require_backend(self) -> DesktopBackend:
        if self._backend is None:
            raise DeviceConnectionError(
                f"Desktop device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        return self._backend

    def _probe(self) -> tuple[DesktopBackend, tuple[int, int]]:
        """Check the display is reachable WITHOUT mutating connection state."""
        backend = self._backend_factory()
        try:
            width, height = backend.size()
        except Exception as exc:  # noqa: BLE001 - pyautogui raises assorted backend errors
            raise DeviceConnectionError(
                f"Desktop device {self.name!r}: no display available ({exc}).",
                remediation=_display_remediation(),
            ) from exc
        return backend, (int(width), int(height))

    def connect(self) -> None:
        backend, size = self._probe()
        self._backend = backend
        self._ratio = None
        self._screen_info = None
        if sys.platform == "darwin":
            image = self._grab()
            if image.getbbox() is None:
                # PIL's getbbox() is None for an all-black image. The app has not been
                # launched yet at connect time, so a fully black desktop capture here
                # means macOS is blocking screen recording, not that the app is
                # legitimately rendering a black window.
                self._backend = None
                raise DeviceConnectionError(
                    "Desktop screenshot is entirely black; macOS is blocking screen "
                    "capture.",
                    remediation="Grant your terminal Screen Recording permission "
                    "(System Settings > Privacy & Security > Screen Recording), then "
                    "restart the terminal.",
                )
            logical_width, _logical_height = size
            self._ratio = image.width / logical_width if logical_width > 0 else 1.0

    def disconnect(self) -> None:
        if self._process is not None and self._process.running:
            self.stop_application()
        self._backend = None

    def is_available(self) -> bool:
        try:
            self._probe()
        except DeviceConnectionError:
            return False
        return True

    def health_check(self) -> HealthCheckResult:
        try:
            _backend, (width, height) = self._probe()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(str(exc))
        return HealthCheckResult.ok(
            "Desktop display available",
            screen=f"{width}x{height}",
            platform=self._platform,
            app_running=self.is_application_running(),
        )

    # -- application lifecycle ----------------------------------------------------------

    def is_application_running(self) -> bool:
        return self._process is not None and self._process.running

    def start_application(self) -> None:
        self._require_backend()
        if self._process is not None and self._process.running:
            self.stop_application()
        env = {**os.environ, **self._env} if self._env else None
        self._logs = deque(maxlen=_MAX_LOG_LINES)
        self._process = _ProcessHandle(
            [self._command, *self._args], cwd=self._cwd, env=env, sink=self._logs
        )
        self._log.info("Launched %s (pid %d)", self._command, self._process.pid)
        if self._startup_wait > 0:
            time.sleep(self._startup_wait)

    def stop_application(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.stop(timeout=self._stop_timeout)

    def reset_application(self) -> None:
        self.stop_application()
        if self._reset_command:
            timeout = max(self._stop_timeout, _RESET_TIMEOUT)
            try:
                completed = subprocess.run(
                    self._reset_command,
                    shell=True,
                    cwd=self._cwd,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise DeviceConnectionError(
                    f"reset_command timed out after {timeout}s.",
                    remediation="Check devices.<name>.reset_command exits on its own.",
                ) from exc
            if completed.returncode != 0:
                stderr = completed.stderr.decode(errors="replace").strip()
                raise DeviceConnectionError(
                    f"reset_command failed (exit {completed.returncode}): {stderr}",
                    remediation="Check devices.<name>.reset_command runs cleanly by hand.",
                )
        self.start_application()

    # -- observation -------------------------------------------------------------------------

    def _grab(self) -> Image:
        backend = self._require_backend()
        try:
            image = backend.screenshot()
        except Exception as exc:  # noqa: BLE001 - pyautogui/backend specific errors
            raise ScreenshotError(
                f"Desktop screenshot failed: {exc}", remediation=_display_remediation()
            ) from exc
        return image.convert("RGB")

    def screenshot(self) -> Image:
        image = self._grab()
        if self._region is None:
            return image
        x, y, width, height = self._region
        if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
            raise ScreenshotError(
                f"Desktop device {self.name!r}: region {self._region} exceeds the "
                f"{image.width}x{image.height} screenshot.",
                remediation="Adjust devices.<name>.region to lie within the screen.",
            )
        return image.crop((x, y, x + width, y + height))

    def _pixel_ratio(self) -> float:
        if self._ratio is None:
            logical_width, _ = self._require_backend().size()
            full = self._grab()
            self._ratio = full.width / logical_width if logical_width > 0 else 1.0
        return self._ratio

    def get_screen_info(self) -> ScreenInfo:
        if self._screen_info is None:
            ratio = self._pixel_ratio()
            image = self.screenshot()
            self._screen_info = ScreenInfo(width=image.width, height=image.height, scale=ratio)
        return self._screen_info

    def _to_logical(self, point: Point) -> tuple[float, float]:
        """Screenshot pixel (inside ``region`` if set) -> pyautogui logical coordinate."""
        ratio = self._pixel_ratio()
        offset_x, offset_y = (self._region[0], self._region[1]) if self._region else (0, 0)
        return ((point[0] + offset_x) / ratio, (point[1] + offset_y) / ratio)

    def get_logs(self, lines: int = 200) -> str:
        if lines <= 0:
            return ""
        return "\n".join(list(self._logs)[-lines:])
