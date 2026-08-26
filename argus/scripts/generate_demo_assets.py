"""Generate the demo reference images used by the example test suite.

Run from the repository root:

    python scripts/generate_demo_assets.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "images"


def movie_artwork(base: tuple[int, int, int], accent: tuple[int, int, int]) -> Image.Image:
    """A distinctive 200x200 'movie poster' with structure for template matching."""
    img = Image.new("RGB", (200, 200), base)
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 189, 189], outline=accent, width=6)
    draw.ellipse([50, 30, 150, 130], fill=accent)
    draw.rectangle([40, 150, 160, 175], fill=(255, 255, 255))
    draw.polygon([(100, 45), (120, 95), (80, 95)], fill=base)
    return img


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    movie_artwork((30, 60, 130), (240, 200, 60)).save(ASSETS / "movie_123.png")
    movie_artwork((130, 30, 40), (60, 220, 180)).save(ASSETS / "movie_456.png")
    print(f"Demo assets written to {ASSETS}")


if __name__ == "__main__":
    main()
