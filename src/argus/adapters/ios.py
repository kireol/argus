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
