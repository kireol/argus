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
