"""FakeAdbClient — an in-memory ADB for tests and demos (no hardware, no subprocesses).

Scripts a set of connected devices, their properties, their ``getevent -lp``
listing and a ``getevent -lt`` stream (fixture text or generated lines). The
stream behaves like the real thing: it stays open after the scripted lines
until stopped, and :meth:`FakeAdbClient.disconnect` makes it end abruptly and
removes the device from ``list_devices`` — the way a pulled cable looks.
"""

from __future__ import annotations

import collections
import io
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from PIL import Image as PILImage

from argus_test_creator.adapters.android.getevent_parser import parse_input_devices
from argus_test_creator.adapters.android.models import (
    AndroidDevice,
    AndroidDeviceInfo,
    AndroidInputDevice,
)
from argus_test_creator.core.errors import TargetConnectionError

DEFAULT_LISTING = """add device 1: /dev/input/event5
  name:     "gpio-keys"
  events:
    KEY (0001): KEY_VOLUMEDOWN        KEY_VOLUMEUP          KEY_POWER             KEY_BACK
  input props:
    <none>
add device 2: /dev/input/event2
  name:     "fake_touchscreen"
  events:
    KEY (0001): BTN_TOUCH             BTN_TOOL_FINGER
    ABS (0003): ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_X     : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y     : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
  input props:
    INPUT_PROP_DIRECT
"""


class FakeEventStream:
    """Scripted ``getevent`` output with real-time pacing and abrupt-end support."""

    def __init__(self, lines: Iterable[str], *, line_delay: float = 0.0,
                 end_after_script: bool = False) -> None:
        self._queue: collections.deque[str] = collections.deque(lines)
        self._delay = line_delay
        self._cv = threading.Condition()
        self._ended = end_after_script and not self._queue
        self._end_after = end_after_script
        self._stopped = False
        self.returncode_value: int | None = None
        self.stderr_value = ""

    def push(self, lines: Iterable[str]) -> None:
        with self._cv:
            self._queue.extend(lines)
            self._cv.notify_all()

    def end(self, *, returncode: int = 0, stderr: str = "") -> None:
        with self._cv:
            self._ended = True
            self.returncode_value = returncode
            self.stderr_value = stderr
            self._cv.notify_all()

    def readline(self, timeout: float | None = None) -> str | None:
        with self._cv:
            if not self._queue and not self._ended:
                self._cv.wait(timeout)
            if self._queue:
                line = self._queue.popleft()
                if self._end_after and not self._queue:
                    self._ended = True
                    self.returncode_value = 0
            else:
                return None if self._ended else ""
        if self._delay:
            time.sleep(self._delay)
        return line

    def stop(self, timeout: float = 3.0) -> None:
        with self._cv:
            self._stopped = True
            self._ended = True
            if self.returncode_value is None:
                self.returncode_value = -15
            self._cv.notify_all()

    @property
    def alive(self) -> bool:
        return not self._ended

    @property
    def returncode(self) -> int | None:
        return self.returncode_value

    @property
    def stderr_text(self) -> str:
        return self.stderr_value


class FakeDevice:
    def __init__(
        self,
        serial: str,
        *,
        model: str = "Pixel 8",
        android_version: str = "14",
        sdk: int = 34,
        width: int = 1080,
        height: int = 2400,
        rotation: int = 0,
        listing: str = DEFAULT_LISTING,
        state: str = "device",
    ) -> None:
        self.serial = serial
        self.model = model
        self.android_version = android_version
        self.sdk = sdk
        self.width = width
        self.height = height
        self.rotation = rotation
        self.listing = listing
        self.state = state
        self.script: list[str] = []
        self.stream: FakeEventStream | None = None
        self.streams_started = 0
        self.stop_calls = 0
        self.screenshot_count = 0
        self.line_delay = 0.0
        self.end_after_script = False
        self.getevent_ok = True
        self.screenshot_ok = True

    def screen_size(self) -> tuple[int, int]:
        return (self.height, self.width) if self.rotation in (1, 3) else (self.width, self.height)


class FakeAdbClient:
    def __init__(self, devices: Iterable[FakeDevice] = (), *, adb_available: bool = True) -> None:
        self.devices: dict[str, FakeDevice] = {d.serial: d for d in devices}
        self.adb_available = adb_available
        self.calls: list[tuple[str, ...]] = []
        self._lock = threading.Lock()

    # -- scripting helpers --

    def add_device(self, device: FakeDevice) -> FakeDevice:
        self.devices[device.serial] = device
        return device

    def script_fixture(self, serial: str, path: Path, *, retime: float | None = None) -> None:
        self.script_lines(serial, path.read_text(encoding="utf-8").splitlines(keepends=True))

    def script_lines(self, serial: str, lines: Iterable[str]) -> None:
        device = self._device(serial)
        device.script.extend(lines)
        if device.stream is not None:
            device.stream.push(lines)

    def disconnect(self, serial: str) -> None:
        """Simulate a pulled cable: stream dies, device vanishes."""
        device = self._device(serial)
        device.state = "gone"
        if device.stream is not None:
            device.stream.end(returncode=255, stderr="error: device 'x' not found\n")

    def reconnect(self, serial: str) -> None:
        self.devices[serial].state = "device"

    def _device(self, serial: str) -> FakeDevice:
        device = self.devices.get(serial)
        if device is None or device.state == "gone":
            raise TargetConnectionError(
                f"error: device '{serial}' not found",
                remediation="Check the cable and that `adb devices` lists it as 'device'.",
            )
        return device

    def _record(self, *call: str) -> None:
        with self._lock:
            self.calls.append(call)

    # -- AdbClient protocol --

    def available(self) -> tuple[bool, str]:
        return (True, "fake-adb 1.0") if self.adb_available else (False, "adb not found (fake)")

    def list_devices(self) -> list[AndroidDevice]:
        self._record("devices")
        return [
            AndroidDevice(serial=d.serial, state=d.state, model=d.model.replace(" ", "_"))
            for d in self.devices.values() if d.state != "gone"
        ]

    def shell(self, serial: str, *args: str, timeout: float | None = None) -> str:
        self._record(serial, "shell", *args)
        device = self._device(serial)
        match args:
            case ("getprop",):
                return (f"[ro.product.model]: [{device.model}]\n"
                        f"[ro.build.version.release]: [{device.android_version}]\n"
                        f"[ro.build.version.sdk]: [{device.sdk}]\n")
            case ("wm", "size"):
                return f"Physical size: {device.width}x{device.height}\n"
            case ("dumpsys", "display"):
                return f"  mCurrentRotation={device.rotation}\n"
            case ("getevent", "-lp"):
                if not device.getevent_ok:
                    raise TargetConnectionError("getevent: permission denied")
                return device.listing
            case ("pkill", *_):
                device.stop_calls += 1
                return ""
        return ""

    def exec_out(self, serial: str, *args: str, timeout: float | None = None) -> bytes:
        self._record(serial, "exec-out", *args)
        if args[:2] == ("screencap", "-p"):
            return self.screenshot(serial)
        return b""

    def get_device_info(self, serial: str) -> AndroidDeviceInfo:
        device = self._device(serial)
        self._record(serial, "info")
        return AndroidDeviceInfo(
            serial=serial, model=device.model, android_version=device.android_version,
            sdk=device.sdk, natural_width=device.width, natural_height=device.height,
            rotation=device.rotation,
        )

    def get_input_devices(self, serial: str) -> list[AndroidInputDevice]:
        return parse_input_devices(self.shell(serial, "getevent", "-lp"))

    def getevent_available(self, serial: str) -> tuple[bool, str]:
        device = self._device(serial)
        return (True, "fake getevent") if device.getevent_ok else (False, "permission denied")

    def start_getevent(self, serial: str, device_path: str | None = None) -> FakeEventStream:
        device = self._device(serial)
        self._record(serial, "getevent", device_path or "*")
        stream = FakeEventStream(list(device.script), line_delay=device.line_delay,
                                 end_after_script=device.end_after_script)
        device.script = []
        device.stream = stream
        device.streams_started += 1
        return stream

    def stop_getevent(self, serial: str) -> None:
        device = self.devices.get(serial)
        if device is not None:
            device.stop_calls += 1

    def screenshot(self, serial: str) -> bytes:
        device = self._device(serial)
        if not device.screenshot_ok:
            raise TargetConnectionError("screencap failed")
        device.screenshot_count += 1
        w, h = device.screen_size()
        image = PILImage.new("RGB", (max(w, 1), max(h, 1)),
                             (40 + device.screenshot_count % 50, 60, 90))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


__all__ = ["DEFAULT_LISTING", "FakeAdbClient", "FakeDevice", "FakeEventStream"]
