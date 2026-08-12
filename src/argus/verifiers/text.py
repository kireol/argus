"""OCR-backed text verifiers."""

from __future__ import annotations

from PIL import Image as PILImage

from argus.exceptions import VerificationError
from argus.models.common import Region
from argus.models.observation import Observation
from argus.models.results import VerificationResult
from argus.ocr.base import OCRProvider
from argus.verifiers.base import Expectation, Verifier


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
        haystack = extracted if expectation.case_sensitive else extracted.lower()
        target = needle if expectation.case_sensitive else needle.lower()
        passed = target in haystack
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
        haystack = extracted if expectation.case_sensitive else extracted.lower()
        target = needle if expectation.case_sensitive else needle.lower()
        passed = target not in haystack
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            message=(
                f"Text {needle!r} "
                + ("absent as expected" if passed else "unexpectedly present")
            ),
            details={"expected_absent": needle, "extracted_text": extracted[:1000]},
        )
