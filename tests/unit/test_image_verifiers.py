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

    def test_threshold_override(self, assets, image_config, artwork_a):
        verifier = ImagePresentVerifier(assets, image_config)
        screen = make_screen()  # empty screen
        strict = verifier.verify(
            observe(screen), Expectation(image="movie_123.png", threshold=0.99)
        )
        lenient = verifier.verify(
            observe(screen), Expectation(image="movie_123.png", threshold=0.0)
        )
        assert not strict.passed
        assert lenient.passed

    def test_template_larger_than_screen_errors(self, assets, image_config, artwork_a):
        verifier = ImagePresentVerifier(assets, image_config)
        from PIL import Image

        tiny = Image.new("RGB", (50, 50))
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
