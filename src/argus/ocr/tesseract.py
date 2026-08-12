"""Tesseract OCR provider (requires the optional ``argus[ocr]`` extra)."""

from __future__ import annotations

import shutil

from PIL.Image import Image

from argus.exceptions import VerificationError
from argus.models.common import Region
from argus.ocr.base import OCRProvider, OCRResult, OCRWord


class TesseractProvider(OCRProvider):
    name = "tesseract"

    def __init__(self, language: str = "eng") -> None:
        self._language = language

    def is_available(self) -> tuple[bool, str]:
        try:
            import pytesseract  # noqa: F401
        except ImportError:
            return False, 'pytesseract not installed. Install with: pip install "argus[ocr]"'
        if shutil.which("tesseract") is None:
            return False, (
                "tesseract binary not found on PATH. "
                "Install it (e.g. 'brew install tesseract' / 'apt install tesseract-ocr')."
            )
        return True, ""

    def extract_text(self, image: Image) -> OCRResult:
        available, reason = self.is_available()
        if not available:
            raise VerificationError(f"OCR unavailable: {reason}")

        import pytesseract

        data = pytesseract.image_to_data(
            image, lang=self._language, output_type=pytesseract.Output.DICT
        )
        words: list[OCRWord] = []
        for i, text in enumerate(data["text"]):
            text = text.strip()
            if not text:
                continue
            confidence = float(data["conf"][i])
            words.append(
                OCRWord(
                    text=text,
                    confidence=None if confidence < 0 else confidence / 100.0,
                    region=Region(
                        x=int(data["left"][i]),
                        y=int(data["top"][i]),
                        width=max(1, int(data["width"][i])),
                        height=max(1, int(data["height"][i])),
                    ),
                )
            )
        return OCRResult(text=" ".join(w.text for w in words), words=words)
