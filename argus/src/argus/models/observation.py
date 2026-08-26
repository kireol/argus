"""Observation model — what was actually captured from a device.

A single observation (screenshot + metadata) is reusable by multiple
verifiers so that one capture can satisfy several verification conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL.Image import Image

    from argus.models.common import ScreenInfo


@dataclass(frozen=True)
class Observation:
    """A point-in-time capture of externally observable application state."""

    image: Image
    device: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    screen: ScreenInfo | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size
