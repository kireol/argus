"""OpenCV-based image verifiers.

Performance notes:
- Reference images are cached by :class:`AssetStore`.
- When a region is given, the screenshot is cropped *before* matching.
- One observation can feed all of these verifiers without re-capturing.
"""

from __future__ import annotations

import cv2
import numpy as np

from utf.config.models import ImageVerificationConfig
from utf.exceptions import VerificationError
from utf.models.common import Region
from utf.models.observation import Observation
from utf.models.results import VerificationResult
from utf.verifiers.assets import AssetStore
from utf.verifiers.base import Expectation, Verifier

_MATCH_METHODS = {
    "ccoeff_normed": cv2.TM_CCOEFF_NORMED,
    "ccorr_normed": cv2.TM_CCORR_NORMED,
    "sqdiff_normed": cv2.TM_SQDIFF_NORMED,
}


def observation_to_array(observation: Observation, *, grayscale: bool) -> np.ndarray:
    """Convert an observation's PIL image to an OpenCV array."""
    img = observation.image.convert("L" if grayscale else "RGB")
    array = np.asarray(img)
    if not grayscale:
        array = array[:, :, ::-1].copy()
    return array


def crop(array: np.ndarray, region: Region) -> np.ndarray:
    h, w = array.shape[:2]
    if region.x >= w or region.y >= h:
        raise VerificationError(
            f"Region {region.as_tuple()} lies outside the {w}x{h} screenshot."
        )
    return array[
        region.y : min(region.bottom, h),
        region.x : min(region.right, w),
    ]


class _ImageVerifierBase(Verifier):
    def __init__(self, assets: AssetStore, config: ImageVerificationConfig) -> None:
        self._assets = assets
        self._config = config

    def _settings(self, expectation: Expectation) -> tuple[float, bool, int]:
        threshold = (
            expectation.threshold
            if expectation.threshold is not None
            else self._config.default_threshold
        )
        grayscale = (
            expectation.grayscale
            if expectation.grayscale is not None
            else self._config.grayscale
        )
        method = _MATCH_METHODS.get(self._config.match_method)
        if method is None:
            raise VerificationError(
                f"Unknown match method {self._config.match_method!r}. "
                f"Available: {', '.join(_MATCH_METHODS)}."
            )
        return threshold, grayscale, method

    def _find_template(
        self, observation: Observation, expectation: Expectation
    ) -> tuple[float, Region | None, Region | None]:
        """Template-match the expected image; returns (confidence, location, region_used)."""
        if not expectation.image:
            raise VerificationError(
                f"Verifier {self.name!r} requires an 'image' parameter."
            )
        threshold, grayscale, method = self._settings(expectation)
        del threshold  # decided by callers

        haystack = observation_to_array(observation, grayscale=grayscale)
        region = _as_region(expectation.region)
        offset_x = offset_y = 0
        if region is not None:
            haystack = crop(haystack, region)
            offset_x, offset_y = region.x, region.y

        template = self._assets.load_array(expectation.image, grayscale=grayscale)
        th, tw = template.shape[:2]
        hh, hw = haystack.shape[:2]
        if th > hh or tw > hw:
            raise VerificationError(
                f"Reference image {expectation.image!r} ({tw}x{th}) is larger than "
                f"the search area ({hw}x{hh}). Check the region or image scale."
            )

        scale_tolerance = (
            expectation.scale_tolerance
            if expectation.scale_tolerance is not None
            else self._config.scale_tolerance
        )
        scales = [1.0]
        if scale_tolerance > 0:
            scales = [1.0 - scale_tolerance, 1.0, 1.0 + scale_tolerance]

        best_confidence = -1.0
        best_location: Region | None = None
        for scale in scales:
            scaled = template
            if scale != 1.0:
                new_w = max(1, int(tw * scale))
                new_h = max(1, int(th * scale))
                if new_h > hh or new_w > hw:
                    continue
                scaled = cv2.resize(template, (new_w, new_h))
            result = cv2.matchTemplate(haystack, scaled, method)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            if method == cv2.TM_SQDIFF_NORMED:
                confidence, loc = 1.0 - min_val, min_loc
            else:
                confidence, loc = max_val, max_loc
            if confidence > best_confidence:
                sh, sw = scaled.shape[:2]
                best_confidence = confidence
                best_location = Region(
                    x=loc[0] + offset_x, y=loc[1] + offset_y, width=sw, height=sh
                )

        return best_confidence, best_location, region


class ImagePresentVerifier(_ImageVerifierBase):
    """Passes when a known image is found in the screenshot."""

    name = "image_present"

    def verify(self, observation: Observation, expectation: Expectation) -> VerificationResult:
        threshold, _, _ = self._settings(expectation)
        confidence, location, region = self._find_template(observation, expectation)
        passed = confidence >= threshold
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            confidence=round(confidence, 4),
            location=location if passed else None,
            message=(
                f"Image {expectation.image!r} "
                + ("found" if passed else "not found")
                + f" (confidence {confidence:.3f}, threshold {threshold:.2f})"
            ),
            details={
                "image": expectation.image,
                "threshold": threshold,
                "region": region.as_tuple() if region else None,
                "best_match": location.model_dump() if location else None,
            },
        )


class ImageAbsentVerifier(_ImageVerifierBase):
    """Passes when a known image is NOT found in the screenshot."""

    name = "image_not_present"

    def verify(self, observation: Observation, expectation: Expectation) -> VerificationResult:
        threshold, _, _ = self._settings(expectation)
        confidence, location, region = self._find_template(observation, expectation)
        passed = confidence < threshold
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            confidence=round(confidence, 4),
            location=None if passed else location,
            message=(
                f"Image {expectation.image!r} "
                + ("absent as expected" if passed else "unexpectedly present")
                + f" (confidence {confidence:.3f}, threshold {threshold:.2f})"
            ),
            details={
                "image": expectation.image,
                "threshold": threshold,
                "region": region.as_tuple() if region else None,
            },
        )


class ScreenshotMatchVerifier(_ImageVerifierBase):
    """Compares the screenshot (or a region of it) against a reference image.

    Uses mean absolute difference rather than pixel-perfect equality; the
    tolerance is expressed through ``threshold`` (1.0 = identical).
    """

    name = "screenshot_matches"

    def verify(self, observation: Observation, expectation: Expectation) -> VerificationResult:
        if not expectation.image:
            raise VerificationError("screenshot_matches requires an 'image' parameter.")
        threshold, grayscale, _ = self._settings(expectation)

        actual = observation_to_array(observation, grayscale=grayscale)
        region = _as_region(expectation.region)
        if region is not None:
            actual = crop(actual, region)

        reference = self._assets.load_array(expectation.image, grayscale=grayscale)
        if reference.shape[:2] != actual.shape[:2]:
            reference = cv2.resize(reference, (actual.shape[1], actual.shape[0]))

        diff = cv2.absdiff(actual, reference)
        similarity = 1.0 - float(np.mean(diff)) / 255.0
        passed = similarity >= threshold
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            confidence=round(similarity, 4),
            message=(
                f"Screenshot similarity {similarity:.3f} "
                + (">=" if passed else "<")
                + f" threshold {threshold:.2f}"
            ),
            details={
                "image": expectation.image,
                "threshold": threshold,
                "region": region.as_tuple() if region else None,
            },
        )


def _as_region(region: Region | str | None) -> Region | None:
    """Named regions are resolved earlier (by the condition layer)."""
    if region is None or isinstance(region, Region):
        return region
    raise VerificationError(
        f"Unresolved named region {region!r} reached the verifier.",
    )


def diff_image(observation: Observation, reference: np.ndarray) -> np.ndarray:
    """Produce a visual diff (for failure artifacts)."""
    actual = observation_to_array(observation, grayscale=False)
    if reference.ndim == 2:
        reference = cv2.cvtColor(reference, cv2.COLOR_GRAY2BGR)
    if reference.shape[:2] != actual.shape[:2]:
        reference = cv2.resize(reference, (actual.shape[1], actual.shape[0]))
    return cv2.absdiff(actual, reference)
