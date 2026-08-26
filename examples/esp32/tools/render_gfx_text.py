#!/usr/bin/env python3
"""Render "ARGUS" the way the firmware draws it, for images/title.png.

The ESP32 example's home screen prints "ARGUS" with Adafruit_GFX's built-in
5x7 font (``display.setTextSize(2); display.setCursor(0, 0); display.print
("ARGUS");`` in src/main.cpp) — the classic font baked into every
Adafruit_GFX / Adafruit_SSD1306 install (``glcdfont.c``), not a font file this
repo ships. To get a reference crop for the `image_present: title.png` test
without a build toolchain, this script embeds that font's 5-byte glyph
columns for just the five letters this example needs (A, R, G, U, S) and
replicates Adafruit_GFX's ``drawChar`` pixel placement at text size 2,
starting at the same (0, 0) the firmware uses.

Caveats (see README.md "Troubleshooting"):
  * The glyph columns below are transcribed from memory of the well-known
    Adafruit_GFX default font and were NOT diffed byte-for-byte against
    glcdfont.c in this environment (no toolchain here to pull the header and
    compare). They are very likely correct — this is the standard 5x7 font
    used by Adafruit_GFX/SSD1306 sketches everywhere — but they are not
    verified against a real capture.
  * This PNG was never compared against an actual Wokwi/board screenshot.
    Regenerate it from a real run: `argus run --config examples/esp32/argus.yaml
    --save-comparisons`, then copy the saved "actual" ESP-003 crop over this
    file (or re-run this script and diff) if `image_present: title.png`
    fails once real hardware/Wokwi is available.

Usage:
    python examples/esp32/tools/render_gfx_text.py [output_path]

Defaults to writing images/title.png next to this tools/ directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

TEXT = "ARGUS"
TEXT_SIZE = 2  # matches display.setTextSize(2) in src/main.cpp

# Adafruit_GFX default 5x7 font (glcdfont.c), columns for the glyphs this
# example needs. Each glyph is 5 bytes, one per column, bit 0 = top row,
# bits 0-6 used (7 pixel rows); bit 7 is always 0 in this font.
FONT_5X7: dict[str, tuple[int, int, int, int, int]] = {
    "A": (0x7C, 0x12, 0x11, 0x12, 0x7C),
    "R": (0x7F, 0x09, 0x19, 0x29, 0x46),
    "G": (0x3E, 0x41, 0x49, 0x49, 0x7A),
    "U": (0x3F, 0x40, 0x40, 0x40, 0x3F),
    "S": (0x46, 0x49, 0x49, 0x49, 0x31),
}

GLYPH_COLS = 5
GLYPH_ROWS = 8  # Adafruit_GFX's line pitch for this font (7 pixel rows + 1 blank)
CHAR_ADVANCE_COLS = GLYPH_COLS + 1  # 1-pixel gap between characters

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def render(text: str, size: int) -> Image.Image:
    """Render `text` the way Adafruit_GFX's drawChar() would, at (0, 0)."""
    # Drop the trailing inter-character gap after the last glyph: nothing is
    # drawn there, and the firmware's screen has nothing else in that column
    # either (background), so trimming it just tightens the reference crop.
    width = (CHAR_ADVANCE_COLS * len(text) - 1) * size
    height = GLYPH_ROWS * size
    img = Image.new("RGB", (width, height), BLACK)
    pixels = img.load()
    assert pixels is not None

    for char_index, ch in enumerate(text):
        glyph = FONT_5X7.get(ch.upper())
        if glyph is None:
            raise ValueError(
                f"No glyph embedded for {ch!r}; this script only knows "
                f"{sorted(FONT_5X7)} (the letters in 'ARGUS')."
            )
        char_x = char_index * CHAR_ADVANCE_COLS * size
        for col in range(GLYPH_COLS):
            column_bits = glyph[col]
            for row in range(GLYPH_ROWS):
                if not (column_bits & (1 << row)):
                    continue
                px = char_x + col * size
                py = row * size
                for dy in range(size):
                    for dx in range(size):
                        pixels[px + dx, py + dy] = WHITE
    return img


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        out_path = Path(argv[1])
    else:
        out_path = Path(__file__).resolve().parent.parent / "images" / "title.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    image = render(TEXT, TEXT_SIZE)
    image.save(out_path)
    print(f"Wrote {out_path} ({image.width}x{image.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
