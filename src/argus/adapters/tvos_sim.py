"""tvOS Simulator adapter (macOS + Xcode).

Everything goes through ``xcrun simctl``: boot, install, launch/terminate,
PNG screenshots, and a streamed ``log`` subprocess that feeds the device log
buffer. The Simulator has no remote-key API, so key presses are sent as
keyboard shortcuts to the Simulator app via ``osascript`` (this needs the
terminal to have Accessibility permission in System Settings).
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities
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
_MAX_LOG_LINES = 5000
_BOOTED = "booted"

# macOS virtual key codes for the Simulator's remote shortcuts.
_KEY_CODES = {
    "DPAD_UP": 126,
    "UP": 126,
    "DPAD_DOWN": 125,
    "DOWN": 125,
    "DPAD_LEFT": 123,
    "LEFT": 123,
    "DPAD_RIGHT": 124,
    "RIGHT": 124,
    "ENTER": 36,
    "DPAD_CENTER": 36,
    "SELECT": 36,
    "BACK": 53,
    "MENU": 53,
    "ESCAPE": 53,
    "MEDIA_PLAY_PAUSE": 49,
    "SPACE": 49,
}
_KEYSTROKES = {
    "HOME": 'keystroke "h" using {command down, shift down}',
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


Runner = Callable[[list[str]], CommandResult]
Spawner = Callable[[list[str]], Any]


def _subprocess_runner(timeout: float) -> Runner:
    def run(argv: list[str]) -> CommandResult:
        completed = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    return run


def _subprocess_spawner(argv: list[str]) -> Any:
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


class _LogPump(threading.Thread):
    """Copies a process's stdout lines into a bounded deque."""

    def __init__(self, process: Any, sink: deque[str]) -> None:
        super().__init__(daemon=True, name="tvos-sim-log")
        self._process = process
        self._sink = sink

    def run(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            self._sink.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))


class TvosSimAdapter(Device):
    """Controls a tvOS app running in the Xcode Simulator."""

    def __init__(
        self,
        name: str,
        *,
        bundle_id: str,
        udid: str = _BOOTED,
        app_path: str | Path | None = None,
        boot: bool = True,
        process_name: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        runner: Runner | None = None,
        spawner: Spawner | None = None,
    ) -> None:
        super().__init__(name)
        self._bundle_id = bundle_id
        self._requested_udid = udid
        self._app_path = Path(app_path) if app_path is not None else None
        self._boot = boot
        self._process_name = process_name or bundle_id.rsplit(".", 1)[-1]
        self._timeout = float(timeout)
        self._run: Runner = runner or _subprocess_runner(self._timeout)
        self._spawn: Spawner = spawner or _subprocess_spawner
        self._udid: str | None = None
        self._app_running = False
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._log_process: Any = None
        self._log_pump: _LogPump | None = None
        self._spawned_argv: list[str] = []
        self._screen_size: tuple[int, int] | None = None
        self._log = get_logger("argus.tvos_sim", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> TvosSimAdapter:
        options: dict[str, Any] = config.options
        bundle_id = options.get("bundle_id")
        if not bundle_id:
            raise ConfigurationError(
                f"tvOS Simulator device {name!r} requires a 'bundle_id' option.",
                remediation="Set devices.<name>.bundle_id to the app's bundle identifier.",
            )
        return cls(
            name,
            bundle_id=str(bundle_id),
            udid=str(options.get("udid", _BOOTED)),
            app_path=options.get("app_path"),
            boot=bool(options.get("boot", True)),
            process_name=options.get("process_name"),
            timeout=float(options.get("timeout", _DEFAULT_TIMEOUT)),
        )

    # -- identity -----------------------------------------------------------------

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=True,
            supports_keyboard=True,
            supports_app_lifecycle=True,
            supports_logs=True,
            supports_instrumentation=True,
        )

    @property
    def platform(self) -> str:
        return "tvos_sim"

    # -- command plumbing -------------------------------------------------------------

    def _command(self, argv: list[str], *, check: bool = True) -> CommandResult:
        try:
            result = self._run(argv)
        except FileNotFoundError as exc:
            raise DeviceConnectionError(
                f"{argv[0]!r} not found; the tvOS Simulator needs Xcode.",
                remediation="Install Xcode and run: xcode-select --install",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DeviceConnectionError(
                f"{' '.join(argv[:3])} timed out after {self._timeout}s.",
                remediation="Check the Simulator is responsive or raise 'timeout'.",
            ) from exc
        if check and result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise DeviceConnectionError(
                f"{' '.join(argv[:3])} failed ({result.returncode}): {stderr}",
                remediation="Run the command manually to diagnose: " + " ".join(argv),
            )
        return result

    def _simctl(self, *args: str, check: bool = True) -> CommandResult:
        return self._command(["xcrun", "simctl", *args], check=check)

    def _require_udid(self) -> str:
        if self._udid is None:
            raise DeviceConnectionError(
                f"tvOS Simulator device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        return self._udid

    def _list_tvos_devices(self) -> list[dict[str, Any]]:
        result = self._simctl("list", "devices", "-j")
        payload = json.loads(result.stdout.decode("utf-8") or "{}")
        devices: list[dict[str, Any]] = []
        for runtime, entries in payload.get("devices", {}).items():
            if "tvos" in runtime.lower():
                devices.extend(entries)
        return devices

    def _resolve_udid(self) -> tuple[str, str]:
        """Return (udid, state) for the requested simulator."""
        devices = self._list_tvos_devices()
        if self._requested_udid == _BOOTED:
            for device in devices:
                if device.get("state") == "Booted":
                    return str(device["udid"]), "Booted"
            raise DeviceConnectionError(
                "no booted tvOS simulator found.",
                remediation="Boot one in Xcode (or run: xcrun simctl boot <udid>) or set "
                "devices.<name>.udid so Argus can boot it.",
            )
        for device in devices:
            if device.get("udid") == self._requested_udid:
                return str(device["udid"]), str(device.get("state", ""))
        raise DeviceConnectionError(
            f"tvOS simulator {self._requested_udid!r} not found.",
            remediation="List simulators with: xcrun simctl list devices",
        )

    # -- connection -----------------------------------------------------------------

    def connect(self) -> None:
        if self._udid is not None:
            return
        udid, state = self._resolve_udid()
        if state != "Booted":
            if not self._boot:
                raise DeviceConnectionError(
                    f"tvOS simulator {udid!r} is {state or 'not booted'} and boot=false.",
                    remediation="Boot it in Xcode or set devices.<name>.boot: true.",
                )
            self._simctl("boot", udid)
            self._simctl("bootstatus", udid, "-b")
        self._command(["open", "-a", "Simulator"], check=False)
        if self._app_path is not None:
            self._simctl("install", udid, str(self._app_path))
        self._udid = udid
        try:
            self._start_log_stream()
        except DeviceConnectionError:
            self._stop_log_stream()
            self._udid = None
            self._app_running = False
            raise
        except Exception as exc:
            self._stop_log_stream()
            self._udid = None
            self._app_running = False
            raise DeviceConnectionError(
                f"tvOS simulator {udid!r}: failed to start log stream: {exc}",
                remediation="Check the simulator is booted and 'xcrun simctl spawn' works.",
            ) from exc
        self._log.info("Connected to tvOS simulator %s", udid)

    def _start_log_stream(self) -> None:
        argv = [
            "xcrun",
            "simctl",
            "spawn",
            self._require_udid(),
            "log",
            "stream",
            "--style",
            "compact",
            "--predicate",
            f'process == "{self._process_name}"',
        ]
        self._spawned_argv = argv
        try:
            self._log_process = self._spawn(argv)
        except FileNotFoundError as exc:
            raise DeviceConnectionError(
                "'xcrun' not found; the tvOS Simulator needs Xcode.",
                remediation="Install Xcode and run: xcode-select --install",
            ) from exc
        self._log_pump = _LogPump(self._log_process, self._logs)
        self._log_pump.start()

    def _stop_log_stream(self) -> None:
        process, self._log_process = self._log_process, None
        if process is not None:
            with contextlib.suppress(Exception):
                process.terminate()
            with contextlib.suppress(Exception):
                process.wait(timeout=2.0)
            with contextlib.suppress(Exception):
                if process.stdout is not None:
                    process.stdout.close()
        pump, self._log_pump = self._log_pump, None
        if pump is not None:
            pump.join(timeout=2.0)

    def disconnect(self) -> None:
        self._stop_log_stream()
        self._udid = None
        self._app_running = False

    def is_available(self) -> bool:
        try:
            return self._run(["xcrun", "simctl", "help"]).returncode == 0
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return False

    def health_check(self) -> HealthCheckResult:
        if self._udid is None:
            return HealthCheckResult.failed("simulator not connected")
        try:
            devices = self._list_tvos_devices()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(str(exc))
        for device in devices:
            if device.get("udid") == self._udid:
                if device.get("state") == "Booted":
                    return HealthCheckResult.ok("simulator booted", udid=self._udid)
                return HealthCheckResult.failed(f"simulator state is {device.get('state')}")
        return HealthCheckResult.failed("simulator no longer listed")

    # -- application lifecycle --------------------------------------------------------

    def start_application(self) -> None:
        udid = self._require_udid()
        self._logs.clear()
        self._simctl("launch", udid, self._bundle_id)
        self._app_running = True

    def stop_application(self) -> None:
        udid = self._require_udid()
        self._simctl("terminate", udid, self._bundle_id, check=False)
        self._app_running = False

    def reset_application(self) -> None:
        udid = self._require_udid()
        self.stop_application()
        if self._app_path is not None:
            self._simctl("uninstall", udid, self._bundle_id, check=False)
            self._simctl("install", udid, str(self._app_path))
        self.start_application()

    def is_application_running(self) -> bool:
        return self._udid is not None and self._app_running

    # -- observation --------------------------------------------------------------------

    def screenshot(self) -> Image:
        udid = self._require_udid()
        result = self._simctl("io", udid, "screenshot", "--type", "png", "-")
        try:
            with PILImage.open(io.BytesIO(result.stdout)) as img:
                rgb = img.convert("RGB")
        except Exception as exc:  # noqa: BLE001 - any decode failure
            raise ScreenshotError(
                f"Simulator screenshot could not be decoded: {exc}",
                remediation="Check the simulator is booted and showing a screen.",
            ) from exc
        self._screen_size = rgb.size
        return rgb

    def get_screen_info(self) -> ScreenInfo:
        if self._screen_size is None:
            self.screenshot()
        assert self._screen_size is not None
        return ScreenInfo(width=self._screen_size[0], height=self._screen_size[1])

    def get_logs(self, lines: int = 200) -> str:
        entries = list(self._logs)[-lines:] if lines > 0 else []
        return "\n".join(entries)

    # -- input ----------------------------------------------------------------------------

    def press_key(self, key: str) -> None:
        self._require_udid()
        name = key.removeprefix("KEYCODE_")
        upper = name.upper()
        if len(name) == 1:
            escaped = name.replace("\\", "\\\\").replace('"', '\\"')
            action = f'keystroke "{escaped}"'
        elif upper in _KEY_CODES:
            action = f"key code {_KEY_CODES[upper]}"
        elif upper in _KEYSTROKES:
            action = _KEYSTROKES[upper]
        else:
            raise DeviceCapabilityError(
                f"tvOS Simulator device {self.name!r} cannot send key {key!r}.",
                remediation="Use DPAD_*, ENTER, BACK/MENU, HOME, MEDIA_PLAY_PAUSE or a single "
                "character.",
            )
        argv = [
            "osascript",
            "-e",
            'tell application "Simulator" to activate',
            "-e",
            f'tell application "System Events" to {action}',
        ]
        result = self._command(argv, check=False)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            if "assistive access" in stderr or "not allowed" in stderr:
                raise DeviceConnectionError(
                    "osascript was denied Accessibility access to the Simulator.",
                    remediation="System Settings > Privacy & Security > Accessibility: enable "
                    "your terminal application, then re-run.",
                )
            raise DeviceConnectionError(
                f"osascript failed ({result.returncode}): {stderr.strip()}",
                remediation="Check the Simulator app is running and frontmost.",
            )
