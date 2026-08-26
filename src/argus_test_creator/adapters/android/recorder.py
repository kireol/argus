"""AndroidRecorder — screenshots and *controlled* input over ADB.

Honest capability report: touching the physical screen is **not** observed
in this version (parsing ``getevent`` reliably across devices is future
work). Instead the Creator sends taps/keys/text through ADB — every input is
therefore known exactly and recorded with before/after screenshots. The
``supports_input_recording`` flag is False so the UI explains this.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import threading
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus_test_creator.core.errors import RecordingError, ScreenshotError, TargetConnectionError
from argus_test_creator.models.capabilities import RecorderCapabilities, TargetProfile
from argus_test_creator.models.common import Point
from argus_test_creator.models.recording import RecordingEvent, RecordingEventType
from argus_test_creator.recording.adapter import EventSink, RecorderRegistry
from argus_test_creator.targets.catalog import PLATFORM_CAPABILITIES

_KEYCODES = {
    "ENTER": "KEYCODE_ENTER", "BACK": "KEYCODE_BACK", "HOME": "KEYCODE_HOME",
    "DPAD_UP": "KEYCODE_DPAD_UP", "DPAD_DOWN": "KEYCODE_DPAD_DOWN",
    "DPAD_LEFT": "KEYCODE_DPAD_LEFT", "DPAD_RIGHT": "KEYCODE_DPAD_RIGHT",
    "DPAD_CENTER": "KEYCODE_DPAD_CENTER", "TAB": "KEYCODE_TAB", "SPACE": "KEYCODE_SPACE",
    "BACKSPACE": "KEYCODE_DEL", "DEL": "KEYCODE_DEL", "MENU": "KEYCODE_MENU",
}


class AndroidRecorder:
    def __init__(self, target: TargetProfile, options: dict[str, Any] | None = None) -> None:
        self.target = target
        settings = {**target.settings, **(options or {})}
        self._serial = str(settings.get("serial") or "")
        self._adb = str(settings.get("adb_path") or shutil.which("adb") or "adb")
        self._timeout = float(settings.get("timeout", 20))
        self._capabilities = PLATFORM_CAPABILITIES["android"].model_copy(update={
            "supports_input_recording": False,
            "limitations": (
                "Direct touches on the device are not observed; use the live view and the "
                "remote in the Creator to drive the app (every input is recorded exactly).",
                *PLATFORM_CAPABILITIES["android"].limitations,
            ),
        })
        self._sink: EventSink | None = None
        self._connected = False
        self._size = (0, 0)
        self._lock = threading.Lock()

    @property
    def capabilities(self) -> RecorderCapabilities:
        return self._capabilities

    @property
    def connected(self) -> bool:
        return self._connected

    def describe_limitations(self) -> list[str]:
        return list(self._capabilities.limitations)

    # -- adb --------------------------------------------------------------------------

    def _run(self, *args: str, binary: bool = False, timeout: float | None = None) -> bytes:
        argv = [self._adb]
        if self._serial:
            argv += ["-s", self._serial]
        argv += list(args)
        try:
            completed = subprocess.run(argv, capture_output=True, timeout=timeout or self._timeout,
                                       check=False)
        except FileNotFoundError as exc:
            raise TargetConnectionError(
                "adb was not found.", remediation="Install Android platform-tools and put adb on "
                "PATH (or set the 'adb_path' target setting).",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TargetConnectionError(
                f"adb {' '.join(args)} timed out.",
                remediation="Reconnect the device (adb devices) and retry.",
            ) from exc
        if completed.returncode != 0:
            raise TargetConnectionError(
                f"adb {' '.join(args)} failed: {completed.stderr.decode(errors='replace')[:300]}",
                remediation="Check `adb devices` shows the device as 'device' (authorized).",
            )
        return completed.stdout if binary else completed.stdout

    def connect(self) -> None:
        out = self._run("devices").decode(errors="replace")
        devices = [line.split()[0] for line in out.splitlines()[1:] if "\tdevice" in line]
        if not devices:
            raise TargetConnectionError(
                "No authorized Android device is connected.",
                remediation="Enable USB debugging, connect the device, accept the prompt, then "
                            "check `adb devices`.",
            )
        if self._serial and self._serial not in devices:
            raise TargetConnectionError(
                f"Device {self._serial!r} is not connected.",
                remediation=f"Connected devices: {', '.join(devices)}.",
            )
        if not self._serial and len(devices) > 1:
            raise TargetConnectionError(
                "Several devices are connected; choose one.",
                remediation=f"Set the 'serial' target setting to one of: {', '.join(devices)}.",
            )
        self._connected = True
        self._size = self.screenshot().size

    def disconnect(self) -> None:
        self._connected = False
        self._sink = None

    # -- observation -----------------------------------------------------------------------

    def screenshot(self) -> Image:
        try:
            data = self._run("exec-out", "screencap", "-p", binary=True)
        except TargetConnectionError as exc:
            raise ScreenshotError(exc.message, remediation=exc.remediation) from exc
        try:
            with PILImage.open(io.BytesIO(data)) as img:
                return img.convert("RGB")
        except OSError as exc:
            raise ScreenshotError("Device returned an invalid screenshot.",
                                  remediation="Retry; some devices need a moment after unlock.",
                                  details=repr(exc)) from exc

    def screen_size(self) -> tuple[int, int]:
        return self._size

    # -- recording ----------------------------------------------------------------------------

    def start_recording(self, sink: EventSink) -> None:
        if not self._connected:
            raise RecordingError("Connect the Android target before recording.")
        self._sink = sink

    def stop_recording(self) -> None:
        self._sink = None

    # -- controlled input (ControllableRecorder) --------------------------------------------------

    def send_tap(self, x: int, y: int) -> None:
        with self._lock:
            self._run("shell", "input", "tap", str(x), str(y))
            self._emit(RecordingEventType.CLICK, position=Point(x=x, y=y), button="touch")

    def send_key(self, key: str) -> None:
        keycode = _KEYCODES.get(key.upper())
        with self._lock:
            if keycode is not None:
                self._run("shell", "input", "keyevent", keycode)
            elif len(key) == 1:
                self._run("shell", "input", "text", _escape(key))
            else:
                self._run("shell", "input", "keyevent", key)
            self._emit(RecordingEventType.KEY_PRESS, key=key)

    def send_text(self, text: str) -> None:
        for char in text:
            self.send_key("SPACE" if char == " " else char)

    def send_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        with self._lock:
            self._run("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2),
                      str(duration_ms))
            self._emit(RecordingEventType.POINTER_DOWN, position=Point(x=x1, y=y1),
                       button="touch")
            self._emit(RecordingEventType.POINTER_UP, position=Point(x=x2, y=y2), button="touch",
                       duration_ms=duration_ms)

    def _emit(self, event_type: RecordingEventType, **fields: Any) -> None:
        sink = self._sink
        if sink is not None:
            sink.push(RecordingEvent(event_type=event_type, **fields))


def _escape(text: str) -> str:
    return text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"').replace("&", "\\&")


def register(registry: RecorderRegistry) -> None:
    registry.register("android", AndroidRecorder)
