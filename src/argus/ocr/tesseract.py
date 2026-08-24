"""Tesseract OCR provider (requires the optional ``argus[ocr]`` extra)."""

from __future__ import annotations

import shutil

from PIL.Image import Image, Resampling

from argus.exceptions import VerificationError
from argus.models.common import Region
from argus.ocr.base import OCRProvider, OCRResult, OCRWord
from argus.ocr.preprocess import isolate_light_text


class TesseractProvider(OCRProvider):
    name = "tesseract"

    def __init__(
        self,
        language: str = "eng",
        *,
        isolate_light_text: bool = False,
        isolate_light_text_luminance: int = 180,
    ) -> None:
        self._language = language
        self._isolate_light_text = isolate_light_text
        self._isolate_light_text_luminance = isolate_light_text_luminance

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

    def _extract_words(self, image: Image) -> list[OCRWord]:
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
        return words

    def extract_text(self, image: Image) -> OCRResult:
        available, reason = self.is_available()
        if not available:
            raise VerificationError(f"OCR unavailable: {reason}")

        # Always run raw OCR. When isolate_light_text is on, also run a
        # thresholded pass and merge — raw wins on black dashboards (thin
        # digits like "1"), isolated wins on bright colorful wallpapers.
        passes = [image]
        if self._isolate_light_text:
            passes.append(
                isolate_light_text(
                    image, luminance=self._isolate_light_text_luminance
                )
            )
        # Oversized glyphs (~150px) often fail at native scale;
        # a half-scale pass recovers digital speed readouts in tight crops.
        width, height = image.size
        if min(width, height) >= 120 and max(width, height) >= 200:
            passes.append(
                image.resize(
                    (max(1, width // 2), max(1, height // 2)),
                    resample=Resampling.LANCZOS,
                )
            )

        words: list[OCRWord] = []
        seen: set[str] = set()
        texts: list[str] = []
        for frame in passes:
            frame_words = self._extract_words(frame)
            frame_text = " ".join(w.text for w in frame_words)
            if frame_text:
                texts.append(frame_text)
            for word in frame_words:
                key = word.text.lower()
                if key in seen:
                    continue
                seen.add(key)
                words.append(word)

        return OCRResult(text=" ".join(texts), words=words)
