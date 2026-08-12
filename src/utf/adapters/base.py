"""Device abstraction.

The test engine talks only to this interface. Platform specifics (ADB, SSH,
display stacks) live in adapters. Not every device supports every operation —
capabilities are discoverable, and unsupported operations raise
:class:`DeviceCapabilityError` with a clear message.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from PIL.Image import Image

from utf.exceptions import DeviceCapabilityError
from utf.models.common import HealthCheckResult, ScreenInfo


@dataclass(frozen=True)
class DeviceCapabilities:
    """Discoverable capabilities of a device adapter."""

    supports_screenshot: bool = False
    supports_tap: bool = False
    supports_swipe: bool = False
    supports_keyboard: bool = False
    supports_app_lifecycle: bool = False
    supports_logs: bool = False
    supports_instrumentation: bool = False


class ScreenshotProvider(ABC):
    """Pluggable screenshot acquisition strategy (framebuffer, Weston, custom...)."""

    @abstractmethod
    def capture(self) -> Image:
        """Capture and return the current screen contents."""


class Device(ABC):
    """Abstract device the framework can observe and control."""

    def __init__(self, name: str) -> None:
        self.name = name

    # -- identity / capabilities ------------------------------------------------

    @property
    @abstractmethod
    def capabilities(self) -> DeviceCapabilities:
        ...

    @property
    def platform(self) -> str:
        """Platform label used for test filtering (overridable per adapter)."""
        return type(self).__name__.lower().removesuffix("adapter")

    # -- connection --------------------------------------------------------------

    @abstractmethod
    def connect(self) -> None:
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def health_check(self) -> HealthCheckResult:
        ...

    # -- application lifecycle ----------------------------------------------------

    def start_application(self) -> None:
        raise self._unsupported("start_application")

    def stop_application(self) -> None:
        raise self._unsupported("stop_application")

    def restart_application(self) -> None:
        self.stop_application()
        self.start_application()

    def reset_application(self) -> None:
        """Reset the application to a clean state (default: restart)."""
        self.restart_application()

    def is_application_running(self) -> bool:
        raise self._unsupported("is_application_running")

    # -- observation ---------------------------------------------------------------

    def screenshot(self) -> Image:
        raise self._unsupported("screenshot")

    def get_screen_info(self) -> ScreenInfo:
        raise self._unsupported("get_screen_info")

    def get_screen_size(self) -> tuple[int, int]:
        return self.get_screen_info().size

    def get_logs(self, lines: int = 200) -> str:
        raise self._unsupported("get_logs")

    # -- input ----------------------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        raise self._unsupported("tap")

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> None:
        raise self._unsupported("swipe")

    def press_key(self, key: str) -> None:
        raise self._unsupported("press_key")

    # -- helpers ----------------------------------------------------------------------

    def _unsupported(self, operation: str) -> DeviceCapabilityError:
        return DeviceCapabilityError(
            f"Device {self.name!r} ({type(self).__name__}) does not support "
            f"{operation!r}.",
            remediation="Check device.capabilities before using this operation, "
            "or use a device type that supports it.",
        )

    def __enter__(self) -> Device:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.disconnect()
