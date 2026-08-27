"""Device abstraction.

The test engine talks only to this interface. Platform specifics (ADB, SSH,
display stacks) live in adapters. Not every device supports every operation —
capabilities are discoverable, and unsupported operations raise
:class:`DeviceCapabilityError` with a clear message.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL.Image import Image

from argus.exceptions import DeviceCapabilityError
from argus.models.common import HealthCheckResult, PlaybackState, ScreenInfo

if TYPE_CHECKING:
    from argus.instrumentation.client import InstrumentationClient


Point = tuple[int, int]


@dataclass(frozen=True)
class DeviceCapabilities:
    """Discoverable capabilities of a device adapter."""

    supports_screenshot: bool = False
    supports_tap: bool = False
    supports_swipe: bool = False
    supports_long_press: bool = False
    supports_drag: bool = False
    supports_multi_touch: bool = False
    supports_keyboard: bool = False
    supports_app_lifecycle: bool = False
    supports_logs: bool = False
    supports_instrumentation: bool = False
    supports_playback_state: bool = False


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

    def begin_metrics_session(self) -> None:  # noqa: B027 - optional hook
        """Reset per-test counters (e.g. ``dumpsys gfxinfo reset``). Optional."""

    def sample_metrics(self) -> dict[str, float]:
        """Cheap snapshot of app/system performance while a test runs.

        Keys are stable names such as ``fps``, ``app_rss_mb``, ``system_load_1m``.
        Return ``{}`` when the adapter cannot sample. Must not raise.
        """
        return {}

    def get_playback_state(self) -> PlaybackState:
        """Current media playback state (devices with supports_playback_state)."""
        raise self._unsupported("get_playback_state")

    # -- instrumentation --------------------------------------------------------------

    def instrumentation_client(self) -> InstrumentationClient | None:
        """Instrumentation served by the device itself (``instrumentation: {type: device}``)."""
        return None

    # -- input ----------------------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        raise self._unsupported("tap")

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> None:
        raise self._unsupported("swipe")

    def press_key(self, key: str) -> None:
        raise self._unsupported("press_key")

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        """Press and hold at a point for ``duration_ms`` before releasing."""
        raise self._unsupported("long_press")

    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        hold_ms: int = 500,
        duration_ms: int = 500,
    ) -> None:
        """Press, hold in place for ``hold_ms``, then move to the target and release.

        This is drag-and-drop / reorder, as opposed to ``swipe`` which moves
        immediately (a fling/scroll).
        """
        raise self._unsupported("drag")

    def multi_touch(self, fingers: Sequence[Sequence[Point]], duration_ms: int = 500) -> None:
        """Move several fingers simultaneously along their paths.

        ``fingers[i]`` is the ordered list of points finger ``i`` visits; every
        finger touches down at its first point and lifts at its last. All fingers
        share the same timeline of ``duration_ms``.
        """
        raise self._unsupported("multi_touch")

    def pinch(
        self,
        cx: int,
        cy: int,
        start_distance: int,
        end_distance: int,
        duration_ms: int = 500,
    ) -> None:
        """Two-finger pinch centred on ``(cx, cy)``.

        Fingers start ``start_distance`` apart on the horizontal axis and end
        ``end_distance`` apart: a growing distance zooms in, a shrinking one
        zooms out. Implemented on top of ``multi_touch`` so any adapter that
        supports multi-touch gets pinch for free.
        """
        half_start, half_end = start_distance // 2, end_distance // 2
        self.multi_touch(
            [
                [(cx - half_start, cy), (cx - half_end, cy)],
                [(cx + half_start, cy), (cx + half_end, cy)],
            ],
            duration_ms,
        )

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


def interpolate_path(path: Sequence[Point], step: int, steps: int) -> Point:
    """Position along a polyline ``path`` at fraction ``step / steps``."""
    if len(path) == 1 or step <= 0:
        return path[0]
    if step >= steps:
        return path[-1]
    segments = len(path) - 1
    position = step / steps * segments
    index = min(int(position), segments - 1)
    fraction = position - index
    (x1, y1), (x2, y2) = path[index], path[index + 1]
    return (round(x1 + (x2 - x1) * fraction), round(y1 + (y2 - y1) * fraction))
