"""Android device adapter (ADB-based, no Android Studio / Appium dependency).

Talks to emulators or physical devices through the ``adb`` binary using
``subprocess`` — portable across Windows/macOS/Linux.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities, Point, interpolate_path
from argus.config.models import DeviceConfig
from argus.exceptions import (
    ConfigurationError,
    DeviceCapabilityError,
    DeviceConnectionError,
    ScreenshotError,
)
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo

_DEFAULT_TIMEOUT = 30.0
_DRAGANDDROP_MIN_SDK = 30  # `input draganddrop` arrived in Android 11
_TOUCH_FRAME_MS = 20  # sendevent frames are shell round-trips; 50 Hz is plenty

# Linux input event codes used by the multi-touch (protocol B) stream.
_EV_SYN, _EV_KEY, _EV_ABS = 0, 1, 3
_SYN_REPORT = 0
_BTN_TOUCH = 330
_ABS_MT_SLOT, _ABS_MT_POSITION_X, _ABS_MT_POSITION_Y, _ABS_MT_TRACKING_ID = 47, 53, 54, 57

_GETEVENT_DEVICE = re.compile(r"^add device \d+: (?P<path>/dev/input/\S+)")
_GETEVENT_AXIS = re.compile(
    r"^\s*(?:ABS \(0003\):)?\s*(?P<code>003[56])\s*:.*?min (?P<min>-?\d+), max (?P<max>-?\d+)"
)


@dataclass(frozen=True)
class _Touchscreen:
    """An evdev touchscreen and its ABS_MT_POSITION_{X,Y} ranges (None = unknown)."""

    path: str
    x_range: tuple[int, int] | None = None
    y_range: tuple[int, int] | None = None


def _parse_touchscreens(getevent_output: str) -> list[_Touchscreen]:
    """Devices from ``getevent -p`` that expose multi-touch position axes."""
    found: list[_Touchscreen] = []
    path: str | None = None
    axes: dict[str, tuple[int, int]] = {}

    def flush() -> None:
        if path and "0035" in axes and "0036" in axes:
            found.append(_Touchscreen(path, axes["0035"], axes["0036"]))

    for line in getevent_output.splitlines():
        device = _GETEVENT_DEVICE.match(line)
        if device:
            flush()
            path, axes = device.group("path"), {}
            continue
        axis = _GETEVENT_AXIS.match(line)
        if axis:
            axes[axis.group("code")] = (int(axis.group("min")), int(axis.group("max")))
    flush()
    return found


class AndroidAdapter(Device):
    """Controls an Android device/emulator through ADB."""

    def __init__(
        self,
        name: str,
        *,
        serial: str | None = None,
        app_package: str | None = None,
        app_activity: str | None = None,
        adb_path: str = "adb",
        command_timeout: float = _DEFAULT_TIMEOUT,
        input_device: str | None = None,
    ) -> None:
        super().__init__(name)
        self._serial = serial
        self._app_package = app_package
        self._app_activity = app_activity
        self._adb_path = adb_path
        self._timeout = command_timeout
        self._input_device = input_device
        self._connected = False
        self._screen_info: ScreenInfo | None = None
        self._sdk: int | None = None
        self._touchscreen: _Touchscreen | None = None
        self._log = get_logger("argus.android", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> AndroidAdapter:
        options: dict[str, Any] = config.options
        return cls(
            name,
            serial=options.get("serial"),
            app_package=options.get("app_package"),
            app_activity=options.get("app_activity"),
            adb_path=options.get("adb_path", "adb"),
            command_timeout=float(options.get("command_timeout", _DEFAULT_TIMEOUT)),
            input_device=options.get("input_device"),
        )

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=True,
            supports_tap=True,
            supports_swipe=True,
            supports_long_press=True,
            supports_drag=True,
            supports_multi_touch=True,
            supports_keyboard=True,
            supports_app_lifecycle=self._app_package is not None,
            supports_logs=True,
            supports_instrumentation=True,
        )

    @property
    def platform(self) -> str:
        return "android"

    # -- adb plumbing -------------------------------------------------------------

    def _adb(self, *args: str, binary: bool = False, timeout: float | None = None) -> bytes:
        if shutil.which(self._adb_path) is None:
            raise DeviceConnectionError(
                f"adb binary not found ({self._adb_path!r}).",
                remediation="Install Android platform-tools and ensure 'adb' is on "
                "PATH, or set devices.<name>.adb_path.",
            )
        command = [self._adb_path]
        if self._serial:
            command += ["-s", self._serial]
        command += list(args)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout or self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DeviceConnectionError(
                f"adb command timed out after {timeout or self._timeout}s: "
                f"{' '.join(args)}",
                remediation="Check the device/emulator is responsive.",
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode(errors="replace").strip()
            raise DeviceConnectionError(
                f"adb {' '.join(args)} failed ({completed.returncode}): {stderr}",
                remediation="Run 'adb devices' to check device state.",
            )
        return completed.stdout if binary else completed.stdout

    def _shell(self, *args: str) -> str:
        return self._adb("shell", *args).decode(errors="replace")

    # -- connection -----------------------------------------------------------------

    def connect(self) -> None:
        devices = self.list_devices(adb_path=self._adb_path)
        if not devices:
            raise DeviceConnectionError(
                "No Android devices/emulators detected.",
                remediation="Start an emulator or connect a device, then check "
                "'adb devices'.",
            )
        if self._serial is None:
            if len(devices) > 1:
                raise ConfigurationError(
                    f"Multiple Android devices detected ({', '.join(devices)}) "
                    "but no serial configured.",
                    remediation="Set devices.<name>.serial to choose one.",
                )
            self._serial = devices[0]
        elif self._serial not in devices:
            raise DeviceConnectionError(
                f"Android device {self._serial!r} not found. "
                f"Detected: {', '.join(devices)}.",
                remediation="Check ANDROID_SERIAL / devices.<name>.serial.",
            )
        self._connected = True
        self._log.info("Connected to Android device %s", self._serial)

    def disconnect(self) -> None:
        self._connected = False

    def is_available(self) -> bool:
        try:
            return bool(self.list_devices(adb_path=self._adb_path))
        except DeviceConnectionError:
            return False

    @staticmethod
    def list_devices(adb_path: str = "adb", timeout: float = 10.0) -> list[str]:
        """Detect connected devices/emulators via ``adb devices``."""
        if shutil.which(adb_path) is None:
            raise DeviceConnectionError(
                f"adb binary not found ({adb_path!r}).",
                remediation="Install Android platform-tools.",
            )
        completed = subprocess.run(
            [adb_path, "devices"], capture_output=True, timeout=timeout, check=False
        )
        devices: list[str] = []
        for line in completed.stdout.decode(errors="replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) == 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def health_check(self) -> HealthCheckResult:
        try:
            state = self._adb("get-state").decode().strip()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(str(exc))
        if state != "device":
            return HealthCheckResult.failed(
                f"Device state is {state!r} (expected 'device')", state=state
            )
        details: dict[str, Any] = {"serial": self._serial, "state": state}
        if self._app_package:
            details["app_running"] = self.is_application_running()
        return HealthCheckResult.ok("Android device responsive", **details)

    # -- application lifecycle ----------------------------------------------------------

    def _require_package(self) -> str:
        if not self._app_package:
            raise ConfigurationError(
                f"Device {self.name!r} has no app_package configured.",
                remediation="Set devices.<name>.app_package in configuration.",
            )
        return self._app_package

    def start_application(self) -> None:
        package = self._require_package()
        if self._app_activity:
            activity = self._app_activity
            if activity.startswith("."):
                activity = package + activity
            self._shell("am", "start", "-n", f"{package}/{activity}")
        else:
            self._shell(
                "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"
            )

    def stop_application(self) -> None:
        self._shell("am", "force-stop", self._require_package())

    def reset_application(self) -> None:
        # pm clear wipes app data and stops the process — a true reset.
        self._shell("pm", "clear", self._require_package())
        self.start_application()

    def is_application_running(self) -> bool:
        package = self._require_package()
        output = self._shell("pidof", package)
        return bool(output.strip())

    # -- observation -----------------------------------------------------------------------

    def screenshot(self) -> Image:
        try:
            png_bytes = self._adb("exec-out", "screencap", "-p", binary=True)
        except DeviceConnectionError as exc:
            raise ScreenshotError(
                f"Android screenshot failed: {exc.message}",
                remediation="Check the device is connected and unlocked.",
            ) from exc
        try:
            with PILImage.open(io.BytesIO(png_bytes)) as img:
                return img.convert("RGB")
        except OSError as exc:
            raise ScreenshotError(
                f"Android screenshot returned invalid PNG data ({len(png_bytes)} bytes).",
                remediation="Some devices corrupt binary output; check adb version.",
            ) from exc

    def get_screen_info(self) -> ScreenInfo:
        if self._screen_info is not None:
            return self._screen_info
        size_output = self._shell("wm", "size")
        # "Physical size: 1080x1920" (possibly with an Override line)
        line = size_output.strip().splitlines()[-1]
        dims = line.split(":")[-1].strip()
        width, height = (int(v) for v in dims.split("x"))
        dpi: float | None = None
        try:
            dpi_output = self._shell("wm", "density").strip().splitlines()[-1]
            dpi = float(dpi_output.split(":")[-1].strip())
        except (ValueError, IndexError, DeviceConnectionError):
            pass
        self._screen_info = ScreenInfo(width=width, height=height, dpi=dpi)
        return self._screen_info

    def get_logs(self, lines: int = 200) -> str:
        return self._adb("logcat", "-d", "-t", str(lines)).decode(errors="replace")

    # -- input ---------------------------------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        self._shell("input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._shell(
            "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)
        )

    def press_key(self, key: str) -> None:
        keycode = key if key.startswith("KEYCODE_") else f"KEYCODE_{key.upper()}"
        self._shell("input", "keyevent", keycode)

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        # The standard ADB idiom: a swipe that goes nowhere is a press-and-hold.
        self._shell("input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms))

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, hold_ms: int = 500, duration_ms: int = 500
    ) -> None:
        if self._sdk_version() >= _DRAGANDDROP_MIN_SDK:
            # `input draganddrop` performs its own long-press before moving.
            self._shell(
                "input", "draganddrop", str(x1), str(y1), str(x2), str(y2), str(duration_ms)
            )
            return
        frames = _hold_then_move((x1, y1), (x2, y2), hold_ms, duration_ms)
        self._send_touch_frames(frames)

    def multi_touch(self, fingers: Sequence[Sequence[Point]], duration_ms: int = 500) -> None:
        steps = max(1, duration_ms // _TOUCH_FRAME_MS)
        frames = [
            [interpolate_path(path, step, steps) for path in fingers] for step in range(steps + 1)
        ]
        # Each frame is followed by one frame interval, except the last (then we lift).
        self._send_touch_frames(
            [(frame, 0 if i == steps else _TOUCH_FRAME_MS) for i, frame in enumerate(frames)]
        )

    # -- multi-touch via evdev ------------------------------------------------------------------

    def _sdk_version(self) -> int:
        if self._sdk is None:
            self._sdk = int(self._shell("getprop", "ro.build.version.sdk").strip() or 0)
        return self._sdk

    def _find_touchscreen(self) -> _Touchscreen:
        if self._touchscreen is not None:
            return self._touchscreen
        if self._input_device:
            # Explicit override: trust it and send screen pixels verbatim.
            self._touchscreen = _Touchscreen(self._input_device)
            return self._touchscreen
        screens = _parse_touchscreens(self._shell("getevent", "-p"))
        if not screens:
            raise DeviceCapabilityError(
                f"Device {self.name!r}: no multi-touch touchscreen found in 'getevent -p'.",
                remediation="Set devices.<name>.input_device to the /dev/input/eventN "
                "path of the touchscreen.",
            )
        self._touchscreen = screens[0]
        return self._touchscreen

    def _to_touch_units(self, point: Point, screen: _Touchscreen) -> Point:
        if screen.x_range is None or screen.y_range is None:
            return point
        width, height = self.get_screen_size()
        return (
            _scale(point[0], width, screen.x_range),
            _scale(point[1], height, screen.y_range),
        )

    def _send_touch_frames(self, frames: Sequence[tuple[Sequence[Point], int]]) -> None:
        """Replay ``frames`` — (finger positions, delay-after-ms) — as protocol-B events.

        Every finger in a frame is a slot; a finger touches down in the first
        frame and lifts after the last. Emitted as one ``sh -c`` script so the
        gesture is not paced by adb round-trips.
        """
        screen = self._find_touchscreen()
        dev = screen.path
        lines = ["set -e"]

        def ev(type_: int, code: int, value: int) -> None:
            lines.append(f"sendevent {dev} {type_} {code} {value}")

        first, *rest = frames
        for slot, point in enumerate(first[0]):
            x, y = self._to_touch_units(point, screen)
            ev(_EV_ABS, _ABS_MT_SLOT, slot)
            ev(_EV_ABS, _ABS_MT_TRACKING_ID, slot)
            if slot == 0:
                ev(_EV_KEY, _BTN_TOUCH, 1)
            ev(_EV_ABS, _ABS_MT_POSITION_X, x)
            ev(_EV_ABS, _ABS_MT_POSITION_Y, y)
        ev(_EV_SYN, _SYN_REPORT, 0)
        previous = list(first[0])
        lines.append(f"sleep {first[1] / 1000:g}")
        for points, delay_ms in rest:
            for slot, point in enumerate(points):
                if point == previous[slot]:
                    continue
                x, y = self._to_touch_units(point, screen)
                ev(_EV_ABS, _ABS_MT_SLOT, slot)
                ev(_EV_ABS, _ABS_MT_POSITION_X, x)
                ev(_EV_ABS, _ABS_MT_POSITION_Y, y)
            ev(_EV_SYN, _SYN_REPORT, 0)
            previous = list(points)
            if delay_ms:
                lines.append(f"sleep {delay_ms / 1000:g}")
        for slot in range(len(previous)):
            ev(_EV_ABS, _ABS_MT_SLOT, slot)
            ev(_EV_ABS, _ABS_MT_TRACKING_ID, -1)
        ev(_EV_KEY, _BTN_TOUCH, 0)
        ev(_EV_SYN, _SYN_REPORT, 0)
        try:
            self._shell("sh", "-c", "\n".join(lines))
        except DeviceConnectionError as exc:
            if "Permission denied" not in exc.message:
                raise
            raise DeviceCapabilityError(
                f"Device {self.name!r}: cannot write touch events to {dev} "
                f"({exc.message}).",
                remediation="Multi-touch needs a writable /dev/input device: use an "
                "emulator, a rooted device ('adb root'), or a build with adb shell "
                "in the 'input' group.",
            ) from exc


def _scale(value: int, screen_extent: int, axis_range: tuple[int, int]) -> int:
    lo, hi = axis_range
    if screen_extent <= 1 or hi == lo:
        return value
    return round(lo + value * (hi - lo) / (screen_extent - 1))


def _hold_then_move(
    start: Point, end: Point, hold_ms: int, duration_ms: int
) -> list[tuple[list[Point], int]]:
    """Frames for a drag: sit at ``start`` for ``hold_ms``, then glide to ``end``."""
    steps = max(1, duration_ms // _TOUCH_FRAME_MS)
    frames: list[tuple[list[Point], int]] = [([start], hold_ms)]
    for step in range(1, steps + 1):
        delay = 0 if step == steps else _TOUCH_FRAME_MS
        frames.append(([interpolate_path([start, end], step, steps)], delay))
    return frames
