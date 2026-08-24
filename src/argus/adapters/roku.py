"""Roku device adapter (developer mode).

Control goes through Roku's External Control Protocol (ECP, port 8060):
key presses, launching the sideloaded ``dev`` channel, and device queries.
Screenshots and channel sideloading use the developer web installer (port 80,
Digest auth as user ``rokudev``) and therefore only work for the sideloaded
channel on a Roku with developer mode enabled. The BrightScript debug console
(telnet, port 8085) is streamed into the device log buffer so ``log_contains``
works. Stdlib only — no optional dependency.
"""

from __future__ import annotations

import contextlib
import io
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceConnectionError, ScreenshotError
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo

_DEFAULT_TIMEOUT = 10.0
_MAX_LOG_LINES = 5000
_DEV_APP_ID = "dev"
_DEV_USER = "rokudev"

# Roku responses are parsed with the stdlib parser: Python 3.12's ElementTree does not
# resolve external entities, and the Roku is a trusted LAN device.

_RESOLUTIONS = {
    "480p": (720, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2160p": (3840, 2160),
    "4k": (3840, 2160),
}

# Android-style key names -> ECP key names. Unknown names pass through unchanged
# (ECP names such as "InstantReplay" or "Search" work directly); single
# characters become literal text input (Lit_<char>).
_KEY_MAP = {
    "DPAD_UP": "Up",
    "DPAD_DOWN": "Down",
    "DPAD_LEFT": "Left",
    "DPAD_RIGHT": "Right",
    "UP": "Up",
    "DOWN": "Down",
    "LEFT": "Left",
    "RIGHT": "Right",
    "ENTER": "Select",
    "DPAD_CENTER": "Select",
    "SELECT": "Select",
    "BACK": "Back",
    "HOME": "Home",
    "MEDIA_PLAY_PAUSE": "Play",
    "MEDIA_PLAY": "Play",
    "MEDIA_PAUSE": "Play",
    "MEDIA_REWIND": "Rev",
    "MEDIA_FAST_FORWARD": "Fwd",
    "INFO": "Info",
    "DEL": "Backspace",
    "BACKSPACE": "Backspace",
    "SEARCH": "Search",
}


def _encode_multipart(
    fields: dict[str, str], files: dict[str, tuple[str, bytes]]
) -> tuple[bytes, str]:
    """Build a multipart/form-data body (the dev installer is a plain HTML form)."""
    boundary = f"----argus{uuid.uuid4().hex}"
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(value.encode())
        out.write(b"\r\n")
    for name, (filename, data) in files.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        out.write(b"Content-Type: application/octet-stream\r\n\r\n")
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


class _DebugConsoleReader(threading.Thread):
    """Streams the BrightScript debug console into a bounded deque.

    Reconnects with backoff: the Roku drops the console connection whenever
    the channel is (re)launched. Failures are logged at debug level only.
    """

    def __init__(self, host: str, port: int, sink: deque[str], log: Any) -> None:
        super().__init__(daemon=True, name=f"roku-console-{host}")
        self._host = host
        self._port = port
        self._sink = sink
        self._log = log
        self._stop_event = threading.Event()
        self._sock: socket.socket | None = None

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()

    def run(self) -> None:
        backoff = 0.5
        while not self._stop_event.is_set():
            try:
                # A short per-attempt timeout keeps the stop-event check below responsive
                # even when the console host blackholes packets instead of refusing.
                sock = socket.create_connection((self._host, self._port), timeout=1.0)
            except OSError as exc:
                self._log.debug("Roku debug console unavailable: %s", exc)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, 10.0)
                continue
            self._sock = sock
            backoff = 0.5
            try:
                self._pump(sock)
            except OSError as exc:
                self._log.debug("Roku debug console dropped: %s", exc)
            finally:
                with contextlib.suppress(OSError):
                    sock.close()
                self._sock = None

    def _pump(self, sock: socket.socket) -> None:
        sock.settimeout(1.0)
        buffer = b""
        while not self._stop_event.is_set():
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                continue
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if self._stop_event.is_set():
                    # Dropped, not appended: stop() may have already fired and a stale
                    # reader from a prior session must never write into a live deque.
                    continue
                self._sink.append(line.decode("utf-8", errors="replace").rstrip("\r"))


class RokuAdapter(Device):
    """Controls a developer-mode Roku running a sideloaded channel."""

    def __init__(
        self,
        name: str,
        *,
        host: str,
        dev_password: str | None = None,
        channel_zip: str | Path | None = None,
        ecp_port: int = 8060,
        debug_port: int = 8085,
        installer_port: int = 80,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(name)
        if channel_zip is not None and not dev_password:
            raise ConfigurationError(
                f"Roku device {name!r}: 'channel_zip' requires 'dev_password'.",
                remediation="Set devices.<name>.dev_password to the developer-mode password.",
            )
        self._host = host
        self._dev_password = dev_password
        self._channel_zip = Path(channel_zip) if channel_zip is not None else None
        self._ecp_port = int(ecp_port)
        self._debug_port = int(debug_port)
        self._installer_port = int(installer_port)
        self._timeout = float(timeout)
        self._connected = False
        self._app_running = False
        self._resolution: tuple[int, int] = (1920, 1080)
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._reader: _DebugConsoleReader | None = None
        self._log = get_logger("argus.roku", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> RokuAdapter:
        options: dict[str, Any] = config.options
        host = options.get("host")
        if not host:
            raise ConfigurationError(
                f"Roku device {name!r} requires a 'host' option.",
                remediation="Set devices.<name>.host to the Roku's IP address.",
            )
        return cls(
            name,
            host=str(host),
            dev_password=options.get("dev_password"),
            channel_zip=options.get("channel_zip"),
            ecp_port=int(options.get("ecp_port", 8060)),
            debug_port=int(options.get("debug_port", 8085)),
            installer_port=int(options.get("installer_port", 80)),
            timeout=float(options.get("timeout", _DEFAULT_TIMEOUT)),
        )

    # -- identity -----------------------------------------------------------------

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=bool(self._dev_password),
            supports_keyboard=True,
            supports_app_lifecycle=True,
            supports_logs=True,
            supports_instrumentation=True,
        )

    @property
    def platform(self) -> str:
        return "roku"

    # -- HTTP plumbing ----------------------------------------------------------------

    def _ecp(self, method: str, path: str) -> bytes:
        url = f"http://{self._host}:{self._ecp_port}/{path}"
        request = urllib.request.Request(
            url, method=method, data=b"" if method == "POST" else None
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return bytes(response.read())
        except (urllib.error.URLError, OSError) as exc:
            raise DeviceConnectionError(
                f"Roku {self._host!r}: {method} /{path} failed: {exc}",
                remediation="Check the Roku is powered on, reachable on the network, and "
                "'host'/'ecp_port' are correct.",
            ) from exc

    @property
    def _installer_url(self) -> str:
        return f"http://{self._host}:{self._installer_port}"

    def _installer(self, path: str, body: bytes | None, content_type: str | None) -> bytes:
        if not self._dev_password:
            raise DeviceConnectionError(
                f"Roku {self._host!r}: developer installer needs 'dev_password'.",
                remediation="Enable developer mode on the Roku and set "
                "devices.<name>.dev_password.",
            )
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, self._installer_url, _DEV_USER, self._dev_password)
        opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(manager))
        headers = {"Content-Type": content_type} if content_type else {}
        request = urllib.request.Request(
            self._installer_url + path,
            data=body,
            headers=headers,
            method="POST" if body is not None else "GET",
        )
        try:
            with opener.open(request, timeout=self._timeout) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise DeviceConnectionError(
                    f"Roku {self._host!r}: developer password rejected.",
                    remediation="Check devices.<name>.dev_password (user 'rokudev').",
                ) from exc
            raise DeviceConnectionError(
                f"Roku {self._host!r}: installer {path} returned HTTP {exc.code}.",
                remediation="Check developer mode is enabled (Home x3, Up x2, Right, Left, "
                "Right, Left, Right).",
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DeviceConnectionError(
                f"Roku {self._host!r}: installer {path} failed: {exc}",
                remediation="Check developer mode is enabled and port "
                f"{self._installer_port} is reachable.",
            ) from exc

    def _installer_form(
        self, path: str, fields: dict[str, str], files: dict[str, tuple[str, bytes]]
    ) -> bytes:
        body, content_type = _encode_multipart(fields, files)
        return self._installer(path, body, content_type)

    def _require_connected(self) -> None:
        if not self._connected:
            raise DeviceConnectionError(
                f"Roku device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )

    def _device_info(self) -> dict[str, str]:
        data = self._ecp("GET", "query/device-info")
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise DeviceConnectionError(
                f"Roku {self._host!r}: /query/device-info returned malformed XML.",
                remediation="Check 'host'/'ecp_port' point at a Roku's ECP port (8060).",
            ) from exc
        return {child.tag: (child.text or "").strip() for child in root}

    # -- connection -----------------------------------------------------------------

    def connect(self) -> None:
        if self._connected:
            return
        info = self._device_info()
        self._resolution = _RESOLUTIONS.get(info.get("ui-resolution", "").lower(), (1920, 1080))
        if self._channel_zip is not None:
            self._sideload(self._channel_zip)
        self._reader = _DebugConsoleReader(self._host, self._debug_port, self._logs, self._log)
        self._reader.start()
        self._connected = True
        self._log.info("Connected to Roku %s (%s)", self._host, info.get("model-name", "?"))

    def disconnect(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.stop()
            reader.join(timeout=2.0)
        self._connected = False
        self._app_running = False

    def is_available(self) -> bool:
        return True

    def health_check(self) -> HealthCheckResult:
        try:
            info = self._device_info()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(str(exc))
        return HealthCheckResult.ok(
            "roku reachable",
            model=info.get("model-name", ""),
            software=info.get("software-version", ""),
            developer_enabled=info.get("developer-enabled", ""),
            connected=self._connected,
        )

    def _sideload(self, archive: Path) -> None:
        if not archive.is_file():
            raise ConfigurationError(
                f"Roku device {self.name!r}: channel_zip {str(archive)!r} not found.",
                remediation="Point devices.<name>.channel_zip at the packaged channel .zip.",
            )
        self._log.info("Sideloading %s", archive.name)
        self._installer_form(
            "/plugin_install",
            {"mysubmit": "Install"},
            {"archive": (archive.name, archive.read_bytes())},
        )

    # -- application lifecycle --------------------------------------------------------

    def start_application(self) -> None:
        self._require_connected()
        self._logs.clear()
        self._ecp("POST", f"launch/{_DEV_APP_ID}")
        self._app_running = True

    def stop_application(self) -> None:
        self._require_connected()
        self._ecp("POST", "keypress/Home")
        self._app_running = False

    def is_application_running(self) -> bool:
        self._require_connected()
        data = self._ecp("GET", "query/active-app")
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise DeviceConnectionError(
                f"Roku {self._host!r}: /query/active-app returned malformed XML.",
                remediation="Check 'host'/'ecp_port' point at a Roku's ECP port (8060).",
            ) from exc
        app = root.find("app")
        return app is not None and app.get("id") == _DEV_APP_ID

    # -- observation --------------------------------------------------------------------

    def screenshot(self) -> Image:
        if not self._dev_password:
            raise self._unsupported("screenshot")
        self._require_connected()
        try:
            self._installer_form(
                "/plugin_inspect", {"mysubmit": "Screenshot"}, {"archive": ("", b"")}
            )
            data = self._installer("/pkgs/dev.jpg", None, None)
            with PILImage.open(io.BytesIO(data)) as img:
                return img.convert("RGB")
        except DeviceConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001 - any decode failure
            raise ScreenshotError(
                f"Roku screenshot failed: {exc}",
                remediation="Screenshots only work for the sideloaded channel while it runs.",
            ) from exc

    def get_screen_info(self) -> ScreenInfo:
        return ScreenInfo(width=self._resolution[0], height=self._resolution[1])

    def get_logs(self, lines: int = 200) -> str:
        entries = list(self._logs)[-lines:] if lines > 0 else []
        return "\n".join(entries)

    # -- input ----------------------------------------------------------------------------

    def press_key(self, key: str) -> None:
        self._require_connected()
        name = key.removeprefix("KEYCODE_")
        if len(name) == 1:
            ecp_key = "Lit_" + urllib.parse.quote(name, safe="")
        else:
            ecp_key = urllib.parse.quote(_KEY_MAP.get(name.upper(), name), safe="")
        self._ecp("POST", f"keypress/{ecp_key}")
