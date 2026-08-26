"""Fixtures for the CI layer: a miniature project against fake adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.conftest import make_artwork

from argus.config.loader import load_config
from argus.config.models import AppConfig


def passing_test(test_id: str = "P-001", **overrides: Any) -> dict[str, Any]:
    test = {
        "id": test_id,
        "name": f"Passing {test_id}",
        "feature": "Movies",
        "tags": ["smoke"],
        "platforms": ["android"],
        "steps": [
            {"action": "backend.set", "data": {"movieId": 123}},
            {
                "action": "wait_until",
                "condition": {"type": "image_present", "image": "movie_123.png"},
                "timeout": "2s",
            },
        ],
    }
    test.update(overrides)
    return test


def visual_failing_test(test_id: str = "V-001", **overrides: Any) -> dict[str, Any]:
    """Fails an image verification -> visual_regression."""
    test = {
        "id": test_id,
        "name": f"Visual failure {test_id}",
        "feature": "Movies",
        "tags": ["visual"],
        "platforms": ["android"],
        "steps": [
            {"action": "backend.set", "data": {"movieId": 123}},
            {
                "action": "verify",
                "condition": {"type": "image_present", "image": "movie_456.png"},
            },
        ],
    }
    test.update(overrides)
    return test


def assertion_failing_test(test_id: str = "A-001", **overrides: Any) -> dict[str, Any]:
    """Fails a non-visual verification -> assertion_failure."""
    test = {
        "id": test_id,
        "name": f"Assertion failure {test_id}",
        "feature": "Settings",
        "tags": ["settings"],
        "platforms": ["android"],
        "steps": [
            {"action": "backend.set", "data": {"movieId": 123}},
            {
                "action": "verify",
                "condition": {"type": "backend_value", "key": "movieId", "equals": 999},
            },
        ],
    }
    test.update(overrides)
    return test


class Project:
    """Builder for a temp project (config + suite + assets)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.assets = root / "assets"
        self.assets.mkdir(exist_ok=True)
        make_artwork((30, 60, 130), (240, 200, 60)).save(self.assets / "movie_123.png")
        make_artwork((130, 30, 40), (60, 220, 180)).save(self.assets / "movie_456.png")
        self.suites = root / "suites"
        self.suites.mkdir(exist_ok=True)
        self.config_path = root / "argus.yaml"
        self.output = root / "argus-results"

    def write_tests(self, tests: list[dict[str, Any]], filename: str = "suite.yaml") -> None:
        (self.suites / filename).write_text(yaml.safe_dump({"tests": tests}))

    def device(self, name: str, platform: str, **options: Any) -> dict[str, Any]:
        return {
            "type": "fake",
            "platform": platform,
            "render": {
                "state_image": {
                    "key": "movieId",
                    "template": "movie_{value}.png",
                    "search_dirs": [str(self.assets)],
                    "position": [50, 50],
                }
            },
            "instrumentation": {"type": "fake", "status": {"ready": True}},
            **options,
        }

    def write_config(
        self,
        *,
        ci: dict[str, Any] | None = None,
        devices: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        config: dict[str, Any] = {
            "backend": {"type": "fake", "initial_state": {"movieId": None}},
            "devices": devices or {"fake_android": self.device("fake_android", "android")},
            "test_paths": [str(self.suites)],
            "asset_paths": [str(self.assets)],
            "results": {"dir": str(self.root / "results")},
            "wait": {"default_timeout": "2s", "default_poll_interval": "50ms"},
            "ci": {"artifacts": {"directory": str(self.output)}, **(ci or {})},
        }
        if extra:
            config.update(extra)
        self.config_path.write_text(yaml.safe_dump(config))
        return self.config_path

    def load(self) -> AppConfig:
        return load_config(self.config_path, root_dir=self.root, env={})


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path)
