"""AdbClient — the single boundary between the Creator and the ``adb`` binary.

Every call passes the serial and any paths as *argv items* (never a shell
string). Short commands use ``subprocess.run``; the live ``getevent`` stream is
an :class:`AdbProcess` — an incrementally read ``Popen`` with clean shutdown.

:class:`SubprocessAdbClient` talks to real hardware; tests use
``fake_adb.FakeAdbClient`` which implements the same protocol.
"""

from __future__ import annotations

import collections
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from argus_test_creator.adapters.android.getevent_parser import parse_input_devices
from argus_test_creator.adapters.android.models import (
    AndroidDevice,
    AndroidDeviceInfo,
    AndroidInputDevice,
)
from argus_test_creator.core.errors import TargetConnectionError
from argus_test_creator.core.logging import get_logger

_log = get_logger("android.adb")

DEFAULT_TIMEOUT = 20.0
ADB_INSTALL_HINT = (
    "Install Android platform-tools and put adb on PATH (or set the 'adb_path' target setting)."
)


# -- streaming process --------------------------------------------------------------------------


@runtime_checkable
class EventStream(Protocol):
    """A line-oriented stream (``getevent``) that can be stopped from another thread."""

    def readline(self, timeout: float | None = None) -> str | None:
        """Next line, ``""`` on timeout, or ``None`` when the stream has ended."""
        ...

    def stop(self, timeout: float = 3.0) -> None: ...

    @property
    def alive(self) -> bool: ...

    @property
    def returncode(self) -> int | None: ...

    @property
    def stderr_text(self) -> str: ...


class AdbProcess:
    """A long-running ``adb`` subprocess read incrementally.

    * stdout is read line by line by a reader thread into a bounded queue so a
      slow consumer never blocks the pipe (oldest lines are dropped and counted);
    * stderr is captured (bounded) for diagnostics;
    * ``stop`` terminates, waits, and kills — no zombies, no orphans.
    """

    def __init__(self, argv: Sequence[str], *, max_buffered_lines: int = 20_000) -> None:
        self.argv = list(argv)
        self._proc: subprocess.Popen[bytes] | None = None
        self._lines: collections.deque[str] = collections.deque(maxlen=max_buffered_lines)
        self._cv = threading.Condition()
        self._eof = False
        self._stderr: collections.deque[str] = collections.deque(maxlen=200)
        self._reader: threading.Thread | None = None
        self._err_reader: threading.Thread | None = None
        self.dropped_lines = 0
        self._lock = threading.Lock()

    def start(self) -> AdbProcess:
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - argv list, never a shell string
                self.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=sys.platform != "win32",
            )
        except FileNotFoundError as exc:
            raise TargetConnectionError("adb was not found.", remediation=ADB_INSTALL_HINT) from exc
        self._reader = threading.Thread(target=self._read_stdout, name="adb-stdout", daemon=True)
        self._err_reader = threading.Thread(target=self._read_stderr, name="adb-stderr",
                                            daemon=True)
        self._reader.start()
        self._err_reader.start()
        return self

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for raw in self._proc.stdout:
                line = raw.decode("utf-8", errors="replace")
                with self._cv:
                    if len(self._lines) == self._lines.maxlen:
                        self.dropped_lines += 1
                    self._lines.append(line)
                    self._cv.notify()
        except (OSError, ValueError):
            pass
        finally:
            with self._cv:
                self._eof = True
                self._cv.notify_all()

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for raw in self._proc.stderr:
                self._stderr.append(raw.decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            pass

    def readline(self, timeout: float | None = None) -> str | None:
        with self._cv:
            if not self._lines and not self._eof:
                self._cv.wait(timeout)
            if self._lines:
                return self._lines.popleft()
            return None if self._eof else ""

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def returncode(self) -> int | None:
        if self._proc is None:
            return None
        self._proc.poll()
        return self._proc.returncode

    @property
    def stderr_text(self) -> str:
        return "".join(self._stderr)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def stop(self, timeout: float = 3.0) -> None:
        proc = self._proc
        if proc is None:
            return
        with self._lock:
            if proc.poll() is None:
                _signal_group(proc, kill=False)
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _signal_group(proc, kill=True)
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        _log.warning("adb process %s did not exit after kill", proc.pid)
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        for thread in (self._reader, self._err_reader):
            if thread is not None and thread.is_alive():
                thread.join(timeout=timeout)
        with self._cv:
            self._eof = True
            self._cv.notify_all()

    def __enter__(self) -> AdbProcess:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def _signal_group(proc: subprocess.Popen[bytes], *, kill: bool) -> None:
    try:
        if sys.platform != "win32":
            import signal

            os.killpg(proc.pid, signal.SIGKILL if kill else signal.SIGTERM)
        elif kill:
            proc.kill()
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill() if kill else proc.terminate()
        except OSError:
            pass


# -- client protocol ------------------------------------------------------------------------


@runtime_checkable
class AdbClient(Protocol):
    def available(self) -> tuple[bool, str]:
        """(True, path/version) or (False, reason)."""
        ...

    def list_devices(self) -> list[AndroidDevice]: ...

    def shell(self, serial: str, *args: str, timeout: float | None = None) -> str: ...

    def exec_out(self, serial: str, *args: str, timeout: float | None = None) -> bytes: ...

    def get_device_info(self, serial: str) -> AndroidDeviceInfo: ...

    def get_input_devices(self, serial: str) -> list[AndroidInputDevice]: ...

    def getevent_available(self, serial: str) -> tuple[bool, str]: ...

    def start_getevent(self, serial: str, device_path: str | None = None) -> EventStream: ...

    def stop_getevent(self, serial: str) -> None:
        """Best-effort: make sure no ``getevent`` lingers on the device."""
        ...

    def screenshot(self, serial: str) -> bytes: ...


# -- real client -------------------------------------------------------------------------------


class SubprocessAdbClient:
    def __init__(self, adb_path: str | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.adb = adb_path or shutil.which("adb") or os.environ.get("ADB") or "adb"
        self.timeout = timeout

    # -- plumbing --

    def _argv(self, serial: str | None, *args: str) -> list[str]:
        argv = [self.adb]
        if serial:
            argv += ["-s", serial]
        return argv + list(args)

    def _run(self, serial: str | None, *args: str, timeout: float | None = None) -> bytes:
        argv = self._argv(serial, *args)
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, never a shell string
                argv, capture_output=True, timeout=timeout or self.timeout, check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise TargetConnectionError("adb was not found.", remediation=ADB_INSTALL_HINT) from exc
        except subprocess.TimeoutExpired as exc:
            raise TargetConnectionError(
                f"adb {' '.join(args)} timed out.",
                remediation="Reconnect the device (adb devices) and retry.",
            ) from exc
        if completed.returncode != 0:
            err = completed.stderr.decode(errors="replace").strip()[:300]
            if "device offline" in err or "not found" in err or "no devices" in err:
                raise TargetConnectionError(
                    f"Android device {serial or ''} is not reachable: {err}",
                    remediation="Check the cable and that `adb devices` lists it as 'device'.",
                )
            raise TargetConnectionError(
                f"adb {' '.join(args)} failed: {err}",
                remediation="Check `adb devices` shows the device as 'device' (authorized).",
            )
        return completed.stdout

    # -- protocol --

    def available(self) -> tuple[bool, str]:
        path = shutil.which(self.adb) or (self.adb if os.path.isfile(self.adb) else None)
        if path is None:
            return False, f"adb not found ({self.adb!r}). {ADB_INSTALL_HINT}"
        try:
            out = self._run(None, "version", timeout=10).decode(errors="replace")
        except TargetConnectionError as exc:
            return False, exc.message
        version = out.splitlines()[0] if out else "adb"
        return True, f"{path} ({version})"

    def list_devices(self) -> list[AndroidDevice]:
        out = self._run(None, "devices", "-l").decode(errors="replace")
        return parse_devices_output(out)

    def shell(self, serial: str, *args: str, timeout: float | None = None) -> str:
        return self._run(serial, "shell", *args, timeout=timeout).decode(errors="replace")

    def exec_out(self, serial: str, *args: str, timeout: float | None = None) -> bytes:
        return self._run(serial, "exec-out", *args, timeout=timeout)

    def get_device_info(self, serial: str) -> AndroidDeviceInfo:
        props = self.shell(serial, "getprop")
        model = _prop(props, "ro.product.model")
        version = _prop(props, "ro.build.version.release")
        sdk_text = _prop(props, "ro.build.version.sdk")
        width, height = parse_wm_size(self.shell(serial, "wm", "size"))
        rotation = self.get_rotation(serial)
        return AndroidDeviceInfo(
            serial=serial, model=model, android_version=version,
            sdk=int(sdk_text) if sdk_text and sdk_text.isdigit() else None,
            natural_width=width, natural_height=height, rotation=rotation,
        )

    def get_rotation(self, serial: str) -> int:
        try:
            text = self.shell(serial, "dumpsys", "display", timeout=10)
            rotation = parse_rotation(text)
            if rotation is not None:
                return rotation
            text = self.shell(serial, "dumpsys", "input", timeout=10)
            rotation = parse_rotation(text)
            return rotation if rotation is not None else 0
        except TargetConnectionError:
            return 0

    def get_input_devices(self, serial: str) -> list[AndroidInputDevice]:
        return parse_input_devices(self.shell(serial, "getevent", "-lp"))

    def getevent_available(self, serial: str) -> tuple[bool, str]:
        try:
            out = self.shell(serial, "getevent", "-lp", timeout=10)
        except TargetConnectionError as exc:
            return False, exc.message
        if "add device" not in out:
            return False, "getevent -lp listed no input devices (permission denied?)"
        return True, f"{out.count('add device')} input devices"

    def start_getevent(self, serial: str, device_path: str | None = None) -> EventStream:
        args = ["shell", "getevent", "-lt"]
        if device_path:
            args.append(device_path)
        return AdbProcess(self._argv(serial, *args)).start()

    def stop_getevent(self, serial: str) -> None:
        try:
            self._run(serial, "shell", "pkill", "-x", "getevent", timeout=5)
        except TargetConnectionError:
            pass  # pkill may be missing on old builds or nothing was running

    def screenshot(self, serial: str) -> bytes:
        return self.exec_out(serial, "screencap", "-p")


# -- parsers (pure) ----------------------------------------------------------------------------

_DEVICE_LINE_RE = re.compile(r"^(?P<serial>\S+)\s+(?P<state>\S+)(?P<rest>.*)$")


def parse_devices_output(text: str) -> list[AndroidDevice]:
    """Parse ``adb devices [-l]``."""
    devices: list[AndroidDevice] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("List of devices", "*")):
            continue
        match = _DEVICE_LINE_RE.match(line)
        if not match:
            continue
        extras = dict(
            item.split(":", 1) for item in match.group("rest").split() if ":" in item
        )
        devices.append(AndroidDevice(
            serial=match.group("serial"), state=match.group("state"),
            model=extras.get("model"), product=extras.get("product"),
            transport_id=extras.get("transport_id"),
        ))
    return devices


_WM_SIZE_RE = re.compile(r"(?P<kind>Physical|Override) size:\s*(?P<w>\d+)x(?P<h>\d+)")


def parse_wm_size(text: str) -> tuple[int, int]:
    """``wm size`` → (width, height) in natural orientation; override wins when present."""
    physical: tuple[int, int] | None = None
    override: tuple[int, int] | None = None
    for match in _WM_SIZE_RE.finditer(text):
        size = (int(match.group("w")), int(match.group("h")))
        if match.group("kind") == "Override":
            override = size
        else:
            physical = size
    return override or physical or (0, 0)


_ROTATION_RES = (
    re.compile(r"mCurrentRotation=(?:ROTATION_)?(\d)"),
    re.compile(r"SurfaceOrientation:\s*(\d)"),
    re.compile(r"\brotation[=:]\s*(?:ROTATION_)?(\d)\b"),
    re.compile(r"mRotation=(?:ROTATION_)?(\d)"),
)


def parse_rotation(text: str) -> int | None:
    for pattern in _ROTATION_RES:
        match = pattern.search(text)
        if match:
            return int(match.group(1)) % 4
    return None


_PROP_RE = re.compile(r"^\[(?P<key>[^\]]+)\]:\s*\[(?P<value>[^\]]*)\]", re.MULTILINE)


def _prop(getprop_output: str, key: str) -> str | None:
    for match in _PROP_RE.finditer(getprop_output):
        if match.group("key") == key:
            return match.group("value").strip() or None
    return None


__all__ = [
    "ADB_INSTALL_HINT", "AdbClient", "AdbProcess", "EventStream", "SubprocessAdbClient",
    "parse_devices_output", "parse_rotation", "parse_wm_size",
]
