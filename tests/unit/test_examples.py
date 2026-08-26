"""Every example under examples/ must load with the real config/test loaders."""
from pathlib import Path

import pytest

from argus.config.loader import load_config
from argus.engine.loader import load_tests

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted(p for p in (ROOT / "examples").iterdir() if (p / "argus.yaml").exists())


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_example_config_and_tests_load(example, monkeypatch):
    # Unresolved ${VARS} are allowed in configs; give the common ones a value.
    for var in ("ANDROID_SERIAL", "ROKU_HOST", "ROKU_DEV_PASSWORD", "YOCTO_HOST",
                "YOCTO_USER", "YOCTO_KEY", "ESP32_PORT"):
        monkeypatch.setenv(var, "x")
    config = load_config(example / "argus.yaml", root_dir=ROOT)
    paths = [config.resolve_path(p) for p in config.test_paths]
    tests = load_tests(paths)
    ids = [t.id for t in tests]
    assert len(ids) >= 5, f"{example.name}: expected at least 5 tests"
    assert len(ids) == len(set(ids)), "duplicate test ids"
    images = example / "images"
    for test in tests:
        for ref in _referenced_images(test):
            assert (images / ref).exists(), f"{example.name}: missing image {ref}"


def _referenced_images(test):
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") in {"image_present", "image_not_present", "screenshot_matches"}:
                yield node["image"]
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)
    yield from walk(test.model_dump())
