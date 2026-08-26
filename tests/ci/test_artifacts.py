"""Artifact layout: path safety, cleaning, metadata whitelist."""

from pathlib import Path

import pytest

from argus.artifacts.manager import safe_path_component
from argus.ci.artifacts import CIArtifactLayout, environment_metadata
from argus.ci.context import CIContext
from argus.exceptions import ConfigurationError


def test_safe_path_component():
    assert safe_path_component("MOV-001_android") == "MOV-001_android"
    assert safe_path_component("../../etc/passwd") == "etc_passwd"
    assert safe_path_component("feature/x:y") == "feature_x_y"
    assert safe_path_component("   ") == "unnamed"
    assert ".." not in safe_path_component("a..b..c")


def test_layout_refuses_root_and_project_dir(tmp_path):
    with pytest.raises(ConfigurationError):
        CIArtifactLayout(".", tmp_path)
    with pytest.raises(ConfigurationError):
        CIArtifactLayout(tmp_path.parent, tmp_path)
    with pytest.raises(ConfigurationError):
        CIArtifactLayout("/", tmp_path)


def test_prepare_cleans_only_owned_entries(tmp_path):
    layout = CIArtifactLayout("argus-results", tmp_path)
    out = layout.directory
    out.mkdir()
    (out / "report.json").write_text("old")
    (out / "tests").mkdir()
    (out / "tests" / "stale.txt").write_text("x")
    (out / "keep.txt").write_text("user file")
    layout.prepare()
    assert not (out / "report.json").exists()
    assert not (out / "tests").exists()
    assert (out / "keep.txt").read_text() == "user file"


def test_safe_child_and_relative(tmp_path):
    layout = CIArtifactLayout("argus-results", tmp_path)
    child = layout.safe_child("tests", "../escape")
    assert layout.directory in child.parents
    assert child.name == "escape"
    assert (
        layout.relative(layout.directory / "tests" / "T1" / "actual.png") == "tests/T1/actual.png"
    )


def test_metadata_is_whitelisted_and_redacted(tmp_path):
    layout = CIArtifactLayout("argus-results", tmp_path)
    layout.prepare()
    env = {"CI": "true", "GITHUB_TOKEN": "ghs_secret", "AWS_SECRET_ACCESS_KEY": "aws_secret"}
    context = CIContext(provider="github", display_name="GitHub Actions", branch="token=abc")
    written = layout.write_metadata(context, env)
    names = {p.name for p in written}
    assert {"ci.json", "environment.json"} <= names
    text = "".join(p.read_text() for p in written)
    assert "ghs_secret" not in text and "aws_secret" not in text
    assert "[REDACTED]" in (layout.metadata_dir / "ci.json").read_text()
    data = environment_metadata(env)
    assert data["variables"] == {"CI": "true"}


def test_inventory_lists_relative_paths(tmp_path):
    layout = CIArtifactLayout("argus-results", tmp_path)
    layout.prepare()
    (layout.tests_dir / "T1_android").mkdir(parents=True)
    (layout.tests_dir / "T1_android" / "actual.png").write_bytes(b"png")
    layout.report_json.write_text("{}")
    entries = layout.inventory({"T1_android": ("T1", "android")})
    paths = {e.path: e for e in entries}
    assert paths["tests/T1_android/actual.png"].test_id == "T1"
    assert paths["tests/T1_android/actual.png"].kind == "screenshot"
    assert paths["report.json"].kind == "report"
    assert all(not Path(e.path).is_absolute() for e in entries)
