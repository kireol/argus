"""Decode raw display framebuffers dumped by the ESP32 agent into PIL images.

Format names follow MicroPython's ``framebuf`` module so the Arduino agent,
the MicroPython agent, and this decoder share one vocabulary.
"""

from __future__ import annotations

import sys
from array import array

from PIL import Image as PILImage
from PIL import ImageColor
from PIL.Image import Image

from argus.exceptions import ScreenshotError

FORMATS: tuple[str, ...] = (
    "MONO_HLSB",
    "MONO_HMSB",
    "MONO_VLSB",
    "GS8",
    "RGB565",
    "RGB565_BE",
    "RGB888",
)


def expected_length(fmt: str, width: int, height: int) -> int:
    """Number of bytes a ``width`` x ``height`` framebuffer occupies in ``fmt``."""
    row_bytes = (width + 7) // 8
    pages = (height + 7) // 8
    if fmt in ("MONO_HLSB", "MONO_HMSB"):
        return row_bytes * height
    if fmt == "MONO_VLSB":
        return width * pages
    if fmt == "GS8":
        return width * height
    if fmt in ("RGB565", "RGB565_BE"):
        return width * height * 2
    if fmt == "RGB888":
        return width * height * 3
    raise ScreenshotError(
        f"Unknown framebuffer format {fmt!r}.",
        remediation=f"Use one of: {', '.join(FORMATS)}.",
    )


def decode(
    data: bytes,
    fmt: str,
    width: int,
    height: int,
    *,
    mono_colors: tuple[str, str] = ("#ffffff", "#000000"),
) -> Image:
    """Turn raw framebuffer bytes into an RGB image."""
    expected = expected_length(fmt, width, height)
    if len(data) != expected:
        raise ScreenshotError(
            f"Framebuffer size mismatch for {fmt} {width}x{height}: "
            f"expected {expected} bytes, got {len(data)}.",
            remediation="Check the fb=<FORMAT>,<w>,<h> the agent reports matches its buffer.",
        )
    if fmt == "MONO_HLSB":
        mask = PILImage.frombytes("1", (width, height), data, "raw", "1")
        return _colorize(mask, mono_colors)
    if fmt == "MONO_HMSB":
        mask = PILImage.frombytes("1", (width, height), data, "raw", "1;R")
        return _colorize(mask, mono_colors)
    if fmt == "MONO_VLSB":
        return _colorize(_unpack_vlsb(data, width, height), mono_colors)
    if fmt == "GS8":
        return PILImage.frombytes("L", (width, height), data).convert("RGB")
    if fmt == "RGB888":
        return PILImage.frombytes("RGB", (width, height), data)
    return _decode_rgb565(data, width, height, big_endian=fmt == "RGB565_BE")


def _colorize(mask: Image, colors: tuple[str, str]) -> Image:
    """Paint set bits with colors[0] and clear bits with colors[1]."""
    foreground = ImageColor.getrgb(colors[0])
    background = ImageColor.getrgb(colors[1])
    out = PILImage.new("RGB", mask.size, background)
    out.paste(foreground, mask=mask)
    return out


def _unpack_vlsb(data: bytes, width: int, height: int) -> Image:
    """SSD1306/u8g2 page layout: one byte per column per 8-row page, bit 0 on top."""
    mask = PILImage.new("1", (width, height), 0)
    pixels = mask.load()
    assert pixels is not None
    pages = (height + 7) // 8
    for page in range(pages):
        base = page * width
        for x in range(width):
            byte = data[base + x]
            if not byte:
                continue
            for bit in range(8):
                y = page * 8 + bit
                if y < height and byte & (1 << bit):
                    pixels[x, y] = 1
    return mask


def _decode_rgb565(data: bytes, width: int, height: int, *, big_endian: bool) -> Image:
    words = array("H", data)
    if (sys.byteorder == "little") == big_endian:
        words.byteswap()
    out = bytearray(width * height * 3)
    i = 0
    for value in words:
        r = (value >> 11) & 0x1F
        g = (value >> 5) & 0x3F
        b = value & 0x1F
        out[i] = (r << 3) | (r >> 2)
        out[i + 1] = (g << 2) | (g >> 4)
        out[i + 2] = (b << 3) | (b >> 2)
        i += 3
    return PILImage.frombytes("RGB", (width, height), bytes(out))
