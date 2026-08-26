"""Screen-change detection (numpy only; no OpenCV dependency in the Creator)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL.Image import Image

from argus_test_creator.models.common import Rect


@dataclass(frozen=True)
class ScreenDiff:
    changed_fraction: float
    changed_region: Rect | None
    mean_delta: float

    @property
    def significant(self) -> bool:
        return self.changed_fraction >= 0.01

    @property
    def major(self) -> bool:
        return self.changed_fraction >= 0.25


def compare_images(before: Image, after: Image, *, threshold: int = 24) -> ScreenDiff:
    """Compare two screenshots; returns the fraction and bounding box of changed pixels."""
    if before.size != after.size:
        return ScreenDiff(changed_fraction=1.0, mean_delta=255.0,
                          changed_region=Rect(x=0, y=0, width=after.width, height=after.height))
    a = np.asarray(before.convert("L"), dtype=np.int16)
    b = np.asarray(after.convert("L"), dtype=np.int16)
    delta = np.abs(a - b)
    mask = delta > threshold
    changed = int(mask.sum())
    total = mask.size or 1
    region: Rect | None = None
    if changed:
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        region = Rect(
            x=int(cols[0]), y=int(rows[0]),
            width=int(cols[-1] - cols[0] + 1), height=int(rows[-1] - rows[0] + 1),
        )
    return ScreenDiff(changed_fraction=changed / total, changed_region=region,
                      mean_delta=float(delta.mean()))


def is_stable(frames: list[Image], *, threshold: int = 24, tolerance: float = 0.002) -> bool:
    """True when consecutive frames differ by less than ``tolerance`` of pixels."""
    return all(
        compare_images(frames[i], frames[i + 1], threshold=threshold).changed_fraction <= tolerance
        for i in range(len(frames) - 1)
    )
