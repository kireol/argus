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
from argus.logging import get_logger

PREFIX = b"\x1b[ARGUS] "
_READ_CHUNK = 4096
_READ_POLL = 0.1
# Bound on the reader's line-accumulation buffer when it is *not* mid-payload (i.e.
# waiting on a "\n"): protects against a garbage stream with no newline growing forever.
# Not applied while collecting a payload - that is already bounded by the "ok <len>"
# header's validated length (see _MAX_PAYLOAD).
_MAX_LINE_BUFFER = 1024 * 1024  # 1 MiB
# Sanity bound on a declared "ok <len>" payload length.
_MAX_PAYLOAD = 4 * 1024 * 1024  # 4 MiB


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
        self._log = get_logger("argus.esp32.link")
        # Set when a request times out: the board may still answer it after we've given up,
        # leaving a stale reply in `_responses` that the *next* request must discard before
        # waiting on its own response. Only drain in that case - draining unconditionally
        # races the reader thread, which can enqueue a request's own answer before its
        # `request()` call gets a chance to write and start waiting for it.
        self._abandoned = False
        # Bumped by reset_stream() after a board reset. The reader thread compares its
        # locally-held generation against this each loop iteration and discards its
        # in-flight `buffer`/`pending` state when it has changed, so bytes left over from
        # a request that was mid-payload across the reset cannot swallow the fresh boot
        # stream (or be misinterpreted as part of it).
        self._generation_lock = threading.Lock()
        self._generation = 0

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
        for part in (cmd, *args):
            if "\r" in part or "\n" in part:
                raise DeviceConnectionError(
                    f"Agent request {cmd!r} argument {part!r} contains a line break.",
                    remediation="Line breaks in a command or argument would inject "
                    "extra protocol frames onto the wire; strip them before sending.",
                )
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

    def reset_stream(self) -> None:
        """Discard reader and request state after the transport has been reset.

        Call this immediately after ``transport.reset()``. Without it, a request that
        was mid-payload when the board reset (e.g. a screenshot the agent never
        finished sending) would leave a stale, incomplete frame in the reader's
        `buffer`/`pending` locals; bytes from the fresh boot stream would then be fed
        into finishing *that* frame instead of being parsed as new log lines/replies. A
        stale reply could also already be queued in `_responses`.
        """
        with self._request_lock:
            self._drain()
            self._abandoned = False
            with self._generation_lock:
                self._generation += 1

    def _current_generation(self) -> int:
        with self._generation_lock:
            return self._generation

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
        generation = self._current_generation()
        while not self._stop.is_set():
            try:
                chunk = self._transport.read(_READ_CHUNK, _READ_POLL)
            except Exception:  # noqa: BLE001 - transport closed underneath us
                break
            current_generation = self._current_generation()
            if current_generation != generation:
                buffer, pending = b"", None
                generation = current_generation
            if chunk:
                buffer += chunk
                if pending is None and len(buffer) > _MAX_LINE_BUFFER:
                    self._log.warning(
                        "esp32 agent link: %d bytes with no newline, dropping buffer",
                        len(buffer),
                    )
                    buffer = b""
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
                length = int(rest)
            except ValueError:
                self._responses.put(_Response(cmd, b"", f"bad length {rest!r}"))
                return None
            if not (0 <= length <= _MAX_PAYLOAD):
                self._responses.put(
                    _Response(cmd, b"", f"length {length} out of range (0..{_MAX_PAYLOAD})")
                )
                return None
            return (cmd, length)
        if status == "err":
            self._responses.put(_Response(cmd, b"", rest or "unknown error"))
            return None
        self._logs.append(line.decode("utf-8", errors="replace"))
        return None
