"""Shared primitives: points, rectangles, durations (Argus-compatible)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_DURATION_RE = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h)?\s*$")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: str | int | float) -> float:
    """Parse an Argus duration (``"10s"``, ``"250ms"``, ``5``) into seconds."""
    if isinstance(value, bool):
        raise ValueError(f"Invalid duration {value!r}")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError(f"Duration must be non-negative, got {value!r}")
        return float(value)
    match = _DURATION_RE.match(str(value))
    if not match:
        raise ValueError(
            f"Invalid duration {value!r}: use a number with an optional unit (10s, 250ms, 2m)."
        )
    return float(match.group("value")) * _UNIT_SECONDS[match.group("unit") or "s"]


def format_duration(seconds: float) -> str:
    """Format seconds in the compact Argus style (``250ms``, ``1.5s``, ``2m``)."""
    if seconds <= 0:
        return "0s"
    if seconds < 1:
        return f"{round(seconds * 1000)}ms"
    if seconds < 60:
        text = f"{seconds:.2f}".rstrip("0").rstrip(".")
        return f"{text}s"
    if seconds % 60 == 0:
        return f"{int(seconds // 60)}m"
    return f"{seconds:g}s"


class Point(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: int
    y: int

    def as_tuple(self) -> tuple[int, int]:
        return (self.x, self.y)


class Rect(BaseModel):
    """A pixel rectangle; serializes to Argus's ``region`` mapping."""

    model_config = ConfigDict(frozen=True)

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def to_argus(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def as_box(self) -> tuple[int, int, int, int]:
        """PIL crop box (left, upper, right, lower)."""
        return (self.x, self.y, self.right, self.bottom)

    @classmethod
    def from_points(cls, x1: int, y1: int, x2: int, y2: int) -> Rect:
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        return cls(x=max(left, 0), y=max(top, 0), width=max(right - left, 1),
                   height=max(bottom - top, 1))

    @classmethod
    def from_any(cls, value: Any) -> Rect | None:
        if value is None:
            return None
        if isinstance(value, Rect):
            return value
        if isinstance(value, dict):
            return cls.model_validate(value)
        raise ValueError(f"Not a region mapping: {value!r}")
