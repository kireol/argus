from __future__ import annotations

import pytest

from argus_test_creator.adapters.android.coordinates import AndroidCoordinateMapper
from argus_test_creator.adapters.android.getevent_parser import parse_input_devices
from argus_test_creator.adapters.android.models import AndroidInputDevice, AxisRange


def mapper(**overrides) -> AndroidCoordinateMapper:
    params = dict(x_range=AxisRange(min=0, max=4095), y_range=AxisRange(min=0, max=4095),
                  natural_width=1080, natural_height=2400, rotation=0)
    params.update(overrides)
    return AndroidCoordinateMapper(**params)


def test_scaling_from_raw_range_to_screen():
    m = mapper()
    assert m.map(0, 0).as_tuple() == (0, 0)
    assert m.map(4095, 4095).as_tuple() == (1079, 2399)
    mid = m.map(2048, 2048)
    assert abs(mid.x - 540) <= 1 and abs(mid.y - 1200) <= 1


def test_identity_when_ranges_match_screen():
    m = mapper(x_range=AxisRange(min=0, max=1079), y_range=AxisRange(min=0, max=2399))
    assert m.map(300, 700).as_tuple() == (300, 700)


def test_offset_ranges_and_clamping():
    m = mapper(x_range=AxisRange(min=100, max=1179), y_range=AxisRange(min=50, max=2449))
    assert m.map(100, 50).as_tuple() == (0, 0)
    assert m.map(400, 750).as_tuple() == (300, 700)
    assert m.map(-5000, 99999).as_tuple() == (0, 2399)


def test_inverted_axes():
    m = mapper(x_range=AxisRange(min=0, max=1079), y_range=AxisRange(min=0, max=2399),
               invert_x=True, invert_y=True)
    assert m.map(0, 0).as_tuple() == (1079, 2399)
    assert m.map(1079, 2399).as_tuple() == (0, 0)


def test_swapped_axes():
    m = mapper(x_range=AxisRange(min=0, max=2399), y_range=AxisRange(min=0, max=1079),
               swap_axes=True)
    # raw (y-ish, x-ish) → screen
    assert m.map(700, 300).as_tuple() == (300, 700)


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, (300, 700)),
        (1, (700, 779)),     # 90° CCW: x' = y, y' = W-1-x
        (2, (779, 1699)),    # 180°
        (3, (1699, 300)),    # 270°: x' = H-1-y, y' = x
    ],
)
def test_rotation_transforms_natural_coordinates(rotation, expected):
    m = mapper(x_range=AxisRange(min=0, max=1079), y_range=AxisRange(min=0, max=2399),
               rotation=rotation)
    assert m.map(300, 700).as_tuple() == expected


def test_rotation_swaps_screen_size():
    assert mapper(rotation=0).screen_size == (1080, 2400)
    assert mapper(rotation=1).screen_size == (2400, 1080)
    assert mapper(rotation=3).screen_size == (2400, 1080)
    assert mapper(rotation=2).screen_size == (1080, 2400)
    assert mapper().with_rotation(5).rotation == 1


def test_landscape_corners_land_in_screenshot_bounds():
    m = mapper(rotation=1)
    for rx, ry in ((0, 0), (4095, 0), (0, 4095), (4095, 4095)):
        p = m.map(rx, ry)
        assert 0 <= p.x <= 2399 and 0 <= p.y <= 1079


def test_degenerate_axis_range_does_not_divide_by_zero():
    m = mapper(x_range=AxisRange(min=5, max=5))
    assert m.map(5, 0).x == 0


def test_for_device_uses_axis_ranges_and_falls_back():
    text = """add device 1: /dev/input/event2
  name:     "ts"
  events:
    ABS (0003): ABS_MT_POSITION_X     : value 0, min 0, max 8191, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y     : value 0, min 0, max 8191, fuzz 0, flat 0, resolution 0
"""
    (device,) = parse_input_devices(text)
    m = AndroidCoordinateMapper.for_device(device, natural_width=1080, natural_height=2400)
    assert m.x_range.max == 8191
    assert m.map(8191, 8191).as_tuple() == (1079, 2399)
    bare = AndroidInputDevice(path="/dev/input/event9", name="x")
    m2 = AndroidCoordinateMapper.for_device(bare, natural_width=720, natural_height=1280)
    assert m2.map(719, 1279).as_tuple() == (719, 1279)
