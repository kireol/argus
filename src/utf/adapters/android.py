"""Android device adapter (ADB-based, no Android Studio / Appium dependency).

Talks to emulators or physical devices through the ``adb`` binary using
``subprocess`` — portable across Windows/macOS/Linux.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from utf.adapters.base import Device, DeviceCapabilities
from utf.config.models import DeviceConfig
from utf.exceptions import (
    ConfigurationError,
    DeviceConnectionError,
    ScreenshotError,
)
from utf.logging import get_logger
from utf.models.common import HealthCheckResult, ScreenInfo

_DEFAULT_TIMEOUT = 30.0


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
    ) -> None:
        super().__init__(name)
        self._serial = serial
        self._app_package = app_package
        self._app_activity = app_activity
        self._adb_path = adb_path
        self._timeout = command_timeout
        self._connected = False
        self._log = get_logger("utf.android", device=name)

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
        )

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=True,
            supports_tap=True,
            supports_swipe=True,
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
        return ScreenInfo(width=width, height=height, dpi=dpi)

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
