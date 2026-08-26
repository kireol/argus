"""Shared fixtures for the framework's own tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from argus.config.models import AppConfig
from argus.models.test_definition import TestDefinition


def make_artwork(base: tuple[int, int, int], accent: tuple[int, int, int]) -> Image.Image:
    """A structured 100x100 image suitable for template matching."""
    img = Image.new("RGB", (100, 100), base)
    draw = ImageDraw.Draw(img)
    draw.rectangle([5, 5, 94, 94], outline=accent, width=4)
    draw.ellipse([25, 15, 75, 65], fill=accent)
    draw.rectangle([20, 75, 80, 88], fill=(255, 255, 255))
    return img


def make_screen(
    artwork: Image.Image | None = None,
    *,
    size: tuple[int, int] = (640, 360),
    position: tuple[int, int] = (50, 40),
) -> Image.Image:
    screen = Image.new("RGB", size, (16, 16, 24))
    if artwork is not None:
        screen.paste(artwork, position)
    return screen


@pytest.fixture
def artwork_a() -> Image.Image:
    return make_artwork((30, 60, 130), (240, 200, 60))


@pytest.fixture
def artwork_b() -> Image.Image:
    return make_artwork((130, 30, 40), (60, 220, 180))


@pytest.fixture
def asset_dir(tmp_path: Path, artwork_a: Image.Image, artwork_b: Image.Image) -> Path:
    assets = tmp_path / "assets"
    assets.mkdir()
    artwork_a.save(assets / "movie_123.png")
    artwork_b.save(assets / "movie_456.png")
    return assets


def make_test(**overrides) -> TestDefinition:
    """A minimal valid test definition for unit tests."""
    data = {
        "id": "T-001",
        "name": "A test",
        "feature": "Feature",
        "tags": ["smoke"],
        "platforms": [],
        "steps": [{"action": "log", "message": "hello"}],
    }
    data.update(overrides)
    return TestDefinition.model_validate(data)


@pytest.fixture
def base_config(tmp_path: Path, asset_dir: Path) -> AppConfig:
    config = AppConfig(asset_paths=[str(asset_dir)], test_paths=[str(tmp_path / "suites")])
    config.root_dir = str(tmp_path)
    return config


def make_context(
    config: AppConfig,
    *,
    test: TestDefinition | None = None,
    device=None,
    backend=None,
    instrumentation=None,
    artifact_dir: Path | None = None,
):
    """Build a TestContext wired with fakes for unit tests."""
    from argus.artifacts.manager import TestArtifacts
    from argus.conditions.base import ConditionFactory
    from argus.conditions.builtin import register as register_conditions
    from argus.engine.context import TestContext, VerifierBundle
    from argus.events.bus import EventBus
    from argus.verifiers.assets import AssetStore

    test = test or make_test()
    assets = AssetStore([Path(p) for p in config.asset_paths])
    conditions = ConditionFactory()
    register_conditions(conditions)
    return TestContext(
        config=config,
        test=test,
        conditions=conditions,
        verifiers=VerifierBundle(assets=assets, config=config),
        events=EventBus(),
        artifacts=TestArtifacts(artifact_dir or Path(config.root_dir or ".") / "artifacts"),
        device=device,
        backend=backend,
        instrumentation=instrumentation,
        variables=dict(test.parameters),
    )
