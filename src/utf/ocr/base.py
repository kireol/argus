"""OCR provider abstraction.

OCR is strictly optional: tests that don't use text conditions never touch
this module, and the framework imports OCR backends lazily.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PIL.Image import Image
from pydantic import BaseModel, Field

from utf.config.models import OCRConfig
from utf.exceptions import ConfigurationError
from utf.models.common import Region


class OCRWord(BaseModel):
    text: str
    confidence: float | None = None
    region: Region | None = None


class OCRResult(BaseModel):
    text: str
    words: list[OCRWord] = Field(default_factory=list)

    def contains(self, needle: str, *, case_sensitive: bool = False) -> bool:
        haystack = self.text if case_sensitive else self.text.lower()
        target = needle if case_sensitive else needle.lower()
        return target in haystack


class OCRProvider(ABC):
    """Extracts text from images."""

    name: str = "ocr"

    @abstractmethod
    def extract_text(self, image: Image) -> OCRResult:
        ...

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """Return (available, human-readable reason when unavailable)."""


def create_ocr_provider(config: OCRConfig) -> OCRProvider:
    """Instantiate the configured OCR provider (lazy imports keep OCR optional)."""
    if config.provider == "tesseract":
        from utf.ocr.tesseract import TesseractProvider

        return TesseractProvider(language=config.language)
    if config.provider == "fake":
        from utf.ocr.fake import FakeOCRProvider

        return FakeOCRProvider()
    raise ConfigurationError(
        f"Unknown OCR provider {config.provider!r}.",
        remediation="Available providers: tesseract, fake.",
    )
