"""OpenCV-based image verifiers.

Performance notes:
- Reference images are cached by :class:`AssetStore`.
- When a region is given, the screenshot is cropped *before* matching.
- One observation can feed all of these verifiers without re-capturing.
- Multiscale search tries scale 1.0 first (and an auto-fit scale when the
  native template cannot fit the search area) and stops once confidence
  meets the threshold (remaining scales only run when needed).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from argus.config.models import ImageVerificationConfig
from argus.exceptions import VerificationError
from argus.models.common import Region
from argus.models.observation import Observation
from argus.models.results import VerificationResult
from argus.verifiers.assets import AssetStore
from argus.verifiers.base import Expectation, Verifier

_MATCH_METHODS = {
    "ccoeff_normed": cv2.TM_CCOEFF_NORMED,
    "ccorr_normed": cv2.TM_CCORR_NORMED,
    "sqdiff_normed": cv2.TM_SQDIFF_NORMED,
}

# OpenCV only accepts a mask with these methods.
_MASK_COMPATIBLE_METHODS = {cv2.TM_CCORR_NORMED, cv2.TM_SQDIFF_NORMED}
_DEFAULT_MASK_LUMINANCE = 30
# Tiny templates match noise with high confidence (e.g. 4×4 → false "present").
_MIN_TEMPLATE_SIDE = 16


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


def _background_mask(template: np.ndarray, luminance: int) -> np.ndarray:
    """Build an 8-bit mask that keeps non-dark template pixels (the icon)."""
    if template.ndim == 2:
        gray = template
    else:
        gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    return ((gray > luminance).astype(np.uint8)) * 255


def _confidence_from_result(
    result: np.ndarray, method: int
) -> tuple[float, tuple[int, int]]:
    """Return (confidence, loc); treat non-finite / out-of-range scores as 0.

    Masked ``TM_CCORR_NORMED`` on empty (e.g. all-black) regions can yield
    ``+inf``; ``nan_to_num`` would turn that into a huge finite float unless
    ``posinf`` is set explicitly.
    """
    if method == cv2.TM_SQDIFF_NORMED:
        min_val, _, min_loc, _ = cv2.minMaxLoc(
            np.nan_to_num(result, nan=1.0, posinf=1.0, neginf=1.0)
        )
        confidence = 1.0 - float(min_val)
        loc = min_loc
    else:
        _, max_val, _, max_loc = cv2.minMaxLoc(
            np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
        )
        confidence = float(max_val)
        loc = max_loc
    if not np.isfinite(confidence):
        confidence = 0.0
    else:
        # Clamp to [0, 1]; posinf is already mapped to 0 above so FLT_MAX
        # overflow cannot sneak through as a "perfect" match.
        confidence = float(np.clip(confidence, 0.0, 1.0))
    return confidence, loc


#: Below this std-dev (0–255 scale) a template is "flat" under its mask.
_FLAT_TEMPLATE_STD = 1.0


def _masked_ccoeff_at(
    haystack: np.ndarray,
    template: np.ndarray,
    mask: np.ndarray,
    loc: tuple[int, int],
) -> float:
    """Mean-subtracted normalized correlation of ``template`` with the haystack
    patch at ``loc``, over ``mask > 0`` pixels. Returns a value in ``[-1, 1]``.

    Masked ``TM_CCORR_NORMED`` is a cosine between two all-positive vectors, so
    a bright icon *shape* over flat dim chrome scores ~0.99 even with no icon
    there (TT-DOOR_FL-002). Subtracting the mean first makes a flat patch score
    ~0 while a real icon stays ~1. A crisp one-colour glyph has no variance
    under its own mask; for those the mask is grown by one pixel so the dark
    rim gives the comparison structure. Degenerate inputs (patch out of bounds,
    fewer than four pixels, zero variance) return 0.0.
    """
    x, y = loc
    th, tw = template.shape[:2]
    patch = haystack[y : y + th, x : x + tw]
    if patch.shape[:2] != (th, tw):
        return 0.0

    def _select(m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        keep = m > 0
        return (
            template[keep].astype(np.float64).reshape(-1),
            patch[keep].astype(np.float64).reshape(-1),
        )

    t, h = _select(mask)
    if t.size < 4:
        return 0.0
    if t.std() < _FLAT_TEMPLATE_STD:
        t, h = _select(cv2.dilate(mask, np.ones((3, 3), np.uint8)))
    t = t - t.mean()
    h = h - h.mean()
    denom = math.sqrt(float(np.dot(t, t)) * float(np.dot(h, h)))
    if not np.isfinite(denom) or denom < 1e-9:
        return 0.0
    return float(np.clip(np.dot(t, h) / denom, -1.0, 1.0))


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
        self,
        observation: Observation,
        expectation: Expectation,
        *,
        allow_auto_shrink: bool = True,
    ) -> tuple[float, Region | None, Region | None]:
        """Template-match the expected image; returns (confidence, location, region_used).

        ``allow_auto_shrink`` adds shrink steps down to 16px so a Figma master
        can match a small on-screen icon. Leave it off for ``image_not_present``:
        a 140px Park ``P`` scaled to 28px false-matches speedo chrome.
        """
        if not expectation.image:
            raise VerificationError(
                f"Verifier {self.name!r} requires an 'image' parameter."
            )
        threshold, grayscale, method = self._settings(expectation)
        early_exit_at = threshold

        haystack = observation_to_array(observation, grayscale=grayscale)
        region = _as_region(expectation.region)
        offset_x = offset_y = 0
        if region is not None:
            haystack = crop(haystack, region)
            offset_x, offset_y = region.x, region.y

        template = self._assets.load_array(expectation.image, grayscale=grayscale)
        th, tw = template.shape[:2]
        hh, hw = haystack.shape[:2]
        scale_tolerance = (
            expectation.scale_tolerance
            if expectation.scale_tolerance is not None
            else self._config.scale_tolerance
        )
        native_fits = th <= hh and tw <= hw
        fit_scale = min(hw / tw, hh / th) if tw > 0 and th > 0 else 0.0
        min_fit = (
            _MIN_TEMPLATE_SIDE / min(th, tw) if min(th, tw) > 0 else 1.0
        )
        # Hard-fail only when even a 16px template cannot fit the search area.
        # Native size that does not fit is downscaled automatically — tests do
        # not need scale_tolerance just because a golden is larger than a region.
        if hh < _MIN_TEMPLATE_SIDE or hw < _MIN_TEMPLATE_SIDE or (
            not native_fits and fit_scale < min_fit
        ):
            raise VerificationError(
                f"Reference image {expectation.image!r} ({tw}x{th}) is larger than "
                f"the search area ({hw}x{hh}). Check the region or image scale.",
                remediation="Enlarge the region, shrink the reference, or raise scale_tolerance.",
            )

        mask: np.ndarray | None = None
        if expectation.mask_background:
            luminance = (
                expectation.mask_luminance
                if expectation.mask_luminance is not None
                else _DEFAULT_MASK_LUMINANCE
            )
            mask = _background_mask(template, luminance)
            if int(np.count_nonzero(mask)) == 0:
                raise VerificationError(
                    f"mask_background produced an empty mask for {expectation.image!r}.",
                    remediation=(
                        "Lower mask_luminance or use a reference with visible icon pixels."
                    ),
                )
            # TM_CCOEFF_NORMED does not accept masks — prefer CCORR for neon icons.
            if method not in _MASK_COMPATIBLE_METHODS:
                method = cv2.TM_CCORR_NORMED

        # Prefer native scale first (usual happy path). If it cannot fit the
        # haystack, try the largest scale that does. Then other scales nearest
        # to 1.0 so slight DPI drift exits early without a full sweep.
        scales = [1.0]
        if not native_fits and fit_scale > 0:
            rounded_fit = round(fit_scale, 4)
            if abs(rounded_fit - 1.0) > 1e-4:
                scales.append(rounded_fit)
        min_lo = _MIN_TEMPLATE_SIDE / min(th, tw) if min(th, tw) > 0 else 1.0
        if scale_tolerance > 0:
            # Sample between (1-tol) and (1+tol). Three endpoints alone miss
            # mid values (e.g. tol 0.5 needs ~0.55 for Config F telltale icons).
            # Floor lo so a large tol (legacy YAML uses 1.2) cannot shrink the
            # template into noise-sized matches.
            lo = max(min_lo, 1.0 - scale_tolerance)
            hi = 1.0 + scale_tolerance
            step = 0.05
            n = int(round((hi - lo) / step)) + 1
            n = max(3, min(n, 41))
            sampled = [lo + i * (hi - lo) / (n - 1) for i in range(n)]
            others = [
                s
                for s in sampled
                if abs(s - 1.0) > 1e-9
                and all(abs(s - existing) > 1e-4 for existing in scales)
            ]
            others.sort(key=lambda s: abs(s - 1.0))
            scales.extend(others)
        # Goldens are often the Figma master (e.g. 96×112) while the app
        # draws a ~22px instance. image_present offers shrink steps down to
        # 16px; native hits still early-exit after scale 1.0.
        # image_not_present must not: a large glyph shrunk to ~28px matches
        # unrelated chrome (Park ``P`` vs a digital ``0``).
        if allow_auto_shrink and min_lo < 0.99:
            shrink: list[float] = []
            step = 0.1
            s = 1.0 - step
            while s >= min_lo - 1e-9:
                shrink.append(round(s, 3))
                s -= step
            shrink.append(round(min_lo, 4))
            shrink.sort(key=lambda v: abs(v - 1.0))
            for s in shrink:
                if s > 0 and all(abs(s - existing) > 0.04 for existing in scales):
                    scales.append(s)

        best_confidence = -1.0
        best_location: Region | None = None
        for scale in scales:
            if scale <= 0:
                continue
            scaled = template
            scaled_mask = mask
            if scale != 1.0:
                new_w = max(1, int(tw * scale))
                new_h = max(1, int(th * scale))
                if new_h > hh or new_w > hw:
                    continue
                scaled = cv2.resize(template, (new_w, new_h))
                if mask is not None:
                    scaled_mask = cv2.resize(
                        mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST
                    )
            sh, sw = scaled.shape[:2]
            if sh > hh or sw > hw:
                continue
            if sh < _MIN_TEMPLATE_SIDE or sw < _MIN_TEMPLATE_SIDE:
                continue
            if scaled_mask is not None:
                result = cv2.matchTemplate(haystack, scaled, method, mask=scaled_mask)
            else:
                result = cv2.matchTemplate(haystack, scaled, method)
            confidence, loc = _confidence_from_result(result, method)
            if scaled_mask is not None:
                # CCORR only locates the peak; re-score it mean-subtracted so an
                # icon-shaped patch of empty chrome cannot pass as the icon.
                confidence = max(
                    0.0, _masked_ccoeff_at(haystack, scaled, scaled_mask, loc)
                )
            if confidence > best_confidence:
                best_confidence = confidence
                best_location = Region(
                    x=loc[0] + offset_x, y=loc[1] + offset_y, width=sw, height=sh
                )
            # Good enough: skip remaining scales (present passes; absent fails fast).
            if best_confidence >= early_exit_at:
                break

        if best_location is None:
            raise VerificationError(
                f"No usable template scale for {expectation.image!r} "
                f"in search area ({hw}x{hh}).",
                remediation="Check scale_tolerance, region size, and reference image.",
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
                "mask_background": expectation.mask_background,
            },
        )


class ImageAbsentVerifier(_ImageVerifierBase):
    """Passes when a known image is NOT found in the screenshot."""

    name = "image_not_present"

    def verify(self, observation: Observation, expectation: Expectation) -> VerificationResult:
        threshold, _, _ = self._settings(expectation)
        confidence, location, region = self._find_template(
            observation, expectation, allow_auto_shrink=False
        )
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
                "mask_background": expectation.mask_background,
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
