"""OCR preprocess helpers."""

from PIL import Image, ImageDraw

from argus.ocr.preprocess import isolate_light_text


def test_isolate_light_text_keeps_bright_glyphs_only():
    img = Image.new("RGB", (80, 40), (240, 80, 160))  # bright-ish wallpaper
    draw = ImageDraw.Draw(img)
    draw.rectangle((10, 5, 40, 35), fill=(255, 255, 255))  # white glyph block

    out = isolate_light_text(img, luminance=235)
    pixels = list(out.getdata())
    # White glyph → black (0); pink wallpaper below 235 → white (255).
    assert 0 in pixels
    assert 255 in pixels
    assert out.mode == "L"
