"""Unit tests for OCR text verifiers."""

from __future__ import annotations

from PIL import Image

from argus.models.common import Region
from argus.models.observation import Observation
from argus.ocr.base import OCRProvider, OCRResult
from argus.verifiers.base import Expectation
from argus.verifiers.text import TextAbsentVerifier, TextPresentVerifier


class _FixedOCR(OCRProvider):
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self, image: Image.Image) -> OCRResult:  # noqa: ARG002
        return OCRResult(text=self._text)


def _observation() -> Observation:
    return Observation(image=Image.new("RGB", (10, 10)), timestamp=0.0)


def test_text_present_ignores_trailing_punctuation():
    verifier = TextPresentVerifier(_FixedOCR("Charge Vehicle Now"))
    result = verifier.verify(
        _observation(),
        Expectation(text="Charge Vehicle Now!", region=Region(x=0, y=0, width=1, height=1)),
    )
    assert result.passed


def test_text_present_ignores_mid_string_punctuation():
    verifier = TextPresentVerifier(_FixedOCR("Automatic Shutdow! Off"))
    result = verifier.verify(
        _observation(),
        Expectation(text="Automatic Shutdow Off", region=Region(x=0, y=0, width=1, height=1)),
    )
    assert result.passed


def test_text_not_present_ignores_trailing_punctuation():
    verifier = TextAbsentVerifier(_FixedOCR("Charge Vehicle Now"))
    result = verifier.verify(
        _observation(),
        Expectation(text="Charge Vehicle Now!", region=Region(x=0, y=0, width=1, height=1)),
    )
    assert not result.passed


def test_text_present_matches_numeric_needle_with_ocr_digit_confusions():
    verifier = TextPresentVerifier(_FixedOCR("G6O- GO GO |"))
    result = verifier.verify(
        _observation(),
        Expectation(text="60", region=Region(x=0, y=0, width=1, height=1)),
    )
    assert result.passed


def test_text_not_present_still_detects_numeric_needle_after_digit_normalization():
    verifier = TextAbsentVerifier(_FixedOCR("G6O- GO GO |"))
    result = verifier.verify(
        _observation(),
        Expectation(text="97", region=Region(x=0, y=0, width=1, height=1)),
    )
    assert result.passed
