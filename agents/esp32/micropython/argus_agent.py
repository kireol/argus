"""Argus test agent for MicroPython (ESP32).

Usage::

    from machine import UART, Pin
    import ssd1306
    from argus_agent import ArgusAgent

    i2c = I2C(0, scl=Pin(22), sda=Pin(21))
    oled = ssd1306.SSD1306_I2C(128, 64, i2c)
    argus = ArgusAgent(sys.stdin.buffer if USE_REPL else UART(0), oled.buffer, 128, 64, "MONO_VLSB")
    argus.on_key = handle_key
    while True:
        argus.poll()

The agent shares the UART with normal ``print`` output; anything that is not
an Argus command line is left alone (and passed to ``on_serial_line`` if set).
"""

PREFIX = b"\x1b[ARGUS] "
_FORMATS = ("MONO_HLSB", "MONO_HMSB", "MONO_VLSB", "GS8", "RGB565", "RGB565_BE", "RGB888")


def _json_value(value):
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + text + '"'


def _json_object(mapping):
    parts = []
    for key in sorted(mapping):
        parts.append(_json_value(str(key)) + ":" + _json_value(mapping[key]))
    return "{" + ",".join(parts) + "}"


class ArgusAgent:
    def __init__(self, uart, buffer=None, width=0, height=0, fmt=None, name="app", version="1"):
        if fmt is not None and fmt not in _FORMATS:
            raise ValueError("unknown framebuffer format: " + str(fmt))
        self._uart = uart
        self._buffer = buffer
        self._width = width
        self._height = height
        self._fmt = fmt
        self._name = name
        self._version = version
        self._line = b""
        self._status = {}
        self._state = {}
        self.on_key = None
        self.on_serial_line = None

    # -- public API ------------------------------------------------------------------

    def set_status(self, key, value):
        self._status[key] = value

    def set_state(self, key, value):
        self._state[key] = value

    def poll(self):
        """Service pending serial input. Call this often from the main loop."""
        while True:
            pending = self._uart.any() if hasattr(self._uart, "any") else 1
            if not pending:
                return
            data = self._uart.read(pending)
            if not data:
                return
            for byte in data:
                if byte == 10:  # "\n"
                    self._handle_line(self._line)
                    self._line = b""
                else:
                    self._line += bytes([byte])

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
        self._uart.write(PREFIX + ("%s ok %d\n" % (cmd, len(payload))).encode())
        self._uart.write(payload)
        self._uart.write(b"\n")

    def _err(self, cmd, message):
        self._uart.write(PREFIX + ("%s err %s\n" % (cmd, message)).encode())
