"""Shared model primitives."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Region(BaseModel):
    """A rectangular screen region in pixels."""

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

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class HealthCheckResult(BaseModel):
    """Result of a device/backend/instrumentation health check."""

    status: HealthStatus
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY

    @classmethod
    def ok(cls, message: str = "OK", **details: Any) -> HealthCheckResult:
        return cls(status=HealthStatus.HEALTHY, message=message, details=details)

    @classmethod
    def failed(cls, message: str, **details: Any) -> HealthCheckResult:
        return cls(status=HealthStatus.UNHEALTHY, message=message, details=details)


class ScreenInfo(BaseModel):
    """Screen geometry reported by a device."""

    width: int
    height: int
    dpi: float | None = None
    scale: float | None = None
    orientation: str | None = None

    @model_validator(mode="after")
    def _validate_size(self) -> ScreenInfo:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("screen width/height must be positive")
        return self

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)
