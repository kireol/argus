"""Run the shipped MicroPython agent under CPython and talk to it with AgentLink."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from collections import deque
from pathlib import Path

import pytest

from argus.adapters.esp32.protocol import AgentLink

AGENT_PATH = Path(__file__).resolve().parents[2] / "agents/esp32/micropython/argus_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("argus_agent", AGENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeUart:
    """Two byte queues: host->board (`inbound`) and board->host (`outbound`)."""

    def __init__(self) -> None:
        self.inbound = b""
        self.outbound = b""
        self.lock = threading.Lock()

    # MicroPython UART surface used by the agent
    def any(self) -> int:
        with self.lock:
            return len(self.inbound)

    def read(self, n: int | None = None) -> bytes | None:
        with self.lock:
            if not self.inbound:
                return None
            n = len(self.inbound) if n is None else n
            data, self.inbound = self.inbound[:n], self.inbound[n:]
            return data

    def write(self, data: bytes) -> int:
        with self.lock:
            self.outbound += bytes(data)
        return len(data)


class UartTransport:
    """Transport view of the FakeUart for AgentLink (host side)."""

    def __init__(self, uart: FakeUart) -> None:
        self.uart = uart
        self.closed = False

    def read(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            with self.uart.lock:
                if self.uart.outbound:
                    data, self.uart.outbound = self.uart.outbound[:size], self.uart.outbound[size:]
                    return data
            if self.closed or time.monotonic() >= deadline:
                return b""
            time.sleep(0.005)

    def write(self, data: bytes) -> None:
        with self.uart.lock:
            self.uart.inbound += data

    def reset(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    @property
    def description(self) -> str:
        return "fake-uart"


@pytest.fixture
def world():
    module = _load_agent_module()
    uart = FakeUart()
    buffer = bytearray(16 * 8 // 8)  # 16x8 MONO_HLSB
    buffer[0] = 0b10000000
    keys: list[str] = []
    agent = module.ArgusAgent(uart, buffer, 16, 8, "MONO_HLSB", name="demo", version="0.1")
    agent.on_key = keys.append
    agent.set_status("screen", "home")
    agent.set_state("count", 2)
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            agent.poll()
            time.sleep(0.002)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    logs: deque[str] = deque()
    link = AgentLink(UartTransport(uart), log_sink=logs, timeout=2.0)
    link.start()
    yield agent, uart, keys, link, logs
    link.close()
    stop.set()
    thread.join(timeout=1)


def test_hello(world):
    _agent, _uart, _keys, link, _logs = world
    info = link.hello()
    assert info.name == "demo" and info.version == "0.1"
    assert (info.fb_format, info.width, info.height) == ("MONO_HLSB", 16, 8)
    assert info.caps == {"screen", "input", "status", "state"}


def test_screenshot_returns_buffer(world):
    _agent, _uart, _keys, link, _logs = world
    data = link.request("screenshot")
    assert len(data) == 16 and data[0] == 0b10000000


def test_input_invokes_callback(world):
    _agent, _uart, keys, link, _logs = world
    assert link.request("input", "BTN_OK") == b""
    assert keys == ["BTN_OK"]


def test_status_and_state_json(world):
    _agent, _uart, _keys, link, _logs = world
    assert json.loads(link.request("status")) == {"screen": "home"}
    assert json.loads(link.request("state")) == {"count": 2}


def test_unknown_command_is_error(world):
    _agent, _uart, _keys, link, _logs = world
    from argus.exceptions import DeviceConnectionError

    with pytest.raises(DeviceConnectionError, match="unknown command"):
        link.request("dance")


def test_app_prints_are_logs(world):
    _agent, uart, _keys, link, logs = world
    uart.write(b"app log line\n")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and "app log line" not in logs:
        time.sleep(0.01)
    assert "app log line" in logs


def test_input_without_callback_is_error(world):
    agent, _uart, _keys, link, _logs = world
    agent.on_key = None
    from argus.exceptions import DeviceConnectionError

    with pytest.raises(DeviceConnectionError, match="no key handler"):
        link.request("input", "X")
