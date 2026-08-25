"""Argus test agent for MicroPython (ESP32).

Usage::

    from machine import UART, Pin
    import ssd1306
    from argus_agent import ArgusAgent

    i2c = I2C(0, scl=Pin(22), sda=Pin(21))
    oled = ssd1306.SSD1306_I2C(128, 64, i2c)
    argus = ArgusAgent(UART(0), oled.buffer, 128, 64, "MONO_VLSB")
    argus.on_key = handle_key
    while True:
        argus.poll()

The agent shares its transport with normal ``print`` output; anything that is
not an Argus command line is left alone (and passed to ``on_serial_line`` if
set).

By default the agent both reads from and writes to ``uart`` (a duplex UART:
``machine.UART`` implements ``read``/``any`` and ``write`` on the same
object). Pass a separate ``out`` writer (any object exposing
``write(bytes)``) if replies must go somewhere other than ``uart`` - e.g. a
non-duplex input paired with a different output stream.

``poll()`` is non-blocking. When ``uart`` exposes ``.any()`` (a real
``machine.UART``), that is used to size each read. Objects without ``.any()``
are instead polled with ``select.poll()``/``uselect.poll()`` for readability
before each byte is read. If neither ``.any()`` nor ``select``/``uselect``
polling is available for the given object, the constructor raises
``TypeError`` immediately rather than letting ``poll()`` block later.
"""

PREFIX = b"\x1b[ARGUS] "
_FORMATS = ("MONO_HLSB", "MONO_HMSB", "MONO_VLSB", "GS8", "RGB565", "RGB565_BE", "RGB888")
_MAX_LINE = 128  # matches the Arduino agent's kMaxLine


def _json_string(text):
    # Match ArgusAgent.h's writeJsonString(): `"` and `\` are escaped, control chars
    # < 0x20 become `\u00XX`, everything else is emitted verbatim.
    parts = ['"']
    for ch in text:
        if ch == "\\":
            parts.append("\\\\")
        elif ch == '"':
            parts.append('\\"')
        elif ord(ch) < 0x20:
            parts.append("\\u%04x" % ord(ch))
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def _json_value(value):
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, float):
        # NaN/Infinity have no JSON representation; emit null like most JSON encoders.
        if value != value or value in (float("inf"), float("-inf")):
            return "null"
        return str(value)
    if isinstance(value, int):
        return str(value)
    return _json_string(str(value))


def _json_object(mapping):
    parts = []
    for key in sorted(mapping):
        parts.append(_json_value(str(key)) + ":" + _json_value(mapping[key]))
    return "{" + ",".join(parts) + "}"


def _make_poller(uart):
    """Build a ``select``/``uselect`` poller registered for POLLIN on ``uart``.

    Raises ``TypeError`` if neither module is importable or ``uart`` cannot be
    registered (e.g. it exposes no ``fileno()``), so a caller finds out at
    construction time rather than having ``poll()`` block forever later.
    """
    try:
        import select
    except ImportError:
        try:
            import uselect as select
        except ImportError:
            raise TypeError("uart must provide any() or be pollable")
    try:
        poller = select.poll()
        poller.register(uart, select.POLLIN)
    except Exception:
        raise TypeError("uart must provide any() or be pollable")
    return poller


class ArgusAgent:
    def __init__(
        self, uart, buffer=None, width=0, height=0, fmt=None, name="app", version="1", out=None
    ):
        if fmt is not None and fmt not in _FORMATS:
            raise ValueError("unknown framebuffer format: " + str(fmt))
        self._uart = uart
        self._out = out if out is not None else uart
        self._buffer = buffer
        self._width = width
        self._height = height
        self._fmt = fmt
        self._name = name
        self._version = version
        self._line = bytearray()
        self._discarding = False
        self._status = {}
        self._state = {}
        self.on_key = None
        self.on_serial_line = None
        self._has_any = hasattr(uart, "any")
        self._poller = None
        if not self._has_any:
            self._poller = _make_poller(uart)

    # -- public API ------------------------------------------------------------------

    def set_status(self, key, value):
        self._status[key] = value

    def set_state(self, key, value):
        self._state[key] = value

    def poll(self):
        """Service pending serial input. Call this often from the main loop."""
        while True:
            if self._has_any:
                pending = self._uart.any()
                if not pending:
                    return
                data = self._uart.read(pending)
            else:
                if not self._poller.poll(0):
                    return
                data = self._uart.read(1)
            if not data:
                return
            for byte in data:
                if byte == 10:  # "\n"
                    if self._discarding:
                        self._discarding = False
                    else:
                        self._handle_line(bytes(self._line))
                    self._line = bytearray()
                elif self._discarding:
                    continue  # mid-discard: skip until the terminating "\n"
                elif len(self._line) < _MAX_LINE - 1:
                    self._line.append(byte)
                else:
                    # Overlong line: discard everything through the next "\n" rather than
                    # starting a fresh line mid-stream, which could reparse the tail of a
                    # too-long line as if it were a legitimate command.
                    self._discarding = True
                    self._line = bytearray()

    # -- protocol --------------------------------------------------------------------

    def _caps(self):
        caps = []
        if self._buffer is not None:
            caps.append("screen")
        if self.on_key is not None:
            caps.append("input")
        if self._status:
            caps.append("status")
        if self._state:
            caps.append("state")
        return caps

    def _handle_line(self, line):
        if not line.startswith(PREFIX):
            if self.on_serial_line is not None:
                self.on_serial_line(line.decode("utf-8", "replace").rstrip("\r"))
            return
        body = line[len(PREFIX):].decode("utf-8", "replace").strip()
        if not body:
            return
        parts = body.split(" ", 1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        if cmd == "hello":
            if self._buffer is None:
                fb = "none"
            else:
                fb = "%s,%d,%d" % (self._fmt, self._width, self._height)
            self._ok(cmd, ("name=%s version=%s fb=%s caps=%s" % (
                self._name, self._version, fb, ",".join(self._caps()))).encode())
        elif cmd == "screenshot":
            if self._buffer is None:
                self._err(cmd, "no framebuffer registered")
            else:
                self._ok(cmd, self._buffer)
        elif cmd == "input":
            if self.on_key is None:
                self._err(cmd, "no key handler")
            else:
                self.on_key(arg)
                self._ok(cmd, b"")
        elif cmd == "status":
            self._ok(cmd, _json_object(self._status).encode())
        elif cmd == "state":
            self._ok(cmd, _json_object(self._state).encode())
        else:
            self._err(cmd, "unknown command")

    def _ok(self, cmd, payload):
        self._out.write(PREFIX + ("%s ok %d\n" % (cmd, len(payload))).encode())
        self._out.write(payload)
        self._out.write(b"\n")

    def _err(self, cmd, message):
        self._out.write(PREFIX + ("%s err %s\n" % (cmd, message)).encode())
