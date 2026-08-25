"""iOS device adapter (WebDriverAgent over HTTP).

Drives an iOS app on a simulator or a physical device through a running
WebDriverAgent (https://github.com/appium/WebDriverAgent): app lifecycle,
screenshots and W3C-Actions touch input (tap, swipe, long press, drag,
multi-touch, pinch). Logs come from an optional ``log_command`` subprocess
(``xcrun simctl spawn ... log stream`` for simulators, ``idevicesyslog`` for
devices). Only the standard library is used.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import shlex
import subprocess
import threading
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from PIL import Image as PILImage
from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities, Point
from argus.config.models import DeviceConfig
from argus.exceptions import (
    ConfigurationError,
    DeviceConnectionError,
    ScreenshotError,
)
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo

_DEFAULT_URL = "http://127.0.0.1:8100"
_DEFAULT_TIMEOUT = 30.0
_MAX_LOG_LINES = 5000
_WDA_DOCS = "See docs/ios.md for building and running WebDriverAgent."


class WdaClient(Protocol):
    """The one call the adapter needs: a JSON request against WebDriverAgent."""

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


WdaClientFactory = Callable[[], WdaClient]


def _wda_error(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Return (error, message) when ``payload`` is a WebDriver error response."""
    value = payload.get("value")
    if isinstance(value, dict) and value.get("error"):
        return str(value["error"]), str(value.get("message", ""))
    return None


def _raise_for_wda_error(payload: dict[str, Any]) -> None:
    error = _wda_error(payload)
    if error is None:
        return
    name, message = error
    remediation = "Check the WebDriverAgent log for details."
    if "session" in name:
        remediation = "The WDA session is gone; reconnect the device (a new run does this)."
    raise DeviceConnectionError(
        f"WebDriverAgent error {name!r}: {message}".rstrip(": "), remediation=remediation
    )


class _HttpWdaClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self._base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            payload = _decode(raw)
            _raise_for_wda_error(payload)
            raise DeviceConnectionError(
                f"WebDriverAgent {method} {path} returned HTTP {exc.code}.",
                remediation=_WDA_DOCS,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DeviceConnectionError(
                f"Cannot reach WebDriverAgent at {self._base_url}: {exc}",
                remediation="Is WebDriverAgent running (xcodebuild ... test) and the port "
                f"forwarded? {_WDA_DOCS}",
            ) from exc
        payload = _decode(raw)
        _raise_for_wda_error(payload)
        return payload


def _decode(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeviceConnectionError(
            f"WebDriverAgent returned non-JSON data ({len(raw)} bytes).",
            remediation="Check the url points at WebDriverAgent, not another service.",
        ) from exc
    return payload if isinstance(payload, dict) else {"value": payload}


_DOWN = {"type": "pointerDown", "button": 0}
_UP = {"type": "pointerUp", "button": 0}

_BUTTONS = {"VOLUME_UP": "volumeUp", "VOLUME_DOWN": "volumeDown", "LOCK": "lock"}
_KEY_TEXT = {"ENTER": "\n", "DPAD_CENTER": "\n", "DEL": "\b", "BACKSPACE": "\b", "TAB": "\t",
             "SPACE": " "}


def _pause(duration_ms: int) -> dict[str, Any]:
    return {"type": "pause", "duration": duration_ms}


def _num(value: float) -> float | int:
    """Render whole numbers as ints so request bodies read like the docs."""
    return int(value) if float(value).is_integer() else value


Spawner = Callable[[list[str]], Any]


def _subprocess_spawner(argv: list[str]) -> Any:
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


class IosAdapter(Device):
    """Controls an iOS app through WebDriverAgent."""

    def __init__(
        self,
        name: str,
        *,
        bundle_id: str,
        url: str = _DEFAULT_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        log_command: str | None = None,
        client_factory: WdaClientFactory | None = None,
        spawner: Spawner | None = None,
    ) -> None:
        super().__init__(name)
        self._bundle_id = bundle_id
        self._url = url
        self._timeout = float(timeout)
        self._log_command = log_command
        self._client: WdaClient = (
            client_factory() if client_factory else _HttpWdaClient(url, self._timeout)
        )
        self._spawn: Spawner = spawner or _subprocess_spawner
        self._session_id: str | None = None
        self._scale: float | None = None
        self._screen_info: ScreenInfo | None = None
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._log_process: Any = None
        self._log_pump: threading.Thread | None = None
        self._log = get_logger("argus.ios", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> IosAdapter:
        options: dict[str, Any] = config.options
        bundle_id = options.get("bundle_id")
        if not bundle_id:
            raise ConfigurationError(
                f"iOS device {name!r} needs a bundle_id.",
                remediation="Set devices.<name>.bundle_id (e.g. com.example.app).",
            )
        return cls(
            name,
            bundle_id=str(bundle_id),
            url=str(options.get("url", _DEFAULT_URL)),
            timeout=float(options.get("timeout", _DEFAULT_TIMEOUT)),
            log_command=options.get("log_command"),
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
            supports_app_lifecycle=True,
            supports_logs=self._log_command is not None,
        )

    @property
    def platform(self) -> str:
        return "ios"

    # -- session plumbing ---------------------------------------------------------------

    def _require_session(self) -> str:
        if self._session_id is None:
            raise DeviceConnectionError(
                f"iOS device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        return self._session_id

    def _session_path(self, suffix: str) -> str:
        return f"/session/{self._require_session()}{suffix}"

    def _post(self, suffix: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._client.request("POST", self._session_path(suffix), body or {})

    # -- connection -------------------------------------------------------------------------

    def connect(self) -> None:
        self._client.request("GET", "/status")
        payload = self._client.request(
            "POST",
            "/session",
            {"capabilities": {"alwaysMatch": {"bundleId": self._bundle_id}}},
        )
        session_id = payload.get("sessionId") or (payload.get("value") or {}).get("sessionId")
        if not session_id:
            raise DeviceConnectionError(
                "WebDriverAgent did not return a session id.",
                remediation=f"Check the WDA log; is the app installed? {_WDA_DOCS}",
            )
        self._session_id = str(session_id)
        self._scale = None
        self._screen_info = None

    def disconnect(self) -> None:
        session_id, self._session_id = self._session_id, None
        if session_id is not None:
            with contextlib.suppress(DeviceConnectionError):
                self._client.request("DELETE", f"/session/{session_id}")

    def is_available(self) -> bool:
        try:
            self._client.request("GET", "/status")
        except DeviceConnectionError:
            return False
        return True

    def health_check(self) -> HealthCheckResult:
        try:
            status = self._client.request("GET", "/status").get("value") or {}
            details: dict[str, Any] = {"url": self._url, "wda_state": status.get("state")}
            if self._session_id is not None:
                details["app_running"] = self.is_application_running()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(str(exc))
        return HealthCheckResult.ok("WebDriverAgent responsive", **details)

    # -- application lifecycle ----------------------------------------------------------

    def is_application_running(self) -> bool:
        state = self._post("/wda/apps/state", {"bundleId": self._bundle_id}).get("value")
        return state == 4

    def start_application(self) -> None:
        self._post("/wda/apps/launch", {"bundleId": self._bundle_id})

    def stop_application(self) -> None:
        self._post("/wda/apps/terminate", {"bundleId": self._bundle_id})

    def reset_application(self) -> None:
        # WebDriverAgent cannot wipe app data; a cold relaunch is the closest reset.
        self.stop_application()
        self.start_application()

    # -- observation -------------------------------------------------------------------------

    def screenshot(self) -> Image:
        payload = self._client.request("GET", "/screenshot")
        encoded = payload.get("value") or ""
        try:
            data = base64.b64decode(encoded)
            with PILImage.open(io.BytesIO(data)) as img:
                return img.convert("RGB")
        except (ValueError, OSError) as exc:
            raise ScreenshotError(
                f"WebDriverAgent screenshot is not a valid PNG ({len(encoded)} chars).",
                remediation="Check the device is unlocked and WDA is attached to it.",
            ) from exc

    def _pixel_scale(self) -> float:
        if self._scale is None:
            screen = self._client.request("GET", self._session_path("/wda/screen"))
            value = (screen.get("value") or {}).get("scale")
            self._scale = float(value) if value else 1.0
        return self._scale

    def get_screen_info(self) -> ScreenInfo:
        if self._screen_info is None:
            size = self._client.request("GET", self._session_path("/window/size")).get("value")
            size = size or {}
            scale = self._pixel_scale()
            self._screen_info = ScreenInfo(
                width=round(float(size.get("width", 0)) * scale),
                height=round(float(size.get("height", 0)) * scale),
            )
        return self._screen_info

    def _to_points(self, point: Point) -> tuple[float, float]:
        scale = self._pixel_scale()
        return (point[0] / scale, point[1] / scale)

    # -- input (W3C Actions) --------------------------------------------------------------

    def _move(self, point: Point, duration_ms: int) -> dict[str, Any]:
        x, y = self._to_points(point)
        return {"type": "pointerMove", "duration": duration_ms, "x": _num(x), "y": _num(y)}

    @staticmethod
    def _finger(index: int, actions: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "type": "pointer",
            "id": f"finger{index}",
            "parameters": {"pointerType": "touch"},
            "actions": actions,
        }

    def _perform(self, sources: list[dict[str, Any]]) -> None:
        self._post("/actions", {"actions": sources})
        with contextlib.suppress(DeviceConnectionError):
            self._client.request("DELETE", self._session_path("/actions"))

    def tap(self, x: int, y: int) -> None:
        self._perform([self._finger(0, [self._move((x, y), 0), _DOWN, _UP])])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._perform(
            [
                self._finger(
                    0,
                    [self._move((x1, y1), 0), _DOWN, self._move((x2, y2), duration_ms), _UP],
                )
            ]
        )

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        self._perform(
            [self._finger(0, [self._move((x, y), 0), _DOWN, _pause(duration_ms), _UP])]
        )

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, hold_ms: int = 500, duration_ms: int = 500
    ) -> None:
        self._perform(
            [
                self._finger(
                    0,
                    [
                        self._move((x1, y1), 0),
                        _DOWN,
                        _pause(hold_ms),
                        self._move((x2, y2), duration_ms),
                        _UP,
                    ],
                )
            ]
        )

    def multi_touch(self, fingers: Sequence[Sequence[Point]], duration_ms: int = 500) -> None:
        sources = []
        for index, path in enumerate(fingers):
            segments = max(1, len(path) - 1)
            segment_ms = round(duration_ms / segments)
            actions = [self._move(path[0], 0), _DOWN]
            actions += [self._move(point, segment_ms) for point in path[1:]]
            actions.append(_UP)
            sources.append(self._finger(index, actions))
        self._perform(sources)

    def press_key(self, key: str) -> None:
        name = key.removeprefix("KEYCODE_")
        upper = name.upper()
        if upper == "HOME":
            self._post("/wda/homescreen", {})
        elif upper in _BUTTONS:
            self._post("/wda/pressButton", {"name": _BUTTONS[upper]})
        else:
            text = _KEY_TEXT.get(upper, name)
            self._post("/wda/keys", {"value": list(text)})
