"""Tesseract OCR provider passes."""

from PIL import Image, ImageDraw

from argus.ocr.tesseract import TesseractProvider


def test_half_scale_pass_recovers_large_digit_like_glyphs(monkeypatch):
    """Native-scale OCR can miss oversized glyphs; half-scale is merged in."""

    calls: list[tuple[int, int]] = []

    def fake_image_to_data(image, lang="eng", output_type=None):  # noqa: ARG001
        calls.append(image.size)
        w, h = image.size
        # Pretend only the downscaled frame yields a digit word.
        if w <= 100:
            return {
                "text": ["97"],
                "conf": ["90"],
                "left": [10],
                "top": [10],
                "width": [40],
                "height": [40],
            }
        return {
            "text": [""],
            "conf": ["-1"],
            "left": [0],
            "top": [0],
            "width": [1],
            "height": [1],
        }

    import pytesseract

    monkeypatch.setattr(pytesseract, "image_to_data", fake_image_to_data)
    monkeypatch.setattr(
        "argus.ocr.tesseract.shutil.which", lambda _name: "/usr/bin/tesseract"
    )

    img = Image.new("RGB", (200, 160), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((20, 20, 180, 140), fill=(204, 204, 204))

    result = TesseractProvider(isolate_light_text=False).extract_text(img)
    assert "97" in result.text
    assert any(w <= 100 for w, _ in calls)
