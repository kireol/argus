"""Argus agent protocol over a byte transport.

Line-oriented framing shared with the firmware agents::

    host  -> board   ESC[ARGUS] <cmd>[ <args>]\\n
    board -> host    ESC[ARGUS] <cmd> ok <len>\\n<len raw bytes>\\n
                     ESC[ARGUS] <cmd> err <message>\\n
    anything else    application log line
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

from argus.adapters.esp32.transport import Transport
from argus.exceptions import DeviceConnectionError

PREFIX = b"\x1b[ARGUS] "
_READ_CHUNK = 4096
_READ_POLL = 0.1


@dataclass(frozen=True)
class AgentInfo:
    name: str
    version: str
    fb_format: str | None
    width: int
    height: int
    caps: frozenset[str]


@dataclass(frozen=True)
class _Response:
    cmd: str
    payload: bytes
    error: str | None


def parse_hello(payload: bytes) -> AgentInfo:
    fields: dict[str, str] = {}
    for token in payload.decode("utf-8", errors="replace").split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    if "name" not in fields or "fb" not in fields:
        raise DeviceConnectionError(
            f"Malformed hello response from agent: {payload!r}",
            remediation="Update the firmware's Argus agent; expected "
            "'name=<n> version=<v> fb=<FMT>,<w>,<h> caps=<list>'.",
        )
    fb_format: str | None = None
    width = height = 0
    if fields["fb"].lower() != "none":
        try:
            fmt, w, h = fields["fb"].split(",")
            fb_format, width, height = fmt, int(w), int(h)
        except ValueError as exc:
            raise DeviceConnectionError(
                f"Malformed fb field in hello response: {fields['fb']!r}",
                remediation="Expected fb=<FORMAT>,<width>,<height> or fb=none.",
            ) from exc
    caps = frozenset(c for c in fields.get("caps", "").split(",") if c)
    return AgentInfo(
        name=fields["name"],
        version=fields.get("version", ""),
        fb_format=fb_format,
        width=width,
        height=height,
        caps=caps,
    )


class AgentLink:
    """Reader thread + request/response matching over a :class:`Transport`."""

    def __init__(
        self, transport: Transport, *, log_sink: deque[str], timeout: float = 5.0
    ) -> None:
        self._transport = transport
        self._logs = log_sink
        self._timeout = timeout
        self._responses: queue.Queue[_Response] = queue.Queue()
        self._request_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Set when a request times out: the board may still answer it after we've given up,
        # leaving a stale reply in `_responses` that the *next* request must discard before
        # waiting on its own response. Only drain in that case - draining unconditionally
        # races the reader thread, which can enqueue a request's own answer before its
        # `request()` call gets a chance to write and start waiting for it.
        self._abandoned = False

    # -- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="esp32-agent-link")
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._transport.close()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    @property
    def reader_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- requests ------------------------------------------------------------------------

    def request(self, cmd: str, *args: str) -> bytes:
        """Write ``cmd`` (with ``args``) and block until its matching response arrives.

        Protocol limitation: the wire format has no per-request correlation id, only
        a command name. `_abandoned` + `_drain()` (see ``__init__``) only cover the
        immediate-next-request case: a reply that arrives after *this* call gives up
        is discarded before the *very next* call starts waiting. A reply that arrives
        even later - after an intervening, successful request of the same command has
        already completed - is not covered and could still be misattributed to
        whichever later request happens to ask for that same ``cmd``. Closing that gap
        fully would require the firmware to echo back a request id, which it does not.
        """
        line = " ".join((cmd, *args)).encode("utf-8")
        with self._request_lock:
            if self._abandoned:
                self._drain()
                self._abandoned = False
            self._transport.write(PREFIX + line + b"\n")
            deadline = time.monotonic() + self._timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._abandoned = True
                    raise DeviceConnectionError(
                        f"Agent request {cmd!r} timed out after {self._timeout}s.",
                        remediation="Check the firmware calls argus.poll() regularly and the "
                        "baud rate matches.",
                    )
                try:
                    response = self._responses.get(timeout=remaining)
                except queue.Empty:
                    continue
                if response.cmd != cmd:
                    continue  # stale response from an earlier, timed-out request
                if response.error is not None:
                    raise DeviceConnectionError(
                        f"Agent rejected {cmd!r}: {response.error}",
                        remediation="See the firmware agent's error message.",
                    )
                return response.payload

    def hello(self) -> AgentInfo:
        return parse_hello(self.request("hello"))

    def _drain(self) -> None:
        while True:
            try:
                self._responses.get_nowait()
            except queue.Empty:
                return

    # -- reader --------------------------------------------------------------------------

    def _run(self) -> None:
        buffer = b""
        pending: tuple[str, int] | None = None  # (cmd, payload length) awaiting bytes
        while not self._stop.is_set():
            try:
                chunk = self._transport.read(_READ_CHUNK, _READ_POLL)
            except Exception:  # noqa: BLE001 - transport closed underneath us
                break
            if chunk:
                buffer += chunk
            while True:
                if pending is not None:
                    cmd, length = pending
                    if len(buffer) < length + 1:
                        break
                    payload, buffer = buffer[:length], buffer[length:]
                    if buffer[:1] == b"\n":
                        buffer = buffer[1:]
                    self._responses.put(_Response(cmd, payload, None))
                    pending = None
                    continue
                newline = buffer.find(b"\n")
                if newline < 0:
                    break
                line, buffer = buffer[:newline], buffer[newline + 1 :]
                pending = self._handle_line(line)
            if not chunk and self._stop.is_set():
                break

    def _handle_line(self, line: bytes) -> tuple[str, int] | None:
        if not line.startswith(PREFIX):
            self._logs.append(line.decode("utf-8", errors="replace").rstrip("\r"))
            return None
        parts = line[len(PREFIX) :].decode("utf-8", errors="replace").rstrip("\r").split(" ", 2)
        if len(parts) < 2:
            self._logs.append(line.decode("utf-8", errors="replace"))
            return None
        cmd, status = parts[0], parts[1]
        rest = parts[2] if len(parts) > 2 else ""
        if status == "ok":
            try:
                return (cmd, int(rest))
            except ValueError:
                self._responses.put(_Response(cmd, b"", f"bad length {rest!r}"))
                return None
        if status == "err":
            self._responses.put(_Response(cmd, b"", rest or "unknown error"))
            return None
        self._logs.append(line.decode("utf-8", errors="replace"))
        return None
