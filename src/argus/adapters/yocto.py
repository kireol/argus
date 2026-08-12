"""Yocto / embedded Linux adapter (SSH transport, pluggable screenshots).

Uses paramiko (pure-Python SSH) so Windows hosts work without a local ssh
binary. No display stack is assumed: screenshot acquisition is a configurable
provider (command-based by default, so framebuffer/Weston/X11/custom services
are all just configuration).
"""

from __future__ import annotations

import io
import shlex
from typing import TYPE_CHECKING, Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities, ScreenshotProvider
from argus.config.models import DeviceConfig
from argus.exceptions import (
    ConfigurationError,
    DeviceConnectionError,
    ScreenshotError,
)
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo

if TYPE_CHECKING:
    import paramiko

_DEFAULT_TIMEOUT = 15.0


class SSHTransport:
    """Thin wrapper around paramiko with timeouts and clear errors."""

    def __init__(
        self,
        host: str,
        *,
        port: int = 22,
        username: str = "root",
        password: str | None = None,
        private_key: str | None = None,
        connect_timeout: float = _DEFAULT_TIMEOUT,
        command_timeout: float = 30.0,
        host_key_policy: str = "reject",
        known_hosts: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self._password = password
        self._private_key = private_key
        self._connect_timeout = connect_timeout
        self._command_timeout = command_timeout
        self._host_key_policy = host_key_policy
        self._known_hosts = known_hosts
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        try:
            import paramiko
        except ImportError as exc:
            raise DeviceConnectionError(
                "paramiko is not installed (required for Yocto/SSH devices).",
                remediation='Install SSH support: pip install "argus[yocto]"',
            ) from exc

        client = paramiko.SSHClient()
        if self._known_hosts:
            client.load_host_keys(self._known_hosts)
        else:
            client.load_system_host_keys()
        # Secure by default: unknown host keys are rejected unless the user
        # explicitly opts into auto-accept for lab devices.
        if self._host_key_policy == "auto_add":
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        elif self._host_key_policy == "reject":
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            raise ConfigurationError(
                f"Invalid host_key_policy {self._host_key_policy!r}.",
                remediation="Use 'reject' (default, secure) or 'auto_add'.",
            )
        try:
            client.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self._password,
                key_filename=self._private_key,
                timeout=self._connect_timeout,
                allow_agent=True,
                look_for_keys=self._private_key is None,
            )
        except Exception as exc:  # noqa: BLE001 - normalize the many paramiko errors
            raise DeviceConnectionError(
                f"SSH connection to {self.username}@{self.host}:{self.port} "
                f"failed: {exc}",
                remediation="Check host/port/credentials, and host_key_policy if "
                "this is a new device.",
            ) from exc
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def connected(self) -> bool:
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def execute(
        self, command: str, *, timeout: float | None = None, binary: bool = False
    ) -> tuple[int, bytes | str, str]:
        """Run a command; returns (exit_code, stdout, stderr)."""
        if self._client is None:
            raise DeviceConnectionError(
                f"SSH transport to {self.host} is not connected."
            )
        try:
            _, stdout, stderr = self._client.exec_command(
                command, timeout=timeout or self._command_timeout
            )
            exit_code = stdout.channel.recv_exit_status()
            out_bytes = stdout.read()
            err = stderr.read().decode(errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise DeviceConnectionError(
                f"SSH command on {self.host} failed: {command!r}: {exc}",
                remediation="Check connectivity; the device may have rebooted.",
            ) from exc
        out: bytes | str = out_bytes if binary else out_bytes.decode(errors="replace")
        return exit_code, out, err


class CommandScreenshotProvider(ScreenshotProvider):
    """Runs a configurable command on the device and reads the image back.

    Works for any display stack — the command is configuration::

        screenshot:
          command: "weston-screenshooter -f {path}"   # or grim, fbgrab, custom
          remote_path: /tmp/utf_screenshot.png
    """

    def __init__(
        self,
        transport: SSHTransport,
        command: str,
        remote_path: str = "/tmp/utf_screenshot.png",
        timeout: float = 20.0,
    ) -> None:
        self._transport = transport
        self._command = command
        self._remote_path = remote_path
        self._timeout = timeout

    def capture(self) -> Image:
        command = self._command.format(path=shlex.quote(self._remote_path))
        exit_code, _, stderr = self._transport.execute(command, timeout=self._timeout)
        if exit_code != 0:
            raise ScreenshotError(
                f"Screenshot command failed (exit {exit_code}): {stderr.strip()}",
                remediation="Check the screenshot.command configuration for this device.",
            )
        exit_code, data, stderr = self._transport.execute(
            f"cat {shlex.quote(self._remote_path)}", binary=True, timeout=self._timeout
        )
        assert isinstance(data, bytes)
        if exit_code != 0 or not data:
            raise ScreenshotError(
                f"Unable to read screenshot file {self._remote_path}: {stderr.strip()}"
            )
        try:
            with PILImage.open(io.BytesIO(data)) as img:
                return img.convert("RGB")
        except OSError as exc:
            raise ScreenshotError(
                f"Screenshot file {self._remote_path} is not a valid image "
                f"({len(data)} bytes).",
            ) from exc


class YoctoAdapter(Device):
    """Generic SSH-controlled embedded Linux device."""

    def __init__(
        self,
        name: str,
        transport: SSHTransport,
        *,
        screenshot_provider: ScreenshotProvider | None = None,
        app_start: str | None = None,
        app_stop: str | None = None,
        app_process: str | None = None,
        log_command: str | None = None,
        screen_size: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(name)
        self._transport = transport
        self._screenshot_provider = screenshot_provider
        self._app_start = app_start
        self._app_stop = app_stop
        self._app_process = app_process
        self._log_command = log_command
        self._screen_size = screen_size
        self._log = get_logger("argus.yocto", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> YoctoAdapter:
        options: dict[str, Any] = config.options
        host = options.get("host")
        if not host or "${" in str(host):
            raise ConfigurationError(
                f"Yocto device {name!r} has no host configured.",
                remediation="Set devices.<name>.host (e.g. via YOCTO_HOST).",
            )
        transport = SSHTransport(
            str(host),
            port=int(options.get("port", 22)),
            username=str(options.get("username", "root")),
            password=options.get("password"),
            private_key=options.get("private_key"),
            connect_timeout=float(options.get("connect_timeout", _DEFAULT_TIMEOUT)),
            command_timeout=float(options.get("command_timeout", 30.0)),
            host_key_policy=str(options.get("host_key_policy", "reject")),
            known_hosts=options.get("known_hosts"),
        )
        screenshot_cfg = options.get("screenshot") or {}
        provider: ScreenshotProvider | None = None
        if screenshot_cfg.get("command"):
            provider = CommandScreenshotProvider(
                transport,
                command=str(screenshot_cfg["command"]),
                remote_path=str(
                    screenshot_cfg.get("remote_path", "/tmp/utf_screenshot.png")
                ),
                timeout=float(screenshot_cfg.get("timeout", 20.0)),
            )
        app_cfg = options.get("app") or {}
        size = options.get("screen_size")
        return cls(
            name,
            transport,
            screenshot_provider=provider,
            app_start=app_cfg.get("start"),
            app_stop=app_cfg.get("stop"),
            app_process=app_cfg.get("process"),
            log_command=options.get("log_command"),
            screen_size=(int(size[0]), int(size[1])) if size else None,
        )

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=self._screenshot_provider is not None,
            supports_app_lifecycle=bool(self._app_start and self._app_stop),
            supports_logs=self._log_command is not None,
            supports_instrumentation=True,
        )

    @property
    def platform(self) -> str:
        return "yocto"

    # -- connection ---------------------------------------------------------------------

    def connect(self) -> None:
        self._transport.connect()
        self._log.info("Connected to %s", self._transport.host)

    def disconnect(self) -> None:
        self._transport.close()

    def is_available(self) -> bool:
        return self._transport.connected

    def health_check(self) -> HealthCheckResult:
        if not self._transport.connected:
            try:
                self._transport.connect()
            except DeviceConnectionError as exc:
                return HealthCheckResult.failed(str(exc))
        exit_code, output, _ = self._transport.execute("uname -a", timeout=10.0)
        if exit_code != 0:
            return HealthCheckResult.failed("Cannot execute commands over SSH")
        details: dict[str, Any] = {"uname": str(output).strip()}
        if self._app_process:
            details["app_running"] = self.is_application_running()
        return HealthCheckResult.ok("Yocto device responsive", **details)

    # -- application lifecycle --------------------------------------------------------------

    def _require(self, value: str | None, setting: str) -> str:
        if not value:
            raise ConfigurationError(
                f"Yocto device {self.name!r} has no {setting} configured.",
                remediation=f"Set devices.<name>.app.{setting} in configuration.",
            )
        return value

    def start_application(self) -> None:
        command = self._require(self._app_start, "start")
        exit_code, _, stderr = self._transport.execute(command)
        if exit_code != 0:
            raise DeviceConnectionError(
                f"Application start failed (exit {exit_code}): {stderr.strip()}"
            )

    def stop_application(self) -> None:
        command = self._require(self._app_stop, "stop")
        exit_code, _, stderr = self._transport.execute(command)
        if exit_code != 0:
            raise DeviceConnectionError(
                f"Application stop failed (exit {exit_code}): {stderr.strip()}"
            )

    def is_application_running(self) -> bool:
        process = self._require(self._app_process, "process")
        quoted = shlex.quote(process)
        exit_code, _, _ = self._transport.execute(
            f"pidof {quoted} >/dev/null 2>&1 || pgrep -f {quoted} >/dev/null"
        )
        return exit_code == 0

    # -- observation ---------------------------------------------------------------------------

    def screenshot(self) -> Image:
        if self._screenshot_provider is None:
            raise ScreenshotError(
                f"Yocto device {self.name!r} has no screenshot provider configured.",
                remediation="Set devices.<name>.screenshot.command "
                '(e.g. "weston-screenshooter -f {path}" or a framebuffer grab).',
            )
        return self._screenshot_provider.capture()

    def get_screen_info(self) -> ScreenInfo:
        if self._screen_size:
            return ScreenInfo(width=self._screen_size[0], height=self._screen_size[1])
        # Try common sources without assuming a display stack.
        exit_code, output, _ = self._transport.execute(
            "cat /sys/class/graphics/fb0/virtual_size 2>/dev/null"
        )
        if exit_code == 0 and "," in str(output):
            width, height = (int(v) for v in str(output).strip().split(","))
            return ScreenInfo(width=width, height=height)
        raise ScreenshotError(
            f"Cannot determine screen size for {self.name!r}.",
            remediation="Set devices.<name>.screen_size: [width, height].",
        )

    def get_logs(self, lines: int = 200) -> str:
        command = self._log_command or (
            f"journalctl -n {lines} --no-pager 2>/dev/null || dmesg | tail -n {lines}"
        )
        _, output, _ = self._transport.execute(command)
        return str(output)

    def execute(self, command: str, timeout: float | None = None) -> tuple[int, str, str]:
        """Arbitrary command execution (used by custom preflight checks/tools)."""
        exit_code, output, stderr = self._transport.execute(command, timeout=timeout)
        return exit_code, str(output), stderr
