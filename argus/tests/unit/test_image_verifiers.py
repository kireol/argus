"""OpenCV image verifier behavior."""

import pytest
from tests.conftest import make_screen

from argus.config.models import ImageVerificationConfig
from argus.exceptions import AssetError, VerificationError
from argus.models.common import Region
from argus.models.observation import Observation
from argus.verifiers.assets import AssetStore
from argus.verifiers.base import Expectation
from argus.verifiers.image import (
    ImageAbsentVerifier,
    ImagePresentVerifier,
    ScreenshotMatchVerifier,
)


@pytest.fixture
def assets(asset_dir):
    return AssetStore([asset_dir])


@pytest.fixture
def image_config():
    return ImageVerificationConfig(default_threshold=0.90)


def observe(image):
    return Observation(image=image, device="test")


class TestImagePresent:
    def test_finds_image(self, assets, image_config, artwork_a):
        verifier = ImagePresentVerifier(assets, image_config)
        screen = make_screen(artwork_a, position=(200, 100))
        result = verifier.verify(observe(screen), Expectation(image="movie_123.png"))
        assert result.passed
        assert result.confidence >= 0.99
        assert result.location is not None
        assert abs(result.location.x - 200) <= 2
        assert abs(result.location.y - 100) <= 2

    def test_missing_image_fails(self, assets, image_config, artwork_b):
        verifier = ImagePresentVerifier(assets, image_config)
        screen = make_screen(artwork_b)  # contains movie_456, not movie_123
        result = verifier.verify(observe(screen), Expectation(image="movie_123.png"))
        assert not result.passed
        assert result.location is None

    def test_region_restricts_search(self, assets, image_config, artwork_a):
        verifier = ImagePresentVerifier(assets, image_config)
        screen = make_screen(artwork_a, position=(400, 200))
        inside = Region(x=380, y=180, width=150, height=150)
        outside = Region(x=0, y=0, width=150, height=150)
        assert verifier.verify(
            observe(screen), Expectation(image="movie_123.png", region=inside)
        ).passed
        result = verifier.verify(
            observe(screen), Expectation(image="movie_123.png", region=outside)
        )
        assert not result.passed

    def test_region_offsets_location(self, assets, image_config, artwork_a):
        verifier = ImagePresentVerifier(assets, image_config)
        screen = make_screen(artwork_a, position=(400, 200))
        region = Region(x=350, y=150, width=250, height=200)
        result = verifier.verify(
            observe(screen), Expectation(image="movie_123.png", region=region)
        )
        assert result.passed
        assert abs(result.location.x - 400) <= 2  # absolute, not region-relative
        assert abs(result.location.y - 200) <= 2

    def test_scale_tolerance_samples_intermediate_scales(
        self, image_config, tmp_path
    ):
        """tol=0.5 must try ~0.55, not only the endpoints 0.5 / 1.0 / 1.5."""
        from PIL import Image, ImageDraw

        ref = Image.new("RGB", (100, 100), (0, 0, 0))
        draw = ImageDraw.Draw(ref)
        draw.ellipse((10, 10, 90, 90), fill=(220, 40, 40))
        ref.save(tmp_path / "icon.png")

        # On-screen icon is ~55% of the reference size.
        screen = Image.new("RGB", (300, 200), (0, 0, 0))
        small = ref.resize((55, 55))
        screen.paste(small, (40, 40))

        store = AssetStore([tmp_path])
        verifier = ImagePresentVerifier(store, image_config)
        result = verifier.verify(
            observe(screen),
            Expectation(image="icon.png", threshold=0.80, scale_tolerance=0.5),
        )
        assert result.passed
        assert result.confidence is not None and result.confidence >= 0.80

    def test_multiscale_early_exits_when_native_scale_matches(
        self, assets, image_config, artwork_a, monkeypatch
    ):
        """When scale 1.0 already meets the threshold, do not sweep other scales."""
        import argus.verifiers.image as image_mod

        calls: list[tuple[int, int]] = []
        real_match = image_mod.cv2.matchTemplate

        def counting_match(haystack, templ, method, mask=None):
            calls.append(templ.shape[:2])
            if mask is None:
                return real_match(haystack, templ, method)
            return real_match(haystack, templ, method, mask=mask)

        monkeypatch.setattr(image_mod.cv2, "matchTemplate", counting_match)
        verifier = ImagePresentVerifier(assets, image_config)
        screen = make_screen(artwork_a, position=(200, 100))
        result = verifier.verify(
            observe(screen),
            Expectation(image="movie_123.png", threshold=0.90, scale_tolerance=0.5),
        )
        assert result.passed
        assert len(calls) == 1

    def test_tiny_scaled_templates_do_not_false_positive(
        self, image_config, tmp_path
    ):
        """Large scale_tolerance must not shrink templates into noise matches."""
        from PIL import Image, ImageDraw

        ref = Image.new("RGB", (96, 96), (0, 0, 0))
        draw = ImageDraw.Draw(ref)
        draw.rectangle((20, 20, 76, 76), fill=(220, 40, 40))
        ref.save(tmp_path / "battery.png")

        # Screen has a small bright speck that a 4×4 template would match.
        screen = Image.new("RGB", (400, 200), (0, 0, 0))
        draw = ImageDraw.Draw(screen)
        draw.rectangle((200, 100, 204, 104), fill=(255, 255, 255))

        store = AssetStore([tmp_path])
        present = ImagePresentVerifier(store, image_config)
        absent = ImageAbsentVerifier(store, image_config)
        exp = Expectation(image="battery.png", threshold=0.70, scale_tolerance=1.2)
        assert not present.verify(observe(screen), exp).passed
        assert absent.verify(observe(screen), exp).passed

    def test_oversized_reference_fits_by_downscaling(self, image_config, tmp_path):
        """A golden larger than the region is shrunk to fit (no scale_tolerance)."""
        from PIL import Image, ImageDraw

        ref = Image.new("RGB", (96, 112), (0, 0, 0))
        draw = ImageDraw.Draw(ref)
        draw.polygon([(10, 56), (86, 10), (86, 102)], fill=(40, 220, 80))
        ref.save(tmp_path / "left_turn_signal_on.png")

        fit = min(80 / 96, 80 / 112)
        on_screen = ref.resize((int(96 * fit), int(112 * fit)))
        screen = Image.new("RGB", (200, 200), (0, 0, 0))
        screen.paste(on_screen, (10, 10))

        store = AssetStore([tmp_path])
        verifier = ImagePresentVerifier(store, image_config)
        result = verifier.verify(
            observe(screen),
            Expectation(
                image="left_turn_signal_on.png",
                region=Region(x=10, y=10, width=80, height=80),
                threshold=0.80,
            ),
        )
        assert result.passed
        assert result.confidence is not None and result.confidence >= 0.80

    def test_small_on_screen_icon_matches_large_golden(self, image_config, tmp_path):
        """96×112 golden vs ~22px on-screen instance — no scale_tolerance needed."""
        from PIL import Image, ImageDraw

        ref = Image.new("RGB", (96, 112), (0, 0, 0))
        draw = ImageDraw.Draw(ref)
        draw.polygon([(10, 56), (86, 10), (86, 102)], fill=(40, 220, 80))
        ref.save(tmp_path / "left_turn_signal_on.png")

        on_screen = ref.resize((20, 23))
        screen = Image.new("RGB", (200, 200), (0, 0, 0))
        screen.paste(on_screen, (5, 5))

        store = AssetStore([tmp_path])
        present = ImagePresentVerifier(store, image_config)
        absent = ImageAbsentVerifier(store, image_config)
        exp = Expectation(
            image="left_turn_signal_on.png",
            region=Region(x=0, y=0, width=100, height=120),
            threshold=0.80,
            mask_background=True,
        )
        result = present.verify(observe(screen), exp)
        assert result.passed, result.message
        empty = Image.new("RGB", (200, 200), (0, 0, 0))
        assert not present.verify(observe(empty), exp).passed
        assert absent.verify(observe(empty), exp).passed

    def test_absent_does_not_auto_shrink_large_glyph(self, image_config, tmp_path):
        """A native-size Park P must not match a small '0' via 16px shrink."""
        from PIL import Image, ImageDraw, ImageFont

        ref = Image.new("RGB", (140, 150), (0, 0, 0))
        draw = ImageDraw.Draw(ref)
        try:
            font = ImageFont.truetype("Arial.ttf", 120)
        except OSError:
            font = ImageFont.load_default()
        draw.text((20, 5), "P", fill=(255, 255, 255), font=font)
        ref.save(tmp_path / "prndl_p_active.png")

        screen = Image.new("RGB", (400, 300), (0, 0, 0))
        draw = ImageDraw.Draw(screen)
        draw.ellipse((80, 40, 160, 160), outline=(255, 255, 255), width=8)

        store = AssetStore([tmp_path])
        absent = ImageAbsentVerifier(store, image_config)
        exp = Expectation(
            image="prndl_p_active.png",
            region=Region(x=40, y=20, width=202, height=266),
            threshold=0.90,
            mask_background=True,
        )
        result = absent.verify(observe(screen), exp)
        assert result.passed, result.message

    def test_template_larger_than_screen_errors(self, assets, image_config, artwork_a):
        verifier = ImagePresentVerifier(assets, image_config)
        from PIL import Image

        # Smaller than the 16px minimum template — cannot downscale to fit.
        tiny = Image.new("RGB", (10, 10))
        with pytest.raises(VerificationError, match="larger"):
            verifier.verify(observe(tiny), Expectation(image="movie_123.png"))

    def test_missing_asset_raises(self, assets, image_config, artwork_a):
        verifier = ImagePresentVerifier(assets, image_config)
        with pytest.raises(AssetError, match="not found"):
            verifier.verify(
                observe(make_screen(artwork_a)), Expectation(image="nope.png")
            )

    def test_requires_image_param(self, assets, image_config, artwork_a):
        verifier = ImagePresentVerifier(assets, image_config)
        with pytest.raises(VerificationError, match="image"):
            verifier.verify(observe(make_screen(artwork_a)), Expectation())

    def test_grayscale_mode(self, assets, image_config, artwork_a):
        verifier = ImagePresentVerifier(assets, image_config)
        screen = make_screen(artwork_a)
        result = verifier.verify(
            observe(screen), Expectation(image="movie_123.png", grayscale=True)
        )
        assert result.passed

    def test_mask_background_ignores_dark_reference_pixels(
        self, image_config, tmp_path
    ):
        """Icon crops on black still match when the live background changes."""
        from PIL import Image, ImageDraw

        ref = Image.new("RGB", (40, 40), (0, 0, 0))
        draw = ImageDraw.Draw(ref)
        draw.polygon([(5, 20), (30, 5), (30, 35)], fill=(0, 220, 80))
        ref.save(tmp_path / "icon_on_black.png")

        # Same chevron on a noisy background (no black plate behind it).
        screen = Image.new("RGB", (200, 120), (0, 0, 0))
        px = screen.load()
        for y in range(120):
            for x in range(200):
                px[x, y] = ((x * 3) % 255, (y * 5) % 255, (x + y) % 255)
        draw = ImageDraw.Draw(screen)
        draw.polygon([(25, 50), (50, 35), (50, 65)], fill=(0, 220, 80))

        store = AssetStore([tmp_path])
        verifier = ImagePresentVerifier(store, image_config)

        plain = verifier.verify(
            observe(screen), Expectation(image="icon_on_black.png", threshold=0.90)
        )
        masked = verifier.verify(
            observe(screen),
            Expectation(
                image="icon_on_black.png",
                threshold=0.90,
                mask_background=True,
            ),
        )
        assert not plain.passed
        assert masked.passed
        assert masked.confidence >= 0.90

    def test_mask_background_absent_on_empty_region(self, image_config, tmp_path):
        from PIL import Image, ImageDraw

        ref = Image.new("RGB", (40, 40), (0, 0, 0))
        draw = ImageDraw.Draw(ref)
        draw.polygon([(5, 20), (30, 5), (30, 35)], fill=(0, 220, 80))
        ref.save(tmp_path / "icon_on_black.png")

        store = AssetStore([tmp_path])
        verifier = ImageAbsentVerifier(store, image_config)
        empty = Image.new("RGB", (200, 120), (10, 10, 10))
        result = verifier.verify(
            observe(empty),
            Expectation(
                image="icon_on_black.png",
                threshold=0.90,
                mask_background=True,
            ),
        )
        assert result.passed
        assert result.confidence is not None
        assert 0.0 <= result.confidence <= 1.0

    def test_mask_background_absent_on_black_screen_clamps_inf(
        self, image_config, tmp_path
    ):
        """Masked CCORR on pure black must not report FLT_MAX as confidence."""
        from PIL import Image, ImageDraw

        ref = Image.new("RGB", (40, 40), (0, 0, 0))
        draw = ImageDraw.Draw(ref)
        draw.polygon([(5, 20), (30, 5), (30, 35)], fill=(0, 220, 80))
        ref.save(tmp_path / "icon_on_black.png")

        store = AssetStore([tmp_path])
        verifier = ImageAbsentVerifier(store, image_config)
        black = Image.new("RGB", (200, 120), (0, 0, 0))
        result = verifier.verify(
            observe(black),
            Expectation(
                image="icon_on_black.png",
                threshold=0.90,
                mask_background=True,
            ),
        )
        assert result.passed
        assert result.confidence is not None
        assert result.confidence <= 1.0


class TestImageAbsent:
    def test_absent_passes(self, assets, image_config, artwork_b):
        verifier = ImageAbsentVerifier(assets, image_config)
        screen = make_screen(artwork_b)
        result = verifier.verify(observe(screen), Expectation(image="movie_123.png"))
        assert result.passed

    def test_present_fails(self, assets, image_config, artwork_a):
        verifier = ImageAbsentVerifier(assets, image_config)
        screen = make_screen(artwork_a)
        result = verifier.verify(observe(screen), Expectation(image="movie_123.png"))
        assert not result.passed
        assert result.location is not None


class TestScreenshotMatch:
    def test_identical_screens_match(self, assets, image_config, artwork_a, tmp_path):
        screen = make_screen(artwork_a)
        screen.save(tmp_path / "reference.png")
        store = AssetStore([tmp_path])
        verifier = ScreenshotMatchVerifier(store, image_config)
        result = verifier.verify(observe(screen), Expectation(image="reference.png"))
        assert result.passed
        assert result.confidence >= 0.99

    def test_different_screens_fail(self, assets, image_config, artwork_a, artwork_b, tmp_path):
        make_screen(artwork_a).save(tmp_path / "reference.png")
        store = AssetStore([tmp_path])
        verifier = ScreenshotMatchVerifier(store, image_config)
        from PIL import Image

        inverted = Image.eval(make_screen(artwork_b), lambda px: 255 - px)
        result = verifier.verify(observe(inverted), Expectation(image="reference.png"))
        assert not result.passed


class TestAssetStore:
    def test_caches_loaded_images(self, asset_dir):
        store = AssetStore([asset_dir])
        first = store.load_array("movie_123.png")
        second = store.load_array("movie_123.png")
        assert first is second  # same cached object

    def test_exists(self, asset_dir):
        store = AssetStore([asset_dir])
        assert store.exists("movie_123.png")
        assert not store.exists("missing.png")


# -- masked matching on empty chrome ----------------------------------------------------------


def _dim_chrome(width: int = 400, height: int = 160, seed: int = 1):
    """Noisy dim cluster chrome (~BGR 10/20/15) with no icon on it."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    base = np.array([15, 20, 10], dtype=np.int16)  # RGB of BGR 10/20/15
    noise = rng.integers(-6, 7, size=(height, width, 3), dtype=np.int16)
    return Image.fromarray(np.clip(base + noise, 0, 255).astype("uint8"), "RGB")


def _paste_icon(screen, icon, position):
    """Paste only the icon's non-black pixels (no black plate) onto the screen."""
    alpha = icon.convert("L").point(lambda v: 255 if v > 0 else 0)
    out = screen.copy()
    out.paste(icon, position, mask=alpha)
    return out


def test_mask_background_absent_on_dim_chrome(image_config, tmp_path):
    """TT-DOOR_FL-002: a masked telltale must not 'appear' on empty dim chrome.

    Masked TM_CCORR_NORMED scores ~0.99 wherever the mask *shape* overlaps flat
    dim chrome; image_not_present then fails with "unexpectedly present".
    """
    from PIL import Image, ImageDraw

    ref = Image.new("RGB", (96, 96), (0, 0, 0))
    ImageDraw.Draw(ref).rectangle((8, 8, 87, 87), outline=(255, 0, 0), width=10)
    ref.save(tmp_path / "door_ajar.png")

    store = AssetStore([tmp_path])
    present = ImagePresentVerifier(store, image_config)
    absent = ImageAbsentVerifier(store, image_config)
    exp = Expectation(
        image="door_ajar.png",
        threshold=0.70,
        grayscale=True,
        scale_tolerance=0.5,
        mask_background=True,
    )
    chrome = _dim_chrome()
    assert not present.verify(observe(chrome), exp).passed
    result = absent.verify(observe(chrome), exp)
    assert result.passed, result.message
    assert result.confidence is not None and result.confidence < 0.70


def test_mask_background_present_still_finds_icon_on_chrome(image_config, tmp_path):
    """The fix must not cost real icons: a green chevron on the same chrome matches."""
    from PIL import Image, ImageDraw

    ref = Image.new("RGB", (40, 40), (0, 0, 0))
    ImageDraw.Draw(ref).polygon([(5, 20), (30, 5), (30, 35)], fill=(0, 220, 80))
    ref.save(tmp_path / "chevron.png")

    store = AssetStore([tmp_path])
    present = ImagePresentVerifier(store, image_config)
    absent = ImageAbsentVerifier(store, image_config)
    exp = Expectation(
        image="chevron.png",
        threshold=0.70,
        grayscale=True,
        scale_tolerance=0.5,
        mask_background=True,
    )
    screen = _paste_icon(_dim_chrome(), ref, (200, 60))
    result = present.verify(observe(screen), exp)
    assert result.passed, result.message
    assert result.confidence is not None and result.confidence >= 0.70
    assert result.location is not None
    assert abs(result.location.x - 200) <= 2 and abs(result.location.y - 60) <= 2
    assert not absent.verify(observe(screen), exp).passed
