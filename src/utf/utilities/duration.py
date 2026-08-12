"""Human-friendly duration parsing ("10s", "250ms", "2m", "1.5s")."""

from __future__ import annotations

import re

from utf.exceptions import ConfigurationError

_DURATION_RE = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h)?\s*$")

_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: str | int | float) -> float:
    """Parse a human-readable duration into seconds.

    Accepts bare numbers (seconds) or strings with a unit suffix:
    ``ms``, ``s``, ``m``, ``h``. Examples: ``"10s"``, ``"250ms"``, ``5``.
    """
    if isinstance(value, (int, float)):
        if value < 0:
            raise ConfigurationError(f"Duration must be non-negative, got {value!r}")
        return float(value)

    match = _DURATION_RE.match(value)
    if not match:
        raise ConfigurationError(
            f"Invalid duration {value!r}",
            remediation='Use a number with an optional unit, e.g. "10s", "250ms", "2m".',
        )
    number = float(match.group("value"))
    unit = match.group("unit") or "s"
    return number * _UNIT_SECONDS[unit]


def format_duration(seconds: float) -> str:
    """Format seconds for human-readable reports (e.g. ``1.42s``)."""
    if seconds < 0.001:
        return f"{seconds * 1000:.2f}ms"
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}m{secs:04.1f}s"
