"""Byte transports for the ESP32 adapter: USB serial (pyserial) and Wokwi CLI pipes."""

from __future__ import annotations

import contextlib
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from argus.exceptions import DeviceConnectionError


class Transport(Protocol):
    """Minimal duplex byte stream with a board-reset hook."""

    def read(self, size: int, timeout: float) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def reset(self) -> None: ...
    def close(self) -> None: ...

    @property
    def description(self) -> str: ...


def serial_available() -> bool:
    try:
        import serial  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        return False
    return True


def wokwi_available() -> bool:
    return shutil.which("wokwi-cli") is not None


def _default_serial_factory(port: str, baud: int) -> Any:
    try:
        import serial
    except ImportError as exc:
        raise DeviceConnectionError(
            "pyserial is not installed (required for esp32 serial transport).",
            remediation='Install ESP32 support: pip install "argus[esp32]"',
        ) from exc
    return serial.Serial(port, baud, timeout=0)


class SerialTransport:
    """USB-serial link to a board. ``reset()`` toggles DTR/RTS like esptool does."""

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        *,
        usb_cdc: bool = False,
        serial_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        self._port = port
        self._baud = baud
        self._usb_cdc = usb_cdc
        factory = serial_factory or _default_serial_factory
        try:
            self._serial = factory(port, baud)
        except DeviceConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001 - pyserial raises SerialException/OSError
            raise DeviceConnectionError(
                f"Unable to open serial port {port!r}: {exc}",
                remediation="Check the board is plugged in and the port name "
                "(ls /dev/cu.* on macOS, /dev/ttyUSB* or /dev/ttyACM* on Linux).",
            ) from exc

    @property
    def description(self) -> str:
        return f"serial {self._port} @ {self._baud}"

    def read(self, size: int, timeout: float) -> bytes:
        self._serial.timeout = timeout
        return bytes(self._serial.read(size))

    def write(self, data: bytes) -> None:
        self._serial.write(data)
        self._serial.flush()

    def reset(self) -> None:
        if self._usb_cdc:
            # Native USB-CDC (ESP32-C3/S3): both lines high first, then the classic pulse.
            self._serial.rts = True
            self._serial.dtr = True
            time.sleep(0.05)
        self._serial.dtr = False
        self._serial.rts = True
        time.sleep(0.1)
        self._serial.rts = False

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._serial.close()


def _default_spawn(argv: list[str]) -> Any:
    return subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )


class WokwiTransport:
    """Runs ``wokwi-cli --interactive`` and treats its pipes as the simulated UART."""

    def __init__(
        self,
        project_dir: str | Path,
        *,
        timeout_ms: int = 600_000,
        spawn: Callable[[list[str]], Any] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._project_dir = Path(project_dir)
        self._timeout_ms = timeout_ms
        self._spawn = spawn or _default_spawn
        environment = os.environ if env is None else env
        if not environment.get("WOKWI_CLI_TOKEN"):
            raise DeviceConnectionError(
                "WOKWI_CLI_TOKEN is not set (required for the wokwi transport).",
                remediation="Create a token at https://wokwi.com/dashboard/ci and export "
                "WOKWI_CLI_TOKEN=wok_...",
            )
        self._process: Any = None
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._pending = b""
        self._reader: threading.Thread | None = None
        self._start()

    @property
    def description(self) -> str:
        return f"wokwi {self._project_dir}"

    def _start(self) -> None:
        argv = [
            "wokwi-cli",
            "--interactive",
            "--timeout",
            str(self._timeout_ms),
            str(self._project_dir),
        ]
        try:
            self._process = self._spawn(argv)
        except FileNotFoundError as exc:
            raise DeviceConnectionError(
                "wokwi-cli not found on PATH.",
                remediation="Install it: curl -L https://wokwi.com/ci/install.sh | sh",
            ) from exc
        self._reader = threading.Thread(target=self._pump, daemon=True, name="wokwi-stdout")
        self._reader.start()

    def _pump(self) -> None:
        stream = self._process.stdout
        process = self._process
        read1 = getattr(stream, "read1", None)
        while True:
            try:
                chunk = read1(4096) if read1 is not None else stream.read(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            if process is not self._process:
                break
            self._queue.put(chunk)

    def read(self, size: int, timeout: float) -> bytes:
        if not self._pending:
            try:
                self._pending = self._queue.get(timeout=timeout)
            except queue.Empty:
                return b""
        data, self._pending = self._pending[:size], self._pending[size:]
        return data

    def write(self, data: bytes) -> None:
        stdin = self._process.stdin
        stdin.write(data)
        stdin.flush()

    def reset(self) -> None:
        self._stop_process()
        self._pending = b""
        while not self._queue.empty():
            self._queue.get_nowait()
        self._start()

    def _stop_process(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        with contextlib.suppress(Exception):
            process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=5)
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.join(timeout=2)
        with contextlib.suppress(Exception):
            process.stdout.close()
        with contextlib.suppress(Exception):
            process.stdin.close()

    def close(self) -> None:
        self._stop_process()
