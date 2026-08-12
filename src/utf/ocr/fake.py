"""Fake OCR provider for tests and development without tesseract."""

from __future__ import annotations

from PIL.Image import Image

from utf.ocr.base import OCRProvider, OCRResult


class FakeOCRProvider(OCRProvider):
    """Returns queued or fixed text instead of running real OCR."""

    name = "fake"

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls: int = 0

    def is_available(self) -> tuple[bool, str]:
        return True, ""

    def extract_text(self, image: Image) -> OCRResult:
        self.calls += 1
        return OCRResult(text=self.text)
