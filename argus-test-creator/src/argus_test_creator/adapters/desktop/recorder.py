"""DesktopRecorder — observes global mouse/keyboard input and captures the screen.

Requirements (optional extra ``desktop``): ``pynput`` for input listeners and
``mss`` for fast screenshots. On macOS the process needs Screen Recording and
Input Monitoring permissions; the adapter reports that as a limitation and a
clear connection error rather than silently recording nothing.

Coordinates are screenshot pixels of the primary monitor (HiDPI scale
applied), matching Argus's desktop adapter.
"""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus_test_creator.core.errors import RecordingError, ScreenshotError, TargetConnectionError
from argus_test_creator.models.capabilities import RecorderCapabilities, TargetProfile
from argus_test_creator.models.common import Point
from argus_test_creator.models.recording import RecordingEvent, RecordingEventType
from argus_test_creator.recording.adapter import EventSink, RecorderRegistry
from argus_test_creator.targets.catalog import PLATFORM_CAPABILITIES

INSTALL = "pip install 'argus-test-creator[desktop]'"

_SPECIAL_KEYS = {
    "enter": "ENTER", "esc": "BACK", "tab": "TAB", "space": "SPACE", "backspace": "BACKSPACE",
    "delete": "DEL", "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT", "home": "HOME", "end": "END", "page_up": "PAGE_UP",
    "page_down": "PAGE_DOWN",
}
_MODIFIER_KEYS = {"ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr", "shift",
                  "shift_l", "shift_r", "cmd", "cmd_l", "cmd_r"}


class DesktopRecorder:
    def __init__(self, target: TargetProfile, options: dict[str, Any] | None = None) -> None:
        self.target = target
        settings = {**target.settings, **(options or {})}
        self._monitor_index = int(settings.get("monitor", 1))
        self._capabilities = PLATFORM_CAPABILITIES["desktop"]
        self._sink: EventSink | None = None
        self._mouse_listener: Any = None
        self._key_listener: Any = None
        self._connected = False
        self._scale = 1.0
        self._size = (0, 0)
        self._modifiers: set[str] = set()
        self._lock = threading.Lock()
        self._sct_local = threading.local()

    @property
    def capabilities(self) -> RecorderCapabilities:
        return self._capabilities

    @property
    def connected(self) -> bool:
        return self._connected

    def describe_limitations(self) -> list[str]:
        notes = list(self._capabilities.limitations)
        notes.append("Recording captures the primary monitor; the Creator's own window is "
                     "part of the screen — keep it out of the target's area.")
        return notes

    # -- connection ------------------------------------------------------------------

    def connect(self) -> None:
        try:
            import mss  # noqa: F401
            import pynput  # noqa: F401
        except ImportError as exc:
            raise TargetConnectionError(
                "Desktop recording needs the 'desktop' extra.", remediation=INSTALL,
            ) from exc
        try:
            image = self.screenshot()
        except ScreenshotError as exc:
            raise TargetConnectionError(
                str(exc.message),
                remediation="On macOS grant Screen Recording permission to the terminal/app "
                            "running the Creator (System Settings → Privacy & Security).",
            ) from exc
        self._size = image.size
        self._connected = True

    def disconnect(self) -> None:
        self.stop_recording()
        self._connected = False

    # -- observation ---------------------------------------------------------------------

    def screenshot(self) -> Image:
        try:
            import mss
        except ImportError as exc:
            raise ScreenshotError("mss is not installed.", remediation=INSTALL) from exc
        try:
            sct = getattr(self._sct_local, "sct", None)
            if sct is None:
                sct = mss.mss()
                self._sct_local.sct = sct
            monitor = sct.monitors[self._monitor_index]
            shot = sct.grab(monitor)
            image = PILImage.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            self._scale = shot.size[0] / monitor["width"] if monitor["width"] else 1.0
            return image
        except Exception as exc:  # noqa: BLE001
            raise ScreenshotError(
                f"Desktop screenshot failed: {exc}",
                remediation="Check screen-recording permission and the monitor index.",
                details=repr(exc),
            ) from exc

    def screen_size(self) -> tuple[int, int]:
        return self._size

    # -- recording ------------------------------------------------------------------------

    def start_recording(self, sink: EventSink) -> None:
        if not self._connected:
            raise RecordingError("Connect the desktop target before recording.")
        from pynput import keyboard, mouse

        self._sink = sink
        self._mouse_listener = mouse.Listener(on_click=self._on_click, on_move=self._on_move,
                                              on_scroll=self._on_scroll)
        self._key_listener = keyboard.Listener(on_press=self._on_press,
                                               on_release=self._on_release)
        self._mouse_listener.start()
        self._key_listener.start()
        if sys.platform == "darwin":
            # pynput raises on the listener thread when Input Monitoring is denied; surface it.
            threading.Timer(1.0, self._check_listeners).start()

    def stop_recording(self) -> None:
        for listener in (self._mouse_listener, self._key_listener):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:  # noqa: BLE001
                    pass
        self._mouse_listener = self._key_listener = None
        self._sink = None

    def _check_listeners(self) -> None:
        for listener in (self._mouse_listener, self._key_listener):
            if listener is not None and not listener.is_alive():
                self._emit(RecordingEventType.CUSTOM, metadata={
                    "error": "Input listener stopped — grant Input Monitoring permission.",
                })

    # -- pynput callbacks (listener threads) ---------------------------------------------------

    def _point(self, x: float, y: float) -> Point:
        return Point(x=int(x * self._scale), y=int(y * self._scale))

    def _on_click(self, x: float, y: float, button: Any, pressed: bool) -> None:
        kind = RecordingEventType.POINTER_DOWN if pressed else RecordingEventType.POINTER_UP
        self._emit(kind, position=self._point(x, y), button=str(button).split(".")[-1])

    def _on_move(self, x: float, y: float) -> None:
        self._emit(RecordingEventType.POINTER_MOVE, position=self._point(x, y), droppable=True)

    def _on_scroll(self, x: float, y: float, dx: float, dy: float) -> None:
        start = self._point(x, y)
        end = Point(x=start.x + int(dx * 40), y=start.y - int(dy * 40))
        self._emit(RecordingEventType.SCROLL, position=start, position_end=end,
                   metadata={"dx": dx, "dy": dy})

    def _on_press(self, key: Any) -> None:
        name = _key_name(key)
        if name in _MODIFIER_KEYS:
            self._modifiers.add(name.split("_")[0])
            return
        mapped = _SPECIAL_KEYS.get(name, name)
        modifiers = tuple(sorted(self._modifiers))
        if modifiers and len(mapped) == 1:
            mapped = "+".join([*(m.title() for m in modifiers), mapped])
        self._emit(RecordingEventType.KEY_PRESS, key=mapped, modifiers=modifiers,
                   metadata={"modifiers": list(modifiers)})

    def _on_release(self, key: Any) -> None:
        name = _key_name(key)
        if name in _MODIFIER_KEYS:
            self._modifiers.discard(name.split("_")[0])

    def _emit(self, event_type: RecordingEventType, *, droppable: bool = False, **fields: Any) -> None:  # noqa: E501
        sink = self._sink
        if sink is None:
            return
        sink.push(RecordingEvent(event_type=event_type, timestamp=datetime.now(UTC), **fields),
                  droppable=droppable)


def _key_name(key: Any) -> str:
    char = getattr(key, "char", None)
    if char:
        return str(char)
    name = getattr(key, "name", None)
    return str(name) if name else str(key)


def register(registry: RecorderRegistry) -> None:
    registry.register("desktop", DesktopRecorder)
