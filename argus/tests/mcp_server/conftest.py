"""Fixtures for the MCP suite: a miniature project on fake devices only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.conftest import make_artwork

from argus.config.models import AppConfig

pytest.importorskip("mcp", reason="MCP SDK not installed (pip install 'argus[mcp]')")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Project:
    root: Path
    assets: Path
    suites: Path
    results: Path
    config_file: Path
    raw_config: dict[str, Any]

    def config(self, **overrides: Any) -> AppConfig:
        data = _deep_merge(self.raw_config, overrides)
        config = AppConfig.model_validate(data)
        config.root_dir = str(self.root)
        config.config_file = str(self.config_file)
        return config


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


TESTS: list[dict[str, Any]] = [
    {
        "id": "PASS-001",
        "name": "Artwork appears",
        "description": "Movie artwork is rendered after the backend selects it.",
        "feature": "Movies",
        "tags": ["smoke", "visual"],
        "platforms": ["android"],
        "parameters": {"movie_id": 123},
        "steps": [
            {"action": "backend.set", "data": {"movieId": "${movie_id}"}},
            {
                "action": "wait_until",
                "condition": {"type": "image_present", "image": "movie_123.png"},
                "timeout": "2s",
            },
        ],
    },
    {
        "id": "PASS-002",
        "name": "Second artwork appears",
        "feature": "Movies",
        "tags": ["visual", "regression"],
        "platforms": ["android"],
        "steps": [
            {"action": "backend.set", "data": {"movieId": 456}},
            {"action": "verify", "condition": {"type": "image_present", "image": "movie_456.png"}},
        ],
    },
    {
        "id": "FAIL-001",
        "name": "Wrong artwork expected",
        "feature": "Movies",
        "tags": ["visual", "broken"],
        "platforms": ["android"],
        "steps": [
            {"action": "backend.set", "data": {"movieId": 123}},
            {"action": "verify", "condition": {"type": "image_present", "image": "movie_456.png"}},
        ],
    },
    {
        "id": "FAIL-002",
        "name": "Also broken",
        "feature": "Movies",
        "tags": ["broken"],
        "platforms": ["android"],
        "steps": [
            {"action": "backend.set", "data": {"movieId": 123}},
            {"action": "verify", "condition": {"type": "image_present", "image": "movie_456.png"}},
        ],
    },
    {
        "id": "SLOW-001",
        "name": "Takes a while",
        "feature": "Slow",
        "tags": ["slow"],
        "platforms": ["android"],
        "steps": [{"action": "wait", "duration": "3s"}],
    },
    {
        "id": "SET-001",
        "name": "Settings log line",
        "feature": "Settings",
        "tags": ["smoke"],
        "platforms": [],
        "steps": [{"action": "log", "message": "hello"}],
    },
]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    assets = tmp_path / "assets"
    assets.mkdir()
    make_artwork((30, 60, 130), (240, 200, 60)).save(assets / "movie_123.png")
    make_artwork((130, 30, 40), (60, 220, 180)).save(assets / "movie_456.png")

    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "suite.yaml").write_text(yaml.safe_dump({"tests": TESTS}))

    empty_frames = tmp_path / "no_frames"
    empty_frames.mkdir()

    results = tmp_path / "results"
    raw: dict[str, Any] = {
        "backend": {"type": "fake", "initial_state": {"movieId": None}, "token": "s3cret-token"},
        "devices": {
            "fake_android": {
                "type": "fake",
                "platform": "android",
                "screen_size": [1280, 720],
                "render": {
                    "state_image": {
                        "key": "movieId",
                        "template": "movie_{value}.png",
                        "search_dirs": [str(assets)],
                        "position": [100, 100],
                    }
                },
                "instrumentation": {"type": "fake", "status": {"ready": True, "screen": "home"}},
            },
            # Connects, but every screenshot fails (no PNG frames to serve).
            "fake_broken": {
                "type": "fake",
                "platform": "broken",
                "screenshot_dir": str(empty_frames),
            },
            # Unresolved ${...} value -> "not configured".
            "fake_ghost": {"type": "fake", "platform": "ghost", "serial": "${ARGUS_TEST_MISSING}"},
        },
        "test_paths": [str(suites)],
        "asset_paths": [str(assets)],
        "results": {"dir": str(results)},
        "wait": {"default_timeout": "2s", "default_poll_interval": "50ms"},
        "logging": {"level": "WARNING"},
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(raw))
    return Project(
        root=tmp_path,
        assets=assets,
        suites=suites,
        results=results,
        config_file=config_file,
        raw_config=raw,
    )
