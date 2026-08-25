"""SerialTransport / WokwiTransport with injected serial and process fakes."""

from __future__ import annotations

import io
import os

import pytest

from argus.adapters.esp32.transport import SerialTransport, WokwiTransport
from argus.exceptions import DeviceConnectionError


class FakeSerial:
    def __init__(self, port: str, baud: int) -> None:
        self.port, self.baud = port, baud
        self.timeout: float | None = None
        self.dtr = True
        self.rts = True
        self.line_log: list[tuple[str, bool]] = []
        self.incoming = b"hello from board\n"
        self.written: list[bytes] = []
        self.is_open = True

    def __setattr__(self, name, value):
        if name in ("dtr", "rts") and "line_log" in self.__dict__:
            self.line_log.append((name, value))
        super().__setattr__(name, value)

    def read(self, size: int) -> bytes:
        data, self.incoming = self.incoming[:size], self.incoming[size:]
        return data

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


class TestSerialTransport:
    def test_read_write_close(self):
        fakes: list[FakeSerial] = []

        def factory(port, baud):
            fakes.append(FakeSerial(port, baud))
            return fakes[-1]

        t = SerialTransport("/dev/ttyUSB0", 921600, serial_factory=factory)
        assert t.read(5, timeout=0.1) == b"hello"
        assert fakes[0].timeout == 0.1
        t.write(b"x")
        assert fakes[0].written == [b"x"]
        assert "/dev/ttyUSB0" in t.description and "921600" in t.description
        t.close()
        assert not fakes[0].is_open

    def test_classic_reset_sequence(self):
        fake = FakeSerial("p", 1)
        t = SerialTransport("p", 1, serial_factory=lambda p, b: fake)
        t.reset()
        assert fake.line_log == [("dtr", False), ("rts", True), ("rts", False)]

    def test_usb_cdc_reset_sequence(self):
        fake = FakeSerial("p", 1)
        t = SerialTransport("p", 1, usb_cdc=True, serial_factory=lambda p, b: fake)
        t.reset()
        assert fake.line_log[:2] == [("rts", True), ("dtr", True)]
        assert fake.line_log[-1] == ("rts", False)

    def test_open_failure_wrapped(self):
        def factory(port, baud):
            raise OSError("could not open port")

        with pytest.raises(DeviceConnectionError, match="could not open port"):
            SerialTransport("/dev/nope", 115200, serial_factory=factory)

    def test_missing_pyserial(self, monkeypatch):
        import builtins

        real = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "serial" or name.startswith("serial."):
                raise ImportError("no pyserial")
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(DeviceConnectionError, match=r'pip install "argus\[esp32\]"'):
            SerialTransport("/dev/ttyUSB0", 115200)


class FakeProcess:
    def __init__(self, output: bytes) -> None:
        r, w = os.pipe()
        self._w = w
        self.stdout = os.fdopen(r, "rb", buffering=0)
        os.write(w, output)
        self.stdin = io.BytesIO()
        self.terminated = False
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.terminated = True
        os.close(self._w)

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def poll(self) -> int | None:
        return self.returncode


class TestWokwiTransport:
    def test_spawns_cli_and_reads_output(self, tmp_path):
        spawned: list[list[str]] = []
        proc = FakeProcess(b"Wokwi CLI v0.1\nboot\n")

        def spawn(argv):
            spawned.append(argv)
            return proc

        t = WokwiTransport(tmp_path, timeout_ms=1234, spawn=spawn, env={"WOKWI_CLI_TOKEN": "wok_x"})
        assert spawned[0][:3] == ["wokwi-cli", "--interactive", "--timeout"]
        assert spawned[0][3] == "1234" and spawned[0][-1] == str(tmp_path)
        assert t.read(100, timeout=1.0).startswith(b"Wokwi CLI")
        t.write(b"cmd\n")
        assert proc.stdin.getvalue() == b"cmd\n"
        t.close()
        assert proc.terminated

    def test_reset_respawns(self, tmp_path):
        procs = [FakeProcess(b"one\n"), FakeProcess(b"two\n")]

        def spawn(argv):
            return procs.pop(0)

        t = WokwiTransport(tmp_path, spawn=spawn, env={"WOKWI_CLI_TOKEN": "wok_x"})
        assert t.read(100, timeout=1.0) == b"one\n"
        t.reset()
        assert t.read(100, timeout=1.0) == b"two\n"
        t.close()

    def test_requires_token(self, tmp_path):
        with pytest.raises(DeviceConnectionError, match="WOKWI_CLI_TOKEN"):
            WokwiTransport(tmp_path, spawn=lambda argv: FakeProcess(b""), env={})

    def test_missing_cli(self, tmp_path):
        def spawn(argv):
            raise FileNotFoundError("wokwi-cli")

        with pytest.raises(DeviceConnectionError, match="wokwi-cli"):
            WokwiTransport(tmp_path, spawn=spawn, env={"WOKWI_CLI_TOKEN": "wok_x"})

    def test_read_timeout_returns_empty(self, tmp_path):
        t = WokwiTransport(
            tmp_path, spawn=lambda argv: FakeProcess(b""), env={"WOKWI_CLI_TOKEN": "wok_x"}
        )
        assert t.read(10, timeout=0.05) == b""
        t.close()
