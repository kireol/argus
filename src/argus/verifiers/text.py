"""OCR-backed text verifiers."""

from __future__ import annotations

import re

from PIL import Image as PILImage

from argus.exceptions import VerificationError
from argus.models.common import Region
from argus.models.observation import Observation
from argus.models.results import VerificationResult
from argus.ocr.base import OCRProvider
from argus.verifiers.base import Expectation, Verifier

_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")
_OCR_DIGIT_CONFUSIONS = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "Z": "2",
        "z": "2",
        "S": "5",
        "s": "5",
        "G": "6",
        "g": "6",
        "B": "8",
        "b": "8",
    }
)


def _normalize_text_match(text: str, *, case_sensitive: bool) -> str:
    """Collapse OCR-noisy punctuation so substring checks tolerate !/: , omissions."""
    normalized = _NON_ALNUM_RE.sub(" ", text.strip())
    normalized = " ".join(normalized.split())
    return normalized if case_sensitive else normalized.lower()


def _is_numeric_needle(text: str) -> bool:
    return bool(text) and text.isdigit()


def _haystacks_for_match(extracted: str, *, case_sensitive: bool) -> list[str]:
    """Normalized haystacks for substring matching (plain + OCR digit fixes)."""
    plain = _normalize_text_match(extracted, case_sensitive=case_sensitive)
    ocr_digits = _normalize_text_match(
        extracted.translate(_OCR_DIGIT_CONFUSIONS),
        case_sensitive=case_sensitive,
    )
    if ocr_digits == plain:
        return [plain]
    return [plain, ocr_digits]


class _TextVerifierBase(Verifier):
    def __init__(self, ocr: OCRProvider) -> None:
        self._ocr = ocr

    def _extract(self, observation: Observation, expectation: Expectation) -> str:
        if not expectation.text:
            raise VerificationError(f"Verifier {self.name!r} requires a 'text' parameter.")
        image = observation.image
        region = expectation.region
        if isinstance(region, str):
            raise VerificationError(f"Unresolved named region {region!r} reached the verifier.")
        if isinstance(region, Region):
            image = image.crop((region.x, region.y, region.right, region.bottom))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        assert isinstance(image, PILImage.Image)
        return self._ocr.extract_text(image).text


class TextPresentVerifier(_TextVerifierBase):
    name = "text_present"

    def verify(self, observation: Observation, expectation: Expectation) -> VerificationResult:
        extracted = self._extract(observation, expectation)
        needle = expectation.text or ""
        target = _normalize_text_match(needle, case_sensitive=expectation.case_sensitive)
        haystacks = _haystacks_for_match(extracted, case_sensitive=expectation.case_sensitive)
        if _is_numeric_needle(needle):
            passed = any(target in haystack for haystack in haystacks)
        else:
            passed = target in haystacks[0]
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            message=(
                f"Text {needle!r} " + ("found" if passed else "not found") + " on screen"
            ),
            details={"expected_text": needle, "extracted_text": extracted[:1000]},
        )


class TextAbsentVerifier(_TextVerifierBase):
    name = "text_not_present"

    def verify(self, observation: Observation, expectation: Expectation) -> VerificationResult:
        extracted = self._extract(observation, expectation)
        needle = expectation.text or ""
        target = _normalize_text_match(needle, case_sensitive=expectation.case_sensitive)
        haystacks = _haystacks_for_match(extracted, case_sensitive=expectation.case_sensitive)
        if _is_numeric_needle(needle):
            passed = all(target not in haystack for haystack in haystacks)
        else:
            passed = target not in haystacks[0]
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            message=(
                f"Text {needle!r} "
                + ("absent as expected" if passed else "unexpectedly present")
            ),
            details={"expected_absent": needle, "extracted_text": extracted[:1000]},
        )
