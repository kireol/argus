"""AgentLink: splitting a UART byte stream into log lines and ARGUS response frames."""

from __future__ import annotations

import threading
import time
from collections import deque

import pytest

from argus.adapters.esp32.protocol import PREFIX, AgentInfo, AgentLink, parse_hello
from argus.exceptions import DeviceConnectionError


class FakeTransport:
    """Scripted byte stream; `script` is a list of chunks handed out one per read()."""

    def __init__(self, script: list[bytes] | None = None) -> None:
        self.script: list[bytes] = list(script or [])
        self.writes: list[bytes] = []
        self.closed = False
        self.resets = 0
        self._lock = threading.Lock()
        self._wakeup = threading.Event()

    def feed(self, *chunks: bytes) -> None:
        with self._lock:
            self.script.extend(chunks)
        self._wakeup.set()

    def read(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if self.script:
                    chunk = self.script.pop(0)
                    return chunk[:size] if len(chunk) <= size else self._split(chunk, size)
            if self.closed or time.monotonic() >= deadline:
                return b""
            self._wakeup.wait(0.01)
            self._wakeup.clear()

    def _split(self, chunk: bytes, size: int) -> bytes:
        self.script.insert(0, chunk[size:])
        return chunk[:size]

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def reset(self) -> None:
        self.resets += 1

    def close(self) -> None:
        self.closed = True
        self._wakeup.set()

    @property
    def description(self) -> str:
        return "fake"


def frame(cmd: str, payload: bytes) -> bytes:
    return PREFIX + f"{cmd} ok {len(payload)}\n".encode() + payload + b"\n"


@pytest.fixture
def logs() -> deque[str]:
    return deque(maxlen=100)


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def link(transport: FakeTransport, logs: deque[str]):
    link = AgentLink(transport, log_sink=logs, timeout=1.0)
    link.start()
    yield link
    link.close()


def _wait(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_plain_lines_become_logs(link, transport, logs):
    transport.feed(b"boot ok\r\nPlayer: state=PLAYING\n")
    assert _wait(lambda: len(logs) == 2)
    assert list(logs) == ["boot ok", "Player: state=PLAYING"]


def test_request_writes_line_and_returns_payload(link, transport, logs):
    transport.feed(b"log before\n", frame("screenshot", b"\x01\x02\x03"), b"log after\n")
    payload = link.request("screenshot")
    assert payload == b"\x01\x02\x03"
    assert transport.writes == [PREFIX + b"screenshot\n"]
    assert _wait(lambda: list(logs) == ["log before", "log after"])


def test_request_with_args(link, transport):
    transport.feed(frame("input", b""))
    assert link.request("input", "BTN_OK") == b""
    assert transport.writes[-1] == PREFIX + b"input BTN_OK\n"


def test_payload_split_across_reads(link, transport):
    whole = frame("screenshot", bytes(range(32)))
    transport.feed(whole[:5], whole[5:20], whole[20:])
    assert link.request("screenshot") == bytes(range(32))


def test_payload_may_contain_newlines_and_prefix_bytes(link, transport):
    payload = b"\n\n" + PREFIX + b"\n"
    transport.feed(frame("screenshot", payload))
    assert link.request("screenshot") == payload


def test_error_response_raises(link, transport):
    transport.feed(PREFIX + b"screenshot err no framebuffer registered\n")
    with pytest.raises(DeviceConnectionError, match="no framebuffer registered"):
        link.request("screenshot")


def test_timeout_raises(link, transport):
    with pytest.raises(DeviceConnectionError, match="timed out"):
        link.request("hello")


def test_stale_response_is_skipped(link, transport):
    transport.feed(frame("status", b"{}"), frame("hello", b"name=x version=1 fb=none caps="))
    payload = link.request("hello")
    assert payload.startswith(b"name=x")


def test_requests_are_serialised(link, transport):
    transport.feed(frame("status", b"{}"))
    results: list[bytes] = []
    workers = [
        threading.Thread(target=lambda: results.append(link.request("status")))
        for _ in range(2)
    ]
    for w in workers:
        w.start()
    assert _wait(lambda: len(results) == 1)
    transport.feed(frame("status", b"{}"))
    for w in workers:
        w.join(timeout=3)
    assert results == [b"{}", b"{}"]


def test_late_reply_after_timeout_is_dropped_before_next_request(link, transport):
    """A reply that arrives after we gave up must not satisfy the next same-cmd request."""
    with pytest.raises(DeviceConnectionError, match="timed out"):
        link.request("status")
    transport.feed(frame("status", b"late"))
    # Wait for the late reply to actually be parsed onto the internal queue - not
    # merely handed to the fake transport - otherwise this test would itself race
    # the reader thread the same way the bug it covers did.
    assert _wait(lambda: link._responses.qsize() >= 1)

    results: list[bytes] = []
    worker = threading.Thread(target=lambda: results.append(link.request("status")))
    worker.start()
    # The second request's drain-then-write is synchronous and happens before it
    # waits for a response; once its write lands, the stale "late" reply is gone
    # and only a reply fed from here on can satisfy it.
    assert _wait(lambda: len(transport.writes) == 2)
    transport.feed(frame("status", b"fresh"))
    worker.join(timeout=3)
    assert results == [b"fresh"]


def test_successful_request_does_not_drain_a_pre_queued_next_response(link, transport):
    """After a successful request (nothing abandoned), the next request must not

    discard a response already queued up for it.
    """
    transport.feed(frame("status", b"first"))
    assert link.request("status") == b"first"

    transport.feed(frame("status", b"second"))
    assert _wait(lambda: link._responses.qsize() >= 1)
    assert link.request("status") == b"second"


def test_close_stops_reader(transport, logs):
    link = AgentLink(transport, log_sink=logs, timeout=0.2)
    link.start()
    link.close()
    assert transport.closed
    assert not link.reader_alive


def test_parse_hello_full():
    info = parse_hello(b"name=menu version=1.2 fb=MONO_VLSB,128,64 caps=screen,input,status")
    assert info == AgentInfo(
        name="menu", version="1.2", fb_format="MONO_VLSB", width=128, height=64,
        caps=frozenset({"screen", "input", "status"}),
    )


def test_parse_hello_without_framebuffer():
    info = parse_hello(b"name=logger version=0 fb=none caps=")
    assert info.fb_format is None and info.width == 0 and info.caps == frozenset()


def test_parse_hello_rejects_garbage():
    with pytest.raises(DeviceConnectionError, match="hello"):
        parse_hello(b"???")


def test_hello_uses_request(link, transport):
    transport.feed(frame("hello", b"name=a version=1 fb=RGB565,16,8 caps=screen"))
    info = link.hello()
    assert info.width == 16 and info.caps == {"screen"}
