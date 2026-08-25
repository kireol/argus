"""ESP32 device adapter.

Talks to firmware that links the Argus agent (``agents/esp32/``) over USB
serial or through the Wokwi simulator. Logs are whatever the firmware prints;
screenshots are the firmware's framebuffer; keys are delivered to the
firmware's key callback; reset toggles DTR/RTS (or restarts the simulator).
"""

from __future__ import annotations

import subprocess
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities
from argus.adapters.esp32.framebuffer import decode
from argus.adapters.esp32.instrumentation import SerialInstrumentationClient
from argus.adapters.esp32.protocol import AgentInfo, AgentLink
from argus.adapters.esp32.transport import (
    SerialTransport,
    Transport,
    WokwiTransport,
    serial_available,
    wokwi_available,
)
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError
from argus.instrumentation.client import InstrumentationClient
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo

_TRANSPORTS = ("serial", "wokwi")
_MAX_LOG_LINES = 5000
_HELLO_RETRY = 0.5
_FLASH_BAUD = "460800"


def _run_esptool(argv: list[str]) -> int:
    return subprocess.run(argv, check=False).returncode


class Esp32Adapter(Device):
    """Controls ESP32 firmware through the Argus serial agent."""

    def __init__(
        self,
        name: str,
        *,
        transport: str,
        port: str | None = None,
        baud: int = 115200,
        usb_cdc: bool = False,
        project_dir: str | Path | None = None,
        firmware: str | Path | None = None,
        firmware_offset: str = "0x10000",
        agent: bool = True,
        boot_timeout: float = 10.0,
        timeout: float = 5.0,
        mono_colors: tuple[str, str] = ("#ffffff", "#000000"),
        transport_factory: Callable[[], Transport] | None = None,
        runner: Callable[[list[str]], int] | None = None,
    ) -> None:
        super().__init__(name)
        if transport not in _TRANSPORTS:
            raise ConfigurationError(
                f"ESP32 device {name!r}: unknown transport {transport!r}.",
                remediation=f"Use one of: {', '.join(_TRANSPORTS)}.",
            )
        if transport == "serial" and not port:
            raise ConfigurationError(
                f"ESP32 device {name!r} requires a 'port' for the serial transport.",
                remediation="Set devices.<name>.port, e.g. /dev/cu.usbserial-0001.",
            )
        if transport == "wokwi" and project_dir is None:
            raise ConfigurationError(
                f"ESP32 device {name!r} requires 'project_dir' for the wokwi transport.",
                remediation="Set devices.<name>.project_dir to the folder containing wokwi.toml.",
            )
        if firmware is not None and transport != "serial":
            raise ConfigurationError(
                f"ESP32 device {name!r}: 'firmware' flashing needs transport: serial.",
                remediation="Point wokwi.toml at the firmware instead.",
            )
        self._transport_kind = transport
        self._port = port
        self._baud = int(baud)
        self._usb_cdc = bool(usb_cdc)
        self._project_dir = Path(project_dir) if project_dir is not None else None
        self._firmware = Path(firmware) if firmware is not None else None
        self._firmware_offset = firmware_offset
        self._agent = bool(agent)
        self._boot_timeout = float(boot_timeout)
        self._timeout = float(timeout)
        self._mono_colors = (str(mono_colors[0]), str(mono_colors[1]))
        self._transport_factory = transport_factory
        self._run = runner or _run_esptool
        self._transport: Transport | None = None
        self._link: AgentLink | None = None
        self._info: AgentInfo | None = None
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._log = get_logger("argus.esp32", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> Esp32Adapter:
        options: dict[str, Any] = config.options
        transport = options.get("transport")
        if not transport:
            raise ConfigurationError(
                f"ESP32 device {name!r} requires a 'transport' option.",
                remediation="Set devices.<name>.transport to 'serial' or 'wokwi'.",
            )
        colors = options.get("mono_colors", ["#ffffff", "#000000"])
        if not (isinstance(colors, (list, tuple)) and len(colors) == 2):
            raise ConfigurationError(
                f"ESP32 device {name!r}: 'mono_colors' must be a 2-item list "
                f"[foreground, background], got {colors!r}.",
                remediation='Set devices.<name>.mono_colors: ["#ffffff", "#000000"].',
            )
        return cls(
            name,
            transport=str(transport),
            port=options.get("port"),
            baud=int(options.get("baud", 115200)),
            usb_cdc=bool(options.get("usb_cdc", False)),
            project_dir=options.get("project_dir"),
            firmware=options.get("firmware"),
            firmware_offset=str(options.get("firmware_offset", "0x10000")),
            agent=bool(options.get("agent", True)),
            boot_timeout=float(options.get("boot_timeout", 10.0)),
            timeout=float(options.get("timeout", 5.0)),
            mono_colors=(str(colors[0]), str(colors[1])),
        )

    # -- identity -----------------------------------------------------------------

    @property
    def capabilities(self) -> DeviceCapabilities:
        if not self._agent:
            return DeviceCapabilities(supports_logs=True, supports_app_lifecycle=True)
        caps = (
            self._info.caps if self._info is not None else frozenset({"screen", "input", "status"})
        )
        return DeviceCapabilities(
            supports_screenshot="screen" in caps,
            supports_keyboard="input" in caps,
            supports_app_lifecycle=True,
            supports_logs=True,
            supports_instrumentation=bool(caps & {"status", "state"}),
        )

    @property
    def platform(self) -> str:
        return "esp32"

    # -- plumbing --------------------------------------------------------------------

    def _open_transport(self) -> Transport:
        if self._transport_factory is not None:
            return self._transport_factory()
        if self._transport_kind == "serial":
            if not serial_available():
                raise DeviceConnectionError(
                    "pyserial is not installed (required for esp32 serial transport).",
                    remediation='Install ESP32 support: pip install "argus[esp32]"',
                )
            assert self._port is not None
            return SerialTransport(self._port, self._baud, usb_cdc=self._usb_cdc)
        assert self._project_dir is not None
        return WokwiTransport(self._project_dir)

    def _flash(self) -> None:
        assert self._firmware is not None and self._port is not None
        if not self._firmware.is_file():
            raise ConfigurationError(
                f"ESP32 device {self.name!r}: firmware {str(self._firmware)!r} not found.",
                remediation="Point devices.<name>.firmware at the built .bin.",
            )
        argv = [
            "esptool",
            "--port",
            self._port,
            "--baud",
            _FLASH_BAUD,
            "write_flash",
            self._firmware_offset,
            str(self._firmware),
        ]
        self._log.info("Flashing %s", self._firmware.name)
        try:
            code = self._run(argv)
        except FileNotFoundError as exc:
            raise DeviceConnectionError(
                "esptool not found (required for firmware flashing).",
                remediation='Install ESP32 support: pip install "argus[esp32]"',
            ) from exc
        if code != 0:
            raise DeviceConnectionError(
                f"esptool exited with status {code} while flashing {self._firmware.name}.",
                remediation="Run the esptool command manually: " + " ".join(argv),
            )

    def _require_link(self) -> AgentLink:
        if self._link is None:
            raise DeviceConnectionError(
                f"ESP32 device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        return self._link

    def _require_agent(self, cap: str, operation: str) -> AgentLink:
        link = self._require_link()
        if not self._agent or self._info is None or cap not in self._info.caps:
            raise DeviceCapabilityError(
                f"ESP32 device {self.name!r} firmware does not provide {cap!r}; "
                f"{operation} is unavailable.",
                remediation="Register the capability in the firmware's Argus agent "
                "(argus.begin(...) with a framebuffer / argus.onKey(...)).",
            )
        return link

    def _wait_for_agent(self) -> AgentInfo:
        link = self._require_link()
        deadline = time.monotonic() + self._boot_timeout
        last_error: DeviceConnectionError | None = None
        while True:
            try:
                return link.hello()
            except DeviceConnectionError as exc:
                last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(_HELLO_RETRY)
        raise DeviceConnectionError(
            f"ESP32 device {self.name!r}: no Argus agent responded within "
            f"{self._boot_timeout}s ({last_error}).",
            remediation="Check the firmware links the Argus agent and calls argus.poll(), "
            "the baud rate matches, the firmware was flashed at the right firmware_offset "
            "(0x10000 for an app-only image, 0x0 for a merged image), or set agent: false "
            "for a logs-only device.",
        )

    def _boot(self) -> None:
        """Reset the board and (when an agent is expected) wait for its hello."""
        assert self._transport is not None and self._link is not None
        self._logs.clear()
        self._info = None
        self._transport.reset()
        self._link.reset_stream()
        if self._agent:
            self._info = self._wait_for_agent()
            self._log.info(
                "Agent %s v%s (fb=%s caps=%s)",
                self._info.name, self._info.version, self._info.fb_format,
                ",".join(sorted(self._info.caps)),
            )

    # -- connection -----------------------------------------------------------------

    def connect(self) -> None:
        if self._link is not None:
            return
        if self._firmware is not None:
            self._flash()
        transport = self._open_transport()
        link = AgentLink(transport, log_sink=self._logs, timeout=self._timeout)
        self._transport, self._link = transport, link
        link.start()
        try:
            self._boot()
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        link, self._link = self._link, None
        transport, self._transport = self._transport, None
        self._info = None
        if link is not None:
            link.close()
        elif transport is not None:
            transport.close()

    def is_available(self) -> bool:
        if self._transport_factory is not None:
            return True
        return serial_available() if self._transport_kind == "serial" else wokwi_available()

    def health_check(self) -> HealthCheckResult:
        if self._link is None:
            return HealthCheckResult.failed("esp32 not connected")
        if self._agent and self._info is None:
            return HealthCheckResult.failed("agent did not respond")
        details: dict[str, Any] = {"agent": self._agent}
        if self._info is not None:
            details["name"] = self._info.name
            details["fb"] = (
                f"{self._info.fb_format},{self._info.width},{self._info.height}"
                if self._info.fb_format
                else "none"
            )
            details["caps"] = sorted(self._info.caps)
        assert self._transport is not None
        return HealthCheckResult.ok(f"esp32 via {self._transport.description}", **details)

    # -- application lifecycle --------------------------------------------------------

    def start_application(self) -> None:
        self._require_link()
        self._boot()

    def stop_application(self) -> None:
        raise self._unsupported("stop_application")

    def reset_application(self) -> None:
        self.start_application()

    def is_application_running(self) -> bool:
        if self._link is None:
            return False
        return self._info is not None if self._agent else True

    # -- observation --------------------------------------------------------------------

    def screenshot(self) -> Image:
        link = self._require_agent("screen", "screenshot")
        assert self._info is not None and self._info.fb_format is not None
        data = link.request("screenshot")
        return decode(
            data, self._info.fb_format, self._info.width, self._info.height,
            mono_colors=self._mono_colors,
        )

    def get_screen_info(self) -> ScreenInfo:
        self._require_agent("screen", "get_screen_info")
        assert self._info is not None
        return ScreenInfo(width=self._info.width, height=self._info.height)

    def get_logs(self, lines: int = 200) -> str:
        entries = list(self._logs)[-lines:] if lines > 0 else []
        return "\n".join(entries)

    # -- input ----------------------------------------------------------------------------

    def press_key(self, key: str) -> None:
        if "\r" in key or "\n" in key:
            raise ConfigurationError(
                f"Key {key!r} contains a line break, which would inject extra "
                "protocol frames onto the wire.",
                remediation="Pass a single key name with no embedded newlines.",
            )
        link = self._require_agent("input", "press_key")
        link.request("input", key)

    # -- instrumentation ------------------------------------------------------------------

    def instrumentation_client(self) -> InstrumentationClient | None:
        if self._link is None or self._info is None or not (self._info.caps & {"status", "state"}):
            return None
        return SerialInstrumentationClient(self._link, self._info)
