"""GetEventParser — ``getevent`` text → :class:`AndroidRawInputEvent`.

Two formats are understood, both produced by the ``-l`` (label) switch:

* the live stream (``getevent -lt``)::

      [   10.000000] /dev/input/event2: EV_ABS       ABS_MT_POSITION_X    00000200
      /dev/input/event2: EV_KEY       KEY_BACK             DOWN          (without -t)
      [   10.0] /dev/input/event2: 0005         0042                 00000001  (unnamed)

* the device listing (``getevent -lp``) — see :func:`parse_input_devices`.

The parser has no UI, ADB, or gesture knowledge. Malformed lines return
``None`` and are counted so diagnostics can show them.
"""

from __future__ import annotations

import re

from argus_test_creator.adapters.android.models import (
    AndroidInputDevice,
    AndroidRawInputEvent,
    AxisRange,
    EventType,
)

_LINE_RE = re.compile(
    r"^\s*(?:\[\s*(?P<ts>\d+(?:\.\d+)?)\s*\]\s*)?"
    r"(?P<device>/dev/input/[^:\s]+):\s+"
    r"(?P<type>\S+)\s+(?P<code>\S+)\s+(?P<value>\S+)\s*$"
)
_NAMED_VALUES = {"DOWN": 1, "UP": 0, "REPEAT": 2}
_EVENT_TYPE_BY_HEX = {
    "0000": EventType.EV_SYN, "0001": EventType.EV_KEY, "0002": EventType.EV_REL,
    "0003": EventType.EV_ABS, "0004": EventType.EV_MSC, "0005": EventType.EV_SW,
    "0011": EventType.EV_LED, "0012": EventType.EV_SND, "0014": EventType.EV_REP,
    "0015": EventType.EV_FF, "0016": EventType.EV_PWR,
}


class GetEventParser:
    """Stateless line parser with counters (thread-confined: one per stream)."""

    def __init__(self) -> None:
        self.parsed = 0
        self.malformed = 0
        self.unknown = 0
        self.last_malformed: str | None = None

    def parse_line(self, line: str) -> AndroidRawInputEvent | None:
        text = line.rstrip("\r\n")
        if not text.strip():
            return None
        match = _LINE_RE.match(text)
        if match is None:
            self._malformed(text)
            return None
        value = _parse_value(match.group("value"))
        if value is None:
            self._malformed(text)
            return None
        type_text = match.group("type")
        code = match.group("code")
        raw: dict[str, str] = {}
        try:
            event_type = EventType(type_text)
        except ValueError:
            event_type = _EVENT_TYPE_BY_HEX.get(type_text.lower(), EventType.UNKNOWN)
            raw["type"] = type_text
        if event_type == EventType.UNKNOWN or _is_hex_code(code):
            self.unknown += 1
            raw.setdefault("type", type_text)
            raw["code"] = code
        ts_text = match.group("ts")
        event = AndroidRawInputEvent(
            timestamp=float(ts_text) if ts_text is not None else None,
            device=match.group("device"),
            event_type=event_type,
            code=code,
            value=value,
            raw=raw,
        )
        self.parsed += 1
        return event

    def parse_lines(self, text: str) -> list[AndroidRawInputEvent]:
        events = []
        for line in text.splitlines():
            event = self.parse_line(line)
            if event is not None:
                events.append(event)
        return events

    def _malformed(self, text: str) -> None:
        self.malformed += 1
        self.last_malformed = text[:200]


def _parse_value(text: str) -> int | None:
    named = _NAMED_VALUES.get(text.upper())
    if named is not None:
        return named
    try:
        value = int(text, 16)
    except ValueError:
        return None
    if len(text) == 8 and value >= 0x8000_0000:
        value -= 0x1_0000_0000  # 32-bit two's complement (ffffffff → -1)
    return value


def _is_hex_code(code: str) -> bool:
    return len(code) == 4 and all(c in "0123456789abcdefABCDEF" for c in code)


# -- getevent -lp --------------------------------------------------------------------------

_ADD_DEVICE_RE = re.compile(r"^add device \d+:\s*(?P<path>/dev/input/\S+)")
_NAME_RE = re.compile(r'^\s*name:\s*"(?P<name>.*)"\s*$')
_TYPE_RE = re.compile(r"^\s*(?P<type>[A-Z]+)\s*\((?P<hex>[0-9a-fA-F]{4})\):\s*(?P<rest>.*)$")
_ABS_RE = re.compile(
    r"^(?P<code>\S+)\s*:\s*value\s+-?\d+,\s*min\s+(?P<min>-?\d+),\s*max\s+(?P<max>-?\d+)"
    r"(?:,\s*fuzz\s+-?\d+)?(?:,\s*flat\s+-?\d+)?(?:,\s*resolution\s+(?P<res>-?\d+))?"
)


def parse_input_devices(text: str) -> list[AndroidInputDevice]:
    """Parse ``getevent -lp`` (or ``-p``) output into input devices."""
    devices: list[AndroidInputDevice] = []
    current: dict | None = None
    section: str | None = None  # "events" | "props"
    current_type: str | None = None

    def finish() -> None:
        if current is not None:
            devices.append(AndroidInputDevice(
                path=current["path"], name=current["name"],
                capabilities={k: tuple(v) for k, v in current["caps"].items()},
                axis_ranges=current["ranges"], properties=tuple(current["props"]),
            ))

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        add = _ADD_DEVICE_RE.match(line)
        if add:
            finish()
            current = {"path": add.group("path"), "name": "", "caps": {}, "ranges": {},
                       "props": []}
            section = current_type = None
            continue
        if current is None:
            continue
        name = _NAME_RE.match(line)
        if name:
            current["name"] = name.group("name")
            continue
        stripped = line.strip()
        if stripped.startswith("events:"):
            section, current_type = "events", None
            continue
        if stripped.startswith("input props:"):
            section, current_type = "props", None
            continue
        if stripped.startswith(("could not", "version", "id:", "bus", "vendor", "product")):
            continue
        if section == "props":
            if stripped and stripped != "<none>":
                current["props"].append(stripped)
            continue
        if section != "events":
            continue
        typed = _TYPE_RE.match(line)
        if typed:
            current_type = f"EV_{typed.group('type')}"
            rest = typed.group("rest")
        else:
            rest = stripped
        if current_type is None or not rest:
            continue
        codes = current["caps"].setdefault(current_type, [])
        if current_type == "EV_ABS":
            absmatch = _ABS_RE.match(rest)
            if absmatch:
                code = absmatch.group("code")
                codes.append(code)
                current["ranges"][code] = AxisRange(
                    min=int(absmatch.group("min")), max=int(absmatch.group("max")),
                    resolution=int(absmatch.group("res") or 0),
                )
            else:
                codes.extend(rest.split())
        else:
            codes.extend(rest.split())
    finish()
    return devices


__all__ = ["GetEventParser", "parse_input_devices"]
