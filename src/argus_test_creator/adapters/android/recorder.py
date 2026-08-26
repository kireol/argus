"""AndroidRecorder — observes real touches and keys through ``adb shell getevent``.

::

    adb shell getevent -lt  →  AdbProcess (stream)
                            →  GetEventParser        (text → AndroidRawInputEvent)
                            →  AndroidGestureRecognizer (slots, coordinates, gestures)
                            →  RecordingEvent(GESTURE / KEY_PRESS)  → EventSink → session

``getevent`` is an implementation detail: the session, the authoring model and
the YAML only ever see semantic events. The recorder also keeps the
*controlled* input path (``send_tap``/``send_key``/``send_text``) for the live
view and remote panel; those go through ``adb shell input`` and are recorded
directly — ``getevent`` does not observe injected input, so nothing is
recorded twice.
"""

from __future__ import annotations

import io
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus_test_creator.adapters.android.adb import AdbClient, EventStream, SubprocessAdbClient
from argus_test_creator.adapters.android.coordinates import AndroidCoordinateMapper
from argus_test_creator.adapters.android.diagnostics import AndroidRecordingDiagnostics
from argus_test_creator.adapters.android.gestures import AndroidGestureRecognizer, GestureConfig
from argus_test_creator.adapters.android.getevent_parser import GetEventParser
from argus_test_creator.adapters.android.models import (
    AndroidDevice,
    AndroidDeviceInfo,
    AndroidInputDevice,
    KeyPress,
    LongPress,
    MultiTouch,
    RecognizedGesture,
    Swipe,
    Tap,
    UnknownGesture,
)
from argus_test_creator.core.errors import RecordingError, ScreenshotError, TargetConnectionError
from argus_test_creator.core.logging import get_logger
from argus_test_creator.models.capabilities import RecorderCapabilities, TargetProfile
from argus_test_creator.models.common import Point
from argus_test_creator.models.recording import RecordingEvent, RecordingEventType
from argus_test_creator.recording.adapter import EventSink, RecorderRegistry
from argus_test_creator.targets.catalog import PLATFORM_CAPABILITIES

_log = get_logger("android")

_KEYCODES = {
    "ENTER": "KEYCODE_ENTER", "BACK": "KEYCODE_BACK", "HOME": "KEYCODE_HOME",
    "DPAD_UP": "KEYCODE_DPAD_UP", "DPAD_DOWN": "KEYCODE_DPAD_DOWN",
    "DPAD_LEFT": "KEYCODE_DPAD_LEFT", "DPAD_RIGHT": "KEYCODE_DPAD_RIGHT",
    "DPAD_CENTER": "KEYCODE_DPAD_CENTER", "TAB": "KEYCODE_TAB", "SPACE": "KEYCODE_SPACE",
    "BACKSPACE": "KEYCODE_DEL", "DEL": "KEYCODE_DEL", "MENU": "KEYCODE_MENU",
}
_USEFUL_KEYS = {"KEY_BACK", "KEY_HOME", "KEY_MENU", "KEY_ENTER", "KEY_UP", "KEY_DOWN",
                "KEY_LEFT", "KEY_RIGHT", "KEY_SELECT", "KEY_VOLUMEUP", "KEY_VOLUMEDOWN",
                "KEY_POWER"}
ROTATION_POLL_S = 2.0
STREAM_RESTART_DELAY_S = 0.5


class AndroidRecorder:
    def __init__(
        self,
        target: TargetProfile,
        options: dict[str, Any] | None = None,
        *,
        adb: AdbClient | None = None,
    ) -> None:
        self.target = target
        settings = {**target.settings, **(options or {})}
        self._settings = settings
        self._serial = str(settings.get("serial") or "")
        self._timeout = float(settings.get("timeout", 20))
        self._adb: AdbClient = adb or SubprocessAdbClient(
            str(settings.get("adb_path") or "") or None, timeout=self._timeout
        )
        self._gesture_config = GestureConfig(
            tap_max_distance_px=int(settings.get("tap_max_distance_px", 20)),
            long_press_min_ms=int(settings.get("long_press_min_ms", 500)),
            tap_max_duration_ms=int(settings.get("tap_max_duration_ms", 500)),
        )
        self._capabilities = PLATFORM_CAPABILITIES["android"]
        self.diagnostics = AndroidRecordingDiagnostics()
        self.device_info: AndroidDeviceInfo | None = None
        self.input_devices: list[AndroidInputDevice] = []
        self.touchscreen: AndroidInputDevice | None = None
        self.touchscreen_candidates: list[AndroidInputDevice] = []
        self._mapper: AndroidCoordinateMapper | None = None
        self._sink: EventSink | None = None
        self._connected = False
        self._lost = False
        self._size = (0, 0)
        self._lock = threading.Lock()
        self._stream: EventStream | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._recognizer: AndroidGestureRecognizer | None = None
        self._parser = GetEventParser()
        self._epoch: datetime | None = None  # wall-clock for kernel timestamp 0
        self._restart_attempts = 0

    # -- RecorderAdapter ---------------------------------------------------------------------

    @property
    def capabilities(self) -> RecorderCapabilities:
        return self._capabilities

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def adb(self) -> AdbClient:
        return self._adb

    @property
    def recording(self) -> bool:
        return self._reader is not None and self._reader.is_alive()

    @property
    def target_lost(self) -> bool:
        return self._lost

    def describe_limitations(self) -> list[str]:
        notes = list(self._capabilities.limitations)
        if self.touchscreen is None and self._connected:
            notes.insert(0, "No touchscreen input device was found; only keys are recorded.")
        return notes

    # -- discovery -----------------------------------------------------------------------------

    def list_devices(self) -> list[AndroidDevice]:
        """Every device ``adb`` knows about (any state) — for explicit selection."""
        return self._adb.list_devices()

    def select_device(self, serial: str) -> None:
        if self._connected:
            self.disconnect()
        self._serial = serial

    def connect(self) -> None:
        devices = self._adb.list_devices()
        self._serial = _choose_serial(self._serial, devices)
        info = self._adb.get_device_info(self._serial)
        inputs = self._adb.get_input_devices(self._serial)
        touchscreen, candidates = select_touchscreen(
            inputs, override=str(self._settings.get("input_device") or "") or None
        )
        self.device_info = info
        self.input_devices = inputs
        self.touchscreen = touchscreen
        self.touchscreen_candidates = candidates
        self._mapper = self._build_mapper(info, touchscreen)
        self._size = info.screen_size
        if self._size == (0, 0):
            self._size = self.screenshot().size
        keys_observable = any(
            set(d.key_codes) & _USEFUL_KEYS for d in inputs if not d.is_touchscreen
        )
        base = PLATFORM_CAPABILITIES["android"]
        self._capabilities = base.model_copy(update={
            "supports_input_recording": touchscreen is not None or keys_observable,
            "supports_touch": touchscreen is not None,
            "supports_multi_touch": touchscreen is not None and touchscreen.uses_mt_protocol,
            "supports_hardware_keys": keys_observable,
            "supports_live_screen": True,
            "supports_screenshot": True,
            "limitations": (
                "Touches and hardware keys are observed via `adb shell getevent`; on-screen "
                "keyboard typing is recorded as taps.",
                "Pinch/rotate are recorded as multi-touch paths, not as device.pinch.",
                *(() if touchscreen is not None else
                  ("No touchscreen input device found — only keys are observed.",)),
            ),
        })
        self._connected = True
        self._lost = False
        self.diagnostics.update(
            adb=self._adb.available()[1], serial=self._serial, model=info.model or "",
            android_version=info.android_version or "",
            input_device=touchscreen.path if touchscreen else "",
            input_device_name=touchscreen.name if touchscreen else "",
            touchscreen=touchscreen is not None, screen_size=self._size,
            rotation=info.rotation, last_error="",
        )
        _log.info("android.device.connected serial=%s model=%s android=%s size=%sx%s",
                  self._serial, info.model, info.android_version, *self._size)
        if touchscreen is not None:
            _log.info("android.input_device.selected path=%s name=%s x=%s y=%s",
                      touchscreen.path, touchscreen.name, touchscreen.x_range(),
                      touchscreen.y_range())

    def disconnect(self) -> None:
        self.stop_recording()
        self._connected = False
        self._sink = None

    # -- observation ---------------------------------------------------------------------------

    def screenshot(self) -> Image:
        try:
            data = self._adb.screenshot(self._serial)
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
        if self.recording:
            raise RecordingError("Android recording is already running.")
        self._sink = sink
        self._stop.clear()
        self._restart_attempts = 0
        self.diagnostics.reset_counters()
        self._recognizer = AndroidGestureRecognizer(
            mapper=self._mapper, config=self._gesture_config,
            touch_device=self.touchscreen.path if self.touchscreen else None,
        )
        self._parser = GetEventParser()
        self._epoch = None
        self._start_stream()
        self._reader = threading.Thread(target=self._read_loop, name=f"getevent-{self._serial}",
                                        daemon=True)
        self._reader.start()

    def stop_recording(self) -> None:
        self._stop.set()
        stream = self._stream
        if stream is not None:
            stream.stop()
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=5)
        self._reader = None
        self._stream = None
        if self._connected and stream is not None:
            try:
                self._adb.stop_getevent(self._serial)
            except TargetConnectionError:
                pass
        recognizer = self._recognizer
        if recognizer is not None:
            for gesture in recognizer.flush():
                self._emit_gesture(gesture)
        self._recognizer = None
        self.diagnostics.update(stream_alive=False)
        if stream is not None:
            _log.info("android.getevent.stopped serial=%s raw=%s actions=%s", self._serial,
                      self.diagnostics.raw_events, self.diagnostics.recognized)
        self._sink = None

    def reconnect(self) -> None:
        """After a disconnect: verify the same device is back and resume the stream."""
        devices = self._adb.list_devices()
        if not any(d.serial == self._serial and d.usable for d in devices):
            raise TargetConnectionError(
                f"Device {self._serial} is not back yet.",
                remediation="Reconnect the cable, unlock the device, then try again.",
            )
        try:
            info = self._adb.get_device_info(self._serial)
            self.device_info = info
            if self._mapper is not None:
                self._mapper = self._mapper.with_rotation(info.rotation)
                self._size = self._mapper.screen_size
        except TargetConnectionError:
            pass
        self._lost = False
        self._connected = True
        self.diagnostics.update(last_error="")
        if self._sink is not None and self._recognizer is not None:
            self._recognizer.set_mapper(self._mapper)
            self._stop.clear()
            self._restart_attempts = 0
            self._emit(RecordingEventType.CONNECTION_RESTORED,
                       metadata={"serial": self._serial})
            self._start_stream()
            if not self.recording:
                self._reader = threading.Thread(target=self._read_loop,
                                                name=f"getevent-{self._serial}", daemon=True)
                self._reader.start()
        _log.info("android.device.reconnected serial=%s", self._serial)

    # -- getevent stream -----------------------------------------------------------------------

    def _start_stream(self) -> None:
        self._stream = self._adb.start_getevent(self._serial)
        self.diagnostics.update(stream_alive=True)
        _log.info("android.getevent.started serial=%s device=%s", self._serial,
                  self.touchscreen.path if self.touchscreen else "*")

    def _read_loop(self) -> None:
        last_rotation_check = time.monotonic()
        while not self._stop.is_set():
            stream = self._stream
            if stream is None:
                time.sleep(0.05)
                continue
            line = stream.readline(timeout=0.25)
            if line is None:
                if self._stop.is_set():
                    break
                if not self._handle_stream_end(stream):
                    break
                continue
            if line:
                self._handle_line(line)
            elif time.monotonic() - last_rotation_check >= ROTATION_POLL_S:
                last_rotation_check = time.monotonic()
                self._refresh_rotation()

    def _handle_line(self, line: str) -> None:
        recognizer = self._recognizer
        if recognizer is None:
            return
        event = self._parser.parse_line(line)
        self.diagnostics.update(malformed=self._parser.malformed, unknown=self._parser.unknown)
        if event is None:
            return
        self.diagnostics.count_raw()
        _log.debug("android.raw %s", line.rstrip())
        if self._epoch is None and event.timestamp is not None:
            self._epoch = datetime.now(UTC) - timedelta(seconds=event.timestamp)
        for gesture in recognizer.feed(event):
            self._emit_gesture(gesture)
        self.diagnostics.update(ignored=recognizer.ignored)

    def _handle_stream_end(self, stream: EventStream) -> bool:
        """The getevent process ended without us stopping it. Returns True to keep looping."""
        stderr = stream.stderr_text.strip()
        try:
            present = any(d.serial == self._serial and d.usable for d in self._adb.list_devices())
        except TargetConnectionError:
            present = False
        if present and self._restart_attempts < 2:
            self._restart_attempts += 1
            _log.warning("android.getevent.exited rc=%s stderr=%r — restarting",
                         stream.returncode, stderr[:200])
            time.sleep(STREAM_RESTART_DELAY_S)
            try:
                self._start_stream()
                return True
            except TargetConnectionError as exc:
                stderr = exc.message
        self._lost = True
        self._connected = False
        self.diagnostics.update(stream_alive=False,
                                last_error=stderr or "getevent stream ended")
        message = (f"Android device {self._serial} disconnected." if not present
                   else f"getevent stopped on {self._serial}: {stderr or 'unknown reason'}")
        _log.error("android.recording.error %s", message)
        self._emit(RecordingEventType.CONNECTION_LOST, metadata={
            "error": message, "serial": self._serial, "stderr": stderr[:500],
            "remediation": "Reconnect the device and press Reconnect, or stop the recording. "
                           "Everything recorded so far is kept.",
        })
        self._stream = None
        return False

    def _refresh_rotation(self) -> None:
        recognizer = self._recognizer
        mapper = self._mapper
        if recognizer is None or mapper is None or recognizer.in_gesture:
            return
        get_rotation = getattr(self._adb, "get_rotation", None)
        try:
            rotation = (get_rotation(self._serial) if callable(get_rotation)
                        else self._adb.get_device_info(self._serial).rotation)
        except TargetConnectionError:
            return
        if rotation != mapper.rotation:
            self._mapper = mapper.with_rotation(rotation)
            self._size = self._mapper.screen_size
            recognizer.set_mapper(self._mapper)
            self.diagnostics.update(rotation=rotation, screen_size=self._size)
            _log.info("android.display.rotated rotation=%s size=%sx%s", rotation, *self._size)

    # -- gestures → RecordingEvent ------------------------------------------------------------

    def _emit_gesture(self, gesture: RecognizedGesture) -> None:
        timestamp = self._wall_clock(gesture.timestamp)
        common: dict[str, Any] = {"timestamp": timestamp, "duration_ms": gesture.duration_ms}
        meta: dict[str, Any] = {"raw_event_count": gesture.raw_event_count, **gesture.metadata}
        description: str
        match gesture:
            case Tap(x=x, y=y):
                description = f"Tap ({x}, {y})"
                self._emit(RecordingEventType.GESTURE, position=Point(x=x, y=y), button="touch",
                           metadata={**meta, "gesture": "tap"}, **common)
            case LongPress(x=x, y=y):
                description = f"Long press ({x}, {y})"
                self._emit(RecordingEventType.GESTURE, position=Point(x=x, y=y), button="touch",
                           metadata={**meta, "gesture": "long_press"}, **common)
            case Swipe(start_x=sx, start_y=sy, end_x=ex, end_y=ey, path=path):
                description = f"Swipe ({sx}, {sy}) → ({ex}, {ey})"
                self._emit(RecordingEventType.GESTURE, position=Point(x=sx, y=sy),
                           position_end=Point(x=ex, y=ey), button="touch",
                           metadata={**meta, "gesture": "swipe",
                                     "path": [p.model_dump() for p in path]}, **common)
            case MultiTouch(fingers=fingers):
                description = f"Multi-touch ({len(fingers)} fingers)"
                first = fingers[0][0]
                self._emit(RecordingEventType.GESTURE, position=Point(x=first.x, y=first.y),
                           button="touch",
                           metadata={**meta, "gesture": "multi_touch",
                                     "fingers": [[p.model_dump() for p in path]
                                                 for path in fingers]}, **common)
            case KeyPress(key=key, linux_key=linux_key, mapped=mapped):
                description = f"Press {key}"
                self._emit(RecordingEventType.KEY_PRESS, key=key,
                           metadata={**meta, "linux_key": linux_key, "mapped": mapped,
                                     "source": "getevent"}, **common)
                if not mapped:
                    _log.warning("android.key.unmapped linux_key=%s", linux_key)
            case UnknownGesture(reason=reason):
                description = f"Unknown gesture ({reason})"
                self._emit(RecordingEventType.CUSTOM,
                           metadata={**meta, "gesture": "unknown", "reason": reason}, **common)
            case _:
                return
        self.diagnostics.gesture(description)
        _log.info("android.gesture.recognized %s duration=%sms raw=%s", description,
                  gesture.duration_ms, gesture.raw_event_count)

    def _wall_clock(self, kernel_seconds: float) -> datetime:
        if self._epoch is None:
            return datetime.now(UTC)
        return self._epoch + timedelta(seconds=kernel_seconds)

    def _emit(self, event_type: RecordingEventType, **fields: Any) -> None:
        sink = self._sink
        if sink is None:
            return
        if not sink.push(RecordingEvent(event_type=event_type, **fields)):
            self.diagnostics.update(dropped=sink.dropped)

    # -- controlled input (ControllableRecorder) ---------------------------------------------

    def send_tap(self, x: int, y: int) -> None:
        with self._lock:
            self._adb.shell(self._serial, "input", "tap", str(x), str(y))
            self._emit(RecordingEventType.CLICK, position=Point(x=x, y=y), button="touch",
                       metadata={"source": "creator"})

    def send_key(self, key: str) -> None:
        keycode = _KEYCODES.get(key.upper())
        with self._lock:
            if keycode is not None:
                self._adb.shell(self._serial, "input", "keyevent", keycode)
            elif len(key) == 1:
                self._adb.shell(self._serial, "input", "text", _escape(key))
            else:
                self._adb.shell(self._serial, "input", "keyevent", key)
            self._emit(RecordingEventType.KEY_PRESS, key=key, metadata={"source": "creator"})

    def send_text(self, text: str) -> None:
        for char in text:
            self.send_key("SPACE" if char == " " else char)

    def send_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        with self._lock:
            self._adb.shell(self._serial, "input", "swipe", str(x1), str(y1), str(x2), str(y2),
                            str(duration_ms))
            self._emit(RecordingEventType.GESTURE, position=Point(x=x1, y=y1),
                       position_end=Point(x=x2, y=y2), button="touch", duration_ms=duration_ms,
                       metadata={"gesture": "swipe", "source": "creator"})

    # -- helpers ---------------------------------------------------------------------------------

    def _build_mapper(
        self, info: AndroidDeviceInfo, touchscreen: AndroidInputDevice | None
    ) -> AndroidCoordinateMapper | None:
        if touchscreen is None:
            return None
        width, height = info.natural_width, info.natural_height
        if not width or not height:
            width, height = self.screenshot().size
            if info.rotation in (1, 3):
                width, height = height, width
        return AndroidCoordinateMapper.for_device(
            touchscreen, natural_width=width, natural_height=height, rotation=info.rotation,
            invert_x=bool(self._settings.get("invert_x", False)),
            invert_y=bool(self._settings.get("invert_y", False)),
            swap_axes=bool(self._settings.get("swap_axes", False)),
        )


def _choose_serial(requested: str, devices: list[AndroidDevice]) -> str:
    usable = [d for d in devices if d.usable]
    if requested:
        match = next((d for d in devices if d.serial == requested), None)
        if match is None:
            raise TargetConnectionError(
                f"Device {requested!r} is not connected.",
                remediation=("Connected devices: " + ", ".join(d.serial for d in devices))
                if devices else "Connect the device and check `adb devices`.",
            )
        if not match.usable:
            raise TargetConnectionError(
                f"Device {requested!r} is {match.state}.",
                remediation=_state_remediation(match.state),
            )
        return requested
    if not usable:
        if devices:
            states = ", ".join(f"{d.serial} ({d.state})" for d in devices)
            raise TargetConnectionError(
                f"No authorized Android device: {states}.",
                remediation=_state_remediation(devices[0].state),
            )
        raise TargetConnectionError(
            "No Android device is connected.",
            remediation="Enable USB debugging, connect the device, accept the prompt, then "
                        "check `adb devices`.",
        )
    if len(usable) > 1:
        raise TargetConnectionError(
            "Several Android devices are connected; choose one.",
            remediation="Select the device in the Android panel (or set the 'serial' target "
                        "setting) — one of: " + ", ".join(d.label() for d in usable),
        )
    return usable[0].serial


def _state_remediation(state: str) -> str:
    return {
        "unauthorized": "Unlock the device and accept the 'Allow USB debugging' prompt.",
        "offline": "Unplug and reconnect the cable; run `adb kill-server && adb devices`.",
    }.get(state, "Check `adb devices` shows the device as 'device'.")


def select_touchscreen(
    devices: list[AndroidInputDevice], *, override: str | None = None
) -> tuple[AndroidInputDevice | None, list[AndroidInputDevice]]:
    """Pick the touchscreen: explicit override, else direct MT panels, else any touch panel.

    Returns ``(selected, candidates)``. When several equally good candidates
    exist the first by path is used and *all* stay in ``candidates`` so the UI can
    offer the choice — nothing is hidden.
    """
    candidates = [d for d in devices if d.is_touchscreen]
    if override:
        chosen = next((d for d in devices if d.path == override or d.name == override), None)
        if chosen is None:
            raise TargetConnectionError(
                f"Input device {override!r} was not found on the device.",
                remediation="Available: " + ", ".join(f"{d.path} ({d.name})" for d in devices),
            )
        return chosen, candidates
    if not candidates:
        return None, []

    def rank(d: AndroidInputDevice) -> tuple[int, int, int, str]:
        x = d.x_range()
        return (0 if d.is_direct else 1, 0 if d.uses_mt_protocol else 1,
                -(x.span if x else 0), d.path)

    ordered = sorted(candidates, key=rank)
    return ordered[0], ordered


def _escape(text: str) -> str:
    return text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"').replace("&", "\\&")


def register(registry: RecorderRegistry) -> None:
    registry.register("android", AndroidRecorder)


__all__ = ["AndroidRecorder", "register", "select_touchscreen"]
