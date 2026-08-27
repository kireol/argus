"""Desktop application adapter (Windows / Linux / macOS).

Launches the application under test as a local process, screenshots the
display and drives it with mouse and keyboard through ``pyautogui``
(optional dependency: ``pip install "argus[desktop]"``). The process's
stdout/stderr become the device logs. Coordinates in tests are screenshot
pixels; the adapter converts them to logical (HiDPI-scaled) coordinates.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image as PILImage
from PIL.Image import Image, Resampling

from argus.adapters.base import Device, DeviceCapabilities, Point
from argus.adapters.runtime_metrics import parse_ps_etime
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

# Android-style names -> pyautogui key names; anything else passes through lower-cased.
_KEY_MAP = {
    "ENTER": "enter",
    "DPAD_CENTER": "enter",
    "BACK": "escape",
    "ESCAPE": "escape",
    "TAB": "tab",
    "SPACE": "space",
    "DEL": "backspace",
    "BACKSPACE": "backspace",
    "DPAD_UP": "up",
    "DPAD_DOWN": "down",
    "DPAD_LEFT": "left",
    "DPAD_RIGHT": "right",
    "UP": "up",
    "DOWN": "down",
    "LEFT": "left",
    "RIGHT": "right",
    "HOME": "home",
    "END": "end",
    "PAGE_UP": "pageup",
    "PAGE_DOWN": "pagedown",
    "PLUS": "+",
    "MINUS": "-",
}
_MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt",
              "shift": "shift", "cmd": "command", "command": "command", "win": "win",
              "super": "win", "meta": "command"}


def _map_key(name: str) -> str:
    stripped = name.removeprefix("KEYCODE_")
    if len(stripped) == 1:
        return stripped.lower()
    return _KEY_MAP.get(stripped.upper(), stripped.lower())


def _chord(key: str) -> list[str] | None:
    """``Ctrl+Shift+t`` -> ['ctrl', 'shift', 't']; None when ``key`` is not a chord."""
    if "+" not in key or key == "+":
        return None
    # A trailing '+' means the literal '+' key: "Ctrl+Plus" is spelled "Ctrl++" too.
    # Splitting on "+" then leaves empty parts (one per consecutive "+"), which are
    # dropped; the literal key is appended back once the split is done.
    literal_plus = key.endswith("+")
    parts = [p for p in key.split("+") if p != ""]
    if literal_plus:
        parts.append("+")
    if len(parts) < 2:
        return None
    return [_MODIFIERS.get(p.lower(), _map_key(p)) for p in parts]


def _host_uptime_seconds() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, IndexError, ValueError):
        pass
    if sys.platform == "darwin":
        try:
            raw = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        marker = "sec ="
        index = raw.find(marker)
        if index < 0:
            marker = "sec="
            index = raw.find(marker)
        if index < 0:
            return None
        digits: list[str] = []
        for char in raw[index + len(marker) :]:
            if char.isdigit():
                digits.append(char)
            elif digits:
                break
        if not digits:
            return None
        return max(0.0, time.time() - int("".join(digits)))
    if sys.platform == "win32":
        try:
            import ctypes

            return float(ctypes.windll.kernel32.GetTickCount64()) / 1000.0
        except (AttributeError, OSError, ValueError):
            return None
    return None


class DesktopBackend(Protocol):
    """The slice of pyautogui the adapter relies on (fakeable)."""

    KEYBOARD_KEYS: Any

    def size(self) -> tuple[int, int]: ...
    def screenshot(self) -> Image: ...
    def click(self, x: float, y: float) -> None: ...
    def mouseDown(self, x: float, y: float) -> None: ...  # noqa: N802 - pyautogui names
    def mouseUp(self) -> None: ...  # noqa: N802
    def moveTo(self, x: float, y: float, duration: float = 0.0) -> None: ...  # noqa: N802
    def dragTo(  # noqa: N802
        self, x: float, y: float, duration: float = 0.0, *, mouseDownUp: bool = True  # noqa: N803
    ) -> None: ...
    def press(self, key: str) -> None: ...
    def hotkey(self, *keys: str) -> None: ...


BackendFactory = Callable[[], DesktopBackend]


def _pyautogui_backend() -> DesktopBackend:
    try:
        import pyautogui

        pyautogui.FAILSAFE = False  # a corner-of-screen mouse must not abort a test run
        pyautogui.PAUSE = 0  # gestures pace themselves; no per-call sleep
    except ImportError as exc:
        raise DeviceConnectionError(
            "pyautogui is not installed (required for desktop devices).",
            remediation=f"Install desktop support: {_INSTALL}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - e.g. the X11 backend raising at import time
        raise DeviceConnectionError(
            f"pyautogui failed to initialise: {exc}",
            remediation=_display_remediation(),
        ) from exc
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


_DEFAULT_TITLE_BAR_PT = 28


@dataclass(frozen=True)
class _MacWindow:
    """On-screen window in logical points (top-left origin)."""

    bounds: tuple[int, int, int, int]
    window_id: int | None = None
    owner_pid: int | None = None


def _osa_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _osascript_window_bounds_script(title: str, process_name: str | None = None) -> str:
    """AppleScript that returns ``x,y,w,h`` for a titled window.

    Line continuation must not use a backslash — osascript treats ``\\`` as an
    unknown token and the lookup then fails silently.
    """
    escaped_title = _osa_escape(title)
    result = (
        '(item 1 of pos as text) & "," & (item 2 of pos as text) & "," & '
        '(item 1 of sz as text) & "," & (item 2 of sz as text)'
    )
    if process_name:
        escaped_proc = _osa_escape(process_name)
        return f'''
tell application "System Events"
  if not (exists process "{escaped_proc}") then return ""
  tell process "{escaped_proc}"
    repeat with w in windows
      if name of w is "{escaped_title}" then
        set pos to position of w
        set sz to size of w
        return {result}
      end if
    end repeat
  end tell
end tell
'''
    return f'''
tell application "System Events"
  repeat with p in processes
    try
      repeat with w in windows of p
        if name of w is "{escaped_title}" then
          set pos to position of w
          set sz to size of w
          return {result}
        end if
      end repeat
    end try
  end repeat
end tell
'''


def _parse_bounds_csv(line: str) -> tuple[int, int, int, int] | None:
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != 4:
        return None
    try:
        x, y, width, height = (int(float(p)) for p in parts)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return (x, y, width, height)


def _quartz_window_info(title: str, process_name: str | None = None) -> _MacWindow | None:
    """Locate a window via CGWindowList (Screen Recording; any display)."""
    try:
        from Quartz import (  # type: ignore[import-untyped]
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )
    except ImportError:
        return None
    info = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
    for window in info:
        name = window.get("kCGWindowName") or ""
        if name != title:
            continue
        owner = window.get("kCGWindowOwnerName") or ""
        if process_name and owner != process_name:
            continue
        raw = window.get("kCGWindowBounds") or {}
        try:
            x, y = int(raw["X"]), int(raw["Y"])
            width, height = int(raw["Width"]), int(raw["Height"])
        except (KeyError, TypeError, ValueError):
            continue
        if width < 50 or height < 50:
            continue
        window_id = window.get("kCGWindowNumber")
        try:
            parsed_id = int(window_id) if window_id is not None else None
        except (TypeError, ValueError):
            parsed_id = None
        owner_pid = window.get("kCGWindowOwnerPID")
        try:
            parsed_pid = int(owner_pid) if owner_pid is not None else None
        except (TypeError, ValueError):
            parsed_pid = None
        return _MacWindow(
            bounds=(x, y, width, height), window_id=parsed_id, owner_pid=parsed_pid
        )
    return None


def _osascript_window_bounds(
    title: str, process_name: str | None = None
) -> tuple[int, int, int, int] | None:
    """System Events lookup (Accessibility permission)."""
    script = _osascript_window_bounds_script(title, process_name)
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (completed.stdout or "").strip()
    if not line or completed.returncode != 0:
        return None
    return _parse_bounds_csv(line)


def _macos_window_info(title: str, process_name: str | None = None) -> _MacWindow | None:
    """Locate ``title`` on any display. Quartz first, then System Events."""
    if sys.platform != "darwin":
        return None
    found = _quartz_window_info(title, process_name)
    if found is not None:
        return found
    bounds = _osascript_window_bounds(title, process_name)
    if bounds is None and process_name is not None:
        bounds = _osascript_window_bounds(title, None)
    if bounds is None:
        return None
    return _MacWindow(bounds=bounds)


def _macos_window_bounds(
    title: str, process_name: str | None = None
) -> tuple[int, int, int, int] | None:
    """Logical-point (x, y, w, h) of an on-screen window, or None if not found."""
    found = _macos_window_info(title, process_name)
    return None if found is None else found.bounds


def _macos_capture_window(window: _MacWindow) -> Image:
    """Capture one window, including when it sits on a secondary display."""
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        if window.window_id is not None:
            argv = ["screencapture", "-x", "-o", "-l", str(window.window_id), path]
        else:
            x, y, width, height = window.bounds
            argv = ["screencapture", "-x", f"-R{x},{y},{width},{height}", path]
        try:
            completed = subprocess.run(argv, capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScreenshotError(
                f"Desktop screenshot failed: {exc}", remediation=_display_remediation()
            ) from exc
        if completed.returncode != 0 or not os.path.getsize(path):
            detail = (completed.stderr or completed.stdout or b"").decode(errors="replace").strip()
            raise ScreenshotError(
                f"Desktop screenshot failed: {detail or 'screencapture returned empty image'}",
                remediation=_display_remediation(),
            )
        with PILImage.open(path) as captured:
            return captured.convert("RGB")
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _finish_window_image(
    image: Image,
    *,
    logical_window: tuple[int, int],
    title_bar_pt: int,
    content_size: tuple[int, int] | None,
) -> Image:
    """Drop the title bar from a window-sized capture, optionally resize."""
    logical_w, _logical_h = logical_window
    scale = image.width / logical_w if logical_w > 0 else 1.0
    title_px = int(round(max(0, title_bar_pt) * scale))
    if 0 < title_px < image.height:
        image = image.crop((0, title_px, image.width, image.height))
    if content_size is not None and image.size != content_size:
        image = image.resize(content_size, Resampling.LANCZOS)
    return image.convert("RGB")


def _extract_window(
    image: Image,
    *,
    logical_size: tuple[int, int],
    bounds: tuple[int, int, int, int],
    title_bar_pt: int,
    content_size: tuple[int, int] | None,
) -> Image:
    """Crop ``image`` to a window, drop the title bar, optionally resize."""
    logical_w, _logical_h = logical_size
    scale = image.width / logical_w if logical_w > 0 else 1.0
    x, y, width, height = bounds
    left = max(0, int(round(x * scale)))
    top = max(0, int(round(y * scale)))
    right = min(image.width, int(round((x + width) * scale)))
    bottom = min(image.height, int(round((y + height) * scale)))
    if right <= left or bottom <= top:
        raise ScreenshotError(
            f"Window bounds {bounds} do not intersect the "
            f"{image.width}x{image.height} screenshot.",
            remediation="The window is off the captured display; Argus captures it "
            "directly on macOS. On other platforms, put the window on the primary display.",
        )
    crop = image.crop((left, top, right, bottom))
    return _finish_window_image(
        crop,
        logical_window=(width, height),
        title_bar_pt=title_bar_pt,
        content_size=content_size,
    )


def _parse_wh(name: str, field: str, raw: Any) -> tuple[int, int]:
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise ConfigurationError(
            f"Desktop device {name!r}: {field} must be [width, height].",
            remediation="Example: content_size: [1183, 624]",
        )
    try:
        width, height = int(raw[0]), int(raw[1])
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"Desktop device {name!r}: {field} must be two integers [width, height].",
            remediation="Example: content_size: [1183, 624]",
        ) from exc
    if width <= 0 or height <= 0:
        raise ConfigurationError(
            f"Desktop device {name!r}: {field} width and height must be positive.",
            remediation="Example: content_size: [1183, 624]",
        )
    return (width, height)


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
        window_title: str | None = None,
        content_size: tuple[int, int] | None = None,
        title_bar_height: int = _DEFAULT_TITLE_BAR_PT,
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
        self._window_title = window_title.strip() if window_title else None
        self._content_size = content_size
        self._title_bar_height = int(title_bar_height)
        self._platform = platform or _host_platform()
        self._backend_factory: BackendFactory = backend_factory or _pyautogui_backend
        self._backend: DesktopBackend | None = None
        self._process: _ProcessHandle | None = None
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._ratio: float | None = None
        self._screen_info: ScreenInfo | None = None
        self._window: _MacWindow | None = None
        self._log = get_logger("argus.desktop", device=name)

    def _process_name(self) -> str:
        return Path(self._command).name

    def _lookup_window(self) -> _MacWindow | None:
        if not self._window_title:
            return None
        found = _macos_window_info(self._window_title, process_name=self._process_name())
        self._window = found
        return found

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
            try:
                region = (
                    int(raw_region[0]),
                    int(raw_region[1]),
                    int(raw_region[2]),
                    int(raw_region[3]),
                )
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    f"Desktop device {name!r}: region must be four integers "
                    "[x, y, width, height].",
                    remediation="Example: region: [0, 0, 1920, 1080]",
                ) from exc
        env = options.get("env")
        raw_title = options.get("window_title")
        window_title = str(raw_title).strip() if raw_title else None
        content_size = None
        if options.get("content_size") is not None:
            content_size = _parse_wh(name, "content_size", options.get("content_size"))
        title_bar_height = _DEFAULT_TITLE_BAR_PT
        if options.get("title_bar_height") is not None:
            try:
                title_bar_height = int(options["title_bar_height"])
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    f"Desktop device {name!r}: title_bar_height must be an integer.",
                    remediation="Example: title_bar_height: 28",
                ) from exc
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
            window_title=window_title,
            content_size=content_size,
            title_bar_height=title_bar_height,
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
        try:
            backend = self._backend_factory()
            width, height = backend.size()
        except DeviceConnectionError:
            raise
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
            try:
                image = self._grab()
            except ScreenshotError as exc:
                self._backend = None
                raise DeviceConnectionError(
                    f"Desktop device {self.name!r}: screenshot probe failed "
                    f"({exc.message}).",
                    remediation=_display_remediation(),
                ) from exc
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
        self._ensure_window()

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
        if self._process is not None and self._process.running:
            return True
        return bool(self._window_title and self._lookup_window() is not None)

    def _ensure_window(self) -> None:
        """Launch the app when ``window_title`` is set and that window is missing."""
        if not self._window_title:
            return
        if self._lookup_window() is not None:
            return
        self.start_application()
        deadline = time.monotonic() + max(self._startup_wait, 15.0)
        while time.monotonic() < deadline:
            if self._lookup_window() is not None:
                return
            time.sleep(0.2)
        raise DeviceConnectionError(
            f"Desktop device {self.name!r}: window {self._window_title!r} did not appear.",
            remediation="Start the app, or check devices.<name>.command and "
            "window_title. Grant Screen Recording permission so Argus can see "
            "the window (Accessibility is also used as a fallback).",
        )

    def start_application(self) -> None:
        self._require_backend()
        if self._window_title and self._lookup_window() is not None:
            self._log.info(
                "Window %r already present; not launching a second instance",
                self._window_title,
            )
            return
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
        process = self._process
        if process is None:
            return
        try:
            process.stop(timeout=self._stop_timeout)
        finally:
            if not process.running:
                self._process = None

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
                stdout = completed.stdout.decode(errors="replace").strip()
                detail = (stderr or stdout)[-500:]
                raise DeviceConnectionError(
                    f"reset_command failed (exit {completed.returncode}): {detail}",
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
        if self._window_title:
            found = self._lookup_window()
            if found is None:
                raise ScreenshotError(
                    f"Desktop device {self.name!r}: window {self._window_title!r} "
                    "not found.",
                    remediation="Start the app and grant Screen Recording (and "
                    "Accessibility) to the terminal running Argus.",
                )
            if sys.platform == "darwin":
                image = _finish_window_image(
                    _macos_capture_window(found),
                    logical_window=(found.bounds[2], found.bounds[3]),
                    title_bar_pt=self._title_bar_height,
                    content_size=self._content_size,
                )
            else:
                image = _extract_window(
                    image,
                    logical_size=self._require_backend().size(),
                    bounds=found.bounds,
                    title_bar_pt=self._title_bar_height,
                    content_size=self._content_size,
                )
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
        x, y = float(point[0]), float(point[1])
        if self._region is not None:
            x += self._region[0]
            y += self._region[1]
        if self._window_title and self._window is not None:
            win_x, win_y, win_w, win_h = self._window.bounds
            title = max(0, self._title_bar_height)
            content_w = max(1, win_w)
            content_h = max(1, win_h - title)
            if self._content_size is not None:
                img_w, img_h = self._content_size
            else:
                ratio = self._pixel_ratio()
                img_w, img_h = content_w * ratio, content_h * ratio
            return (
                win_x + x * content_w / img_w,
                win_y + title + y * content_h / img_h,
            )
        ratio = self._pixel_ratio()
        return (x / ratio, y / ratio)

    def get_logs(self, lines: int = 200) -> str:
        if lines <= 0:
            return ""
        return "\n".join(list(self._logs)[-lines:])

    def _app_pid(self) -> int | None:
        if self._process is not None and self._process.running:
            return self._process.pid
        window = self._window
        if window is not None and window.owner_pid:
            return window.owner_pid
        return None

    def sample_metrics(self) -> dict[str, float]:
        sample: dict[str, float] = {}
        try:
            load1, load5, load15 = os.getloadavg()
            sample["system_load_1m"] = float(load1)
            sample["system_load_5m"] = float(load5)
            sample["system_load_15m"] = float(load15)
        except (AttributeError, OSError):
            pass
        uptime = _host_uptime_seconds()
        if uptime is not None:
            sample["system_uptime_s"] = uptime
        pid = self._app_pid()
        if pid is None:
            return sample
        try:
            completed = subprocess.run(
                ["ps", "-o", "etime=,rss=,pcpu=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return sample
        parts = completed.stdout.strip().split()
        if len(parts) >= 3:
            elapsed = parse_ps_etime(parts[0])
            if elapsed is not None:
                sample["app_uptime_s"] = elapsed
            try:
                sample["app_rss_mb"] = float(parts[1]) / 1024.0
                sample["app_cpu_percent"] = float(parts[2])
            except ValueError:
                pass
        elif len(parts) >= 2:
            try:
                sample["app_rss_mb"] = float(parts[0]) / 1024.0
                sample["app_cpu_percent"] = float(parts[1])
            except ValueError:
                pass
        return sample

    # -- input --------------------------------------------------------------------------------

    def _no_touch(self, operation: str) -> DeviceCapabilityError:
        return DeviceCapabilityError(
            f"Desktop device {self.name!r} cannot {operation}: desktop has no touch injection.",
            remediation="Zoom with the keyboard instead, e.g. device.key: Ctrl+Plus "
            "(Cmd+Plus on macOS).",
        )

    def tap(self, x: int, y: int) -> None:
        backend = self._require_backend()
        backend.click(*self._to_logical((x, y)))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        backend = self._require_backend()
        backend.mouseDown(*self._to_logical((x1, y1)))
        backend.dragTo(
            *self._to_logical((x2, y2)), duration=duration_ms / 1000, mouseDownUp=False
        )
        backend.mouseUp()

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        backend = self._require_backend()
        backend.mouseDown(*self._to_logical((x, y)))
        time.sleep(duration_ms / 1000)
        backend.mouseUp()

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, hold_ms: int = 500, duration_ms: int = 500
    ) -> None:
        backend = self._require_backend()
        backend.mouseDown(*self._to_logical((x1, y1)))
        time.sleep(hold_ms / 1000)
        backend.dragTo(
            *self._to_logical((x2, y2)), duration=duration_ms / 1000, mouseDownUp=False
        )
        backend.mouseUp()

    def multi_touch(self, fingers: Sequence[Sequence[Point]], duration_ms: int = 500) -> None:
        raise self._no_touch("multi_touch")

    def pinch(
        self, cx: int, cy: int, start_distance: int, end_distance: int, duration_ms: int = 500
    ) -> None:
        raise self._no_touch("pinch")

    def _validate_key_name(self, key: str, name: str, valid: Any) -> None:
        if valid is not None and name not in valid:
            raise DeviceCapabilityError(
                f"Desktop device {self.name!r} cannot press {key!r}: {name!r} is not a "
                "pyautogui key name.",
                remediation="Use a single character, a chord like Ctrl+Shift+t, or a "
                "pyautogui key name (enter, escape, up, f5, pagedown, ...).",
            )

    def press_key(self, key: str) -> None:
        backend = self._require_backend()
        valid = getattr(backend, "KEYBOARD_KEYS", None)
        chord = _chord(key)
        if chord is not None:
            for name in chord:
                self._validate_key_name(key, name, valid)
            backend.hotkey(*chord)
        else:
            name = _map_key(key)
            self._validate_key_name(key, name, valid)
            backend.press(name)
