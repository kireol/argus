"""AndroidCoordinateMapper — raw touch-panel units → screen pixels.

Touch panels report in their own units (e.g. 0–4095) in the panel's *natural*
orientation. The screen the user (and ``screencap``) sees may be rotated.
The mapping is therefore:

1. normalise the raw axis into ``[0, 1]`` using the device's axis ranges,
2. optionally invert an axis (some OEM panels are mounted flipped),
3. scale to the natural screen size,
4. apply the display rotation (0/90/180/270° counter-clockwise, Android's
   ``Surface.ROTATION_*``) so the result matches the rotated screenshot.

Everything is explicit and testable; nothing is assumed about one device.
"""

from __future__ import annotations

from dataclasses import dataclass

from argus_test_creator.adapters.android.models import AndroidInputDevice, AxisRange
from argus_test_creator.models.common import Point


@dataclass(frozen=True)
class AndroidCoordinateMapper:
    x_range: AxisRange
    y_range: AxisRange
    #: Screen size in the *natural* orientation (portrait for phones).
    natural_width: int
    natural_height: int
    #: 0..3 — multiples of 90° as reported by Android (``Surface.ROTATION_*``).
    rotation: int = 0
    invert_x: bool = False
    invert_y: bool = False
    #: Swap the (normalised) axes — rare panels mounted sideways. Ranges still
    #: describe the raw axes as the device reports them.
    swap_axes: bool = False

    @classmethod
    def for_device(
        cls,
        device: AndroidInputDevice,
        *,
        natural_width: int,
        natural_height: int,
        rotation: int = 0,
        invert_x: bool = False,
        invert_y: bool = False,
        swap_axes: bool = False,
    ) -> AndroidCoordinateMapper:
        """Build from a discovered input device; falls back to the screen size as range."""
        x_range = device.x_range() or AxisRange(min=0, max=max(natural_width - 1, 1))
        y_range = device.y_range() or AxisRange(min=0, max=max(natural_height - 1, 1))
        return cls(x_range=x_range, y_range=y_range, natural_width=natural_width,
                   natural_height=natural_height, rotation=rotation % 4,
                   invert_x=invert_x, invert_y=invert_y, swap_axes=swap_axes)

    @property
    def screen_size(self) -> tuple[int, int]:
        if self.rotation in (1, 3):
            return (self.natural_height, self.natural_width)
        return (self.natural_width, self.natural_height)

    def with_rotation(self, rotation: int) -> AndroidCoordinateMapper:
        return AndroidCoordinateMapper(
            x_range=self.x_range, y_range=self.y_range, natural_width=self.natural_width,
            natural_height=self.natural_height, rotation=rotation % 4,
            invert_x=self.invert_x, invert_y=self.invert_y, swap_axes=self.swap_axes,
        )

    def map(self, raw_x: int, raw_y: int) -> Point:
        nx = _normalise(raw_x, self.x_range)
        ny = _normalise(raw_y, self.y_range)
        if self.swap_axes:
            nx, ny = ny, nx
        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny
        # natural-orientation pixels
        px = nx * max(self.natural_width - 1, 0)
        py = ny * max(self.natural_height - 1, 0)
        w, h = self.natural_width, self.natural_height
        match self.rotation:
            case 1:  # 90° — landscape, device turned counter-clockwise
                sx, sy = py, (w - 1) - px
            case 2:
                sx, sy = (w - 1) - px, (h - 1) - py
            case 3:  # 270°
                sx, sy = (h - 1) - py, px
            case _:
                sx, sy = px, py
        sw, sh = self.screen_size
        return Point(x=_clamp(round(sx), sw), y=_clamp(round(sy), sh))


def _normalise(value: int, axis: AxisRange) -> float:
    if axis.span <= 0:
        return 0.0
    return min(max((value - axis.min) / axis.span, 0.0), 1.0)


def _clamp(value: int, size: int) -> int:
    if size <= 0:
        return max(value, 0)
    return min(max(value, 0), size - 1)


__all__ = ["AndroidCoordinateMapper"]
