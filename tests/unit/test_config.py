"""Configuration loading, merging, env expansion."""

from pathlib import Path

import pytest

from argus.config.loader import load_config
from argus.exceptions import ConfigurationError


def write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content)
    return path


def test_defaults_without_files(tmp_path):
    config = load_config(root_dir=tmp_path, env={})
    assert config.verification.image.default_threshold == 0.90
    assert config.results.dir == "results"


def test_explicit_config(tmp_path):
    path = write_config(
        tmp_path,
        "backend:\n  base_url: http://example.test\n"
        "verification:\n  image:\n    default_threshold: 0.75\n",
    )
    config = load_config(path, root_dir=tmp_path, env={})
    assert config.backend.base_url == "http://example.test"
    assert config.verification.image.default_threshold == 0.75


def test_env_expansion(tmp_path):
    path = write_config(tmp_path, "backend:\n  base_url: ${BACKEND_URL}\n")
    config = load_config(path, root_dir=tmp_path, env={"BACKEND_URL": "http://b.test"})
    assert config.backend.base_url == "http://b.test"
    assert config.backend.configured


def test_unresolved_env_means_not_configured(tmp_path):
    path = write_config(tmp_path, "backend:\n  base_url: ${BACKEND_URL}\n")
    config = load_config(path, root_dir=tmp_path, env={})
    assert config.backend.base_url == "${BACKEND_URL}"
    assert not config.backend.configured


def test_unresolved_device_option_means_not_configured(tmp_path):
    path = write_config(
        tmp_path,
        "devices:\n  a:\n    type: android\n    serial: ${ANDROID_SERIAL}\n",
    )
    config = load_config(path, root_dir=tmp_path, env={})
    assert not config.devices["a"].configured
    config2 = load_config(path, root_dir=tmp_path, env={"ANDROID_SERIAL": "emulator-5554"})
    assert config2.devices["a"].configured


def test_repo_default_merged_with_explicit(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "default.yaml").write_text(
        "logging:\n  level: WARNING\nresults:\n  dir: results\n"
    )
    explicit = write_config(tmp_path, "results:\n  dir: custom_results\n")
    config = load_config(explicit, root_dir=tmp_path, env={})
    assert config.logging.level == "WARNING"  # from repo default
    assert config.results.dir == "custom_results"  # explicit wins


def test_missing_explicit_config_errors(tmp_path):
    with pytest.raises(ConfigurationError, match="not found"):
        load_config(tmp_path / "nope.yaml", root_dir=tmp_path, env={})


def test_invalid_yaml_errors(tmp_path):
    path = write_config(tmp_path, "backend: [oops\n")
    with pytest.raises(ConfigurationError, match="Invalid YAML"):
        load_config(path, root_dir=tmp_path, env={})


def test_unknown_field_errors(tmp_path):
    path = write_config(tmp_path, "nonsense_section: true\n")
    with pytest.raises(ConfigurationError, match="nonsense_section"):
        load_config(path, root_dir=tmp_path, env={})


def test_device_platform_defaults_to_type(tmp_path):
    path = write_config(
        tmp_path,
        "devices:\n  a:\n    type: fake\n  b:\n    type: fake\n    platform: android\n",
    )
    config = load_config(path, root_dir=tmp_path, env={})
    assert config.devices["a"].effective_platform == "fake"
    assert config.devices["b"].effective_platform == "android"
    assert set(config.devices_for_platform("android")) == {"b"}


def test_resolve_path_relative_to_root(tmp_path):
    config = load_config(root_dir=tmp_path, env={})
    assert config.resolve_path("assets/images") == tmp_path / "assets/images"
    absolute = Path("/absolute/path")
    assert config.resolve_path(absolute) == absolute


def test_extends_merges_base_then_override(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "base.yaml").write_text(
        "logging:\n  level: WARNING\n"
        "regions:\n  a: {x: 0, y: 0, width: 10, height: 10}\n"
        "variables:\n  SHARED: from-base\n  OVERRIDE_ME: base\n"
        "asset_paths: [assets/shared]\n",
        encoding="utf-8",
    )
    overlay = cfg_dir / "overlay.yaml"
    overlay.write_text(
        "extends: base.yaml\n"
        "regions:\n  a: {x: 1, y: 2, width: 30, height: 40}\n"
        "  b: {x: 5, y: 5, width: 5, height: 5}\n"
        "variables:\n  OVERRIDE_ME: overlay\n"
        "asset_paths: [assets/1300, assets/shared]\n",
        encoding="utf-8",
    )
    config = load_config(overlay, root_dir=tmp_path, env={})
    assert config.logging.level == "WARNING"
    assert config.regions["a"].x == 1
    assert config.regions["a"].width == 30
    assert "b" in config.regions
    assert config.variables["SHARED"] == "from-base"
    assert config.variables["OVERRIDE_ME"] == "overlay"
    assert config.asset_paths == ["assets/1300", "assets/shared"]
    # extends must not leak into AppConfig
    assert "extends" not in config.model_dump()


def test_extends_cycle_errors(tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("extends: b.yaml\nlogging:\n  level: INFO\n", encoding="utf-8")
    b.write_text("extends: a.yaml\nlogging:\n  level: DEBUG\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="cycle"):
        load_config(a, root_dir=tmp_path, env={})


def test_extends_missing_base_errors(tmp_path):
    path = write_config(tmp_path, "extends: missing.yaml\nlogging:\n  level: INFO\n")
    with pytest.raises(ConfigurationError, match="not found"):
        load_config(path, root_dir=tmp_path, env={})
