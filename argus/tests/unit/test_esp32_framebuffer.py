"""Raw framebuffer decoding for the ESP32 adapter."""

from __future__ import annotations

import pytest

from argus.adapters.esp32.framebuffer import FORMATS, decode, expected_length
from argus.exceptions import ScreenshotError

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def test_formats_listed():
    assert FORMATS == (
        "MONO_HLSB", "MONO_HMSB", "MONO_VLSB", "GS8", "RGB565", "RGB565_BE", "RGB888"
    )


@pytest.mark.parametrize(
    ("fmt", "w", "h", "length"),
    [
        ("MONO_HLSB", 12, 3, 6),   # ceil(12/8)=2 bytes per row
        ("MONO_HMSB", 8, 2, 2),
        ("MONO_VLSB", 5, 9, 10),   # ceil(9/8)=2 pages * 5 columns
        ("GS8", 3, 2, 6),
        ("RGB565", 3, 2, 12),
        ("RGB565_BE", 3, 2, 12),
        ("RGB888", 3, 2, 18),
    ],
)
def test_expected_length(fmt, w, h, length):
    assert expected_length(fmt, w, h) == length


def test_mono_hlsb_bit7_is_leftmost():
    # 8x2: row0 = 0b10000001 -> x=0 and x=7 set; row1 = 0b01000000 -> x=1 set
    img = decode(bytes([0b10000001, 0b01000000]), "MONO_HLSB", 8, 2)
    assert img.mode == "RGB" and img.size == (8, 2)
    assert img.getpixel((0, 0)) == WHITE
    assert img.getpixel((7, 0)) == WHITE
    assert img.getpixel((1, 0)) == BLACK
    assert img.getpixel((1, 1)) == WHITE
    assert img.getpixel((0, 1)) == BLACK


def test_mono_hmsb_bit0_is_leftmost():
    img = decode(bytes([0b00000001, 0b10000000]), "MONO_HMSB", 8, 2)
    assert img.getpixel((0, 0)) == WHITE
    assert img.getpixel((7, 0)) == BLACK
    assert img.getpixel((7, 1)) == WHITE


def test_mono_hlsb_row_padding():
    # width 10 -> 2 bytes per row; second byte's bit7 is x=8
    img = decode(bytes([0x00, 0x80, 0xFF, 0xC0]), "MONO_HLSB", 10, 2)
    assert img.getpixel((8, 0)) == WHITE and img.getpixel((9, 0)) == BLACK
    assert img.getpixel((9, 1)) == WHITE


def test_mono_vlsb_page_layout():
    # 4 columns x 16 rows = 2 pages. Byte index = page*width + x; bit b = row page*8+b.
    data = bytearray(8)
    data[0] = 0b00000001  # page0, x=0, row 0
    data[3] = 0b10000000  # page0, x=3, row 7
    data[4 + 2] = 0b00000010  # page1, x=2, row 9
    img = decode(bytes(data), "MONO_VLSB", 4, 16)
    assert img.getpixel((0, 0)) == WHITE
    assert img.getpixel((3, 7)) == WHITE
    assert img.getpixel((2, 9)) == WHITE
    assert img.getpixel((2, 8)) == BLACK
    assert img.getpixel((1, 1)) == BLACK


def test_mono_colors_override():
    img = decode(bytes([0b10000000]), "MONO_HLSB", 8, 1, mono_colors=("#ff0000", "#0000ff"))
    assert img.getpixel((0, 0)) == (255, 0, 0)
    assert img.getpixel((1, 0)) == (0, 0, 255)


def test_gs8():
    img = decode(bytes([0, 128, 255]), "GS8", 3, 1)
    assert img.getpixel((0, 0)) == BLACK
    assert img.getpixel((1, 0)) == (128, 128, 128)
    assert img.getpixel((2, 0)) == WHITE


def test_rgb565_little_endian():
    # red 0xF800, green 0x07E0, blue 0x001F as little-endian bytes
    data = bytes([0x00, 0xF8, 0xE0, 0x07, 0x1F, 0x00])
    img = decode(data, "RGB565", 3, 1)
    assert img.getpixel((0, 0)) == (255, 0, 0)
    assert img.getpixel((1, 0)) == (0, 255, 0)
    assert img.getpixel((2, 0)) == (0, 0, 255)


def test_rgb565_big_endian():
    data = bytes([0xF8, 0x00, 0x07, 0xE0, 0x00, 0x1F])
    img = decode(data, "RGB565_BE", 3, 1)
    assert img.getpixel((0, 0)) == (255, 0, 0)
    assert img.getpixel((1, 0)) == (0, 255, 0)
    assert img.getpixel((2, 0)) == (0, 0, 255)


def test_rgb565_mid_values_expand_to_full_range():
    # 0x7BEF = 01111 011111 01111 -> (15<<3|15>>2, 31<<2|31>>4, 15<<3|15>>2) = (123,125,123)
    img = decode(bytes([0xEF, 0x7B]), "RGB565", 1, 1)
    assert img.getpixel((0, 0)) == (123, 125, 123)


def test_rgb888():
    img = decode(bytes([1, 2, 3, 4, 5, 6]), "RGB888", 2, 1)
    assert img.getpixel((0, 0)) == (1, 2, 3)
    assert img.getpixel((1, 0)) == (4, 5, 6)


def test_wrong_length_raises():
    with pytest.raises(ScreenshotError, match="expected 2 bytes, got 1"):
        decode(b"\x00", "MONO_HLSB", 8, 2)


def test_unknown_format_raises():
    with pytest.raises(ScreenshotError, match="MONO_VLSB"):
        decode(b"", "BGR233", 1, 1)
