"""CLI behavior via Typer's test runner."""

from pathlib import Path

import pytest
import yaml
from tests.conftest import make_artwork
from typer.testing import CliRunner

from argus.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A miniature project: config, suite, and assets in a temp directory."""
    assets = tmp_path / "assets"
    assets.mkdir()
    make_artwork((30, 60, 130), (240, 200, 60)).save(assets / "movie_123.png")

    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "movies.yaml").write_text(
        yaml.safe_dump(
            {
                "tests": [
                    {
                        "id": "MOV-001",
                        "name": "Movie artwork appears",
                        "feature": "Movies",
                        "tags": ["smoke"],
                        "platforms": ["android"],
                        "steps": [
                            {"action": "backend.set", "data": {"movieId": 123}},
                            {
                                "action": "verify",
                                "condition": {
                                    "type": "image_present",
                                    "image": "movie_123.png",
                                },
                            },
                        ],
                    },
                    {
                        "id": "SET-001",
                        "name": "Settings appear",
                        "feature": "Settings",
                        "platforms": ["android"],
                        "steps": [{"action": "log", "message": "hi"}],
                    },
                ]
            }
        )
    )

    config = {
        "backend": {"type": "fake"},
        "devices": {
            "fake_android": {
                "type": "fake",
                "platform": "android",
                "render": {
                    "state_image": {
                        "key": "movieId",
                        "template": "movie_{value}.png",
                        "search_dirs": [str(assets)],
                        "position": [50, 50],
                    }
                },
            }
        },
        "test_paths": [str(suites)],
        "asset_paths": [str(assets)],
        "results": {"dir": str(tmp_path / "results")},
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config))
    return config_file


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "argus" in result.output


def test_list(project):
    result = runner.invoke(app, ["list", "--config", str(project)])
    assert result.exit_code == 0
    assert "Movies" in result.output
    assert "MOV-001" in result.output
    assert "Settings" in result.output


def test_list_filtered_by_feature(project):
    result = runner.invoke(app, ["list", "--config", str(project), "--feature", "movies"])
    assert result.exit_code == 0
    assert "MOV-001" in result.output
    assert "SET-001" not in result.output


def test_run_feature_filter(project):
    result = runner.invoke(app, ["run", "--config", str(project), "--feature", "movies"])
    assert result.exit_code == 0, result.output
    assert "MOV-001" in result.output
    assert "TEST RUN PASSED" in result.output


def test_run_writes_reports(project, tmp_path):
    result = runner.invoke(app, ["run", "--config", str(project)])
    assert result.exit_code == 0, result.output
    run_dirs = list((tmp_path / "results").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "report.json").exists()
    assert (run_dirs[0] / "junit.xml").exists()
    assert (run_dirs[0] / "report.html").exists()


def test_run_no_match(project):
    result = runner.invoke(app, ["run", "--config", str(project), "--tag", "nonexistent"])
    assert result.exit_code == 1
    assert "No tests match" in result.output


def test_validate_framework_only(project):
    result = runner.invoke(app, ["validate", "--config", str(project), "--framework-only"])
    assert result.exit_code == 0, result.output
    assert "READY" in result.output


def test_validate_full(project):
    result = runner.invoke(app, ["validate", "--config", str(project)])
    assert result.exit_code == 0, result.output
    assert "fake_android" in result.output


def test_dry_run_executes_nothing(project, tmp_path):
    result = runner.invoke(app, ["--config", str(project), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY RUN COMPLETE" in result.output
    assert "Tests executed:" in result.output
    # Dry run must not create a results directory or execute tests.
    results = tmp_path / "results"
    assert not results.exists() or not any(results.iterdir())


def test_init_creates_config(tmp_path, monkeypatch):
    target = tmp_path / "userconfig" / "config.yaml"
    monkeypatch.setattr(
        "argus.cli.main.default_user_config_path", lambda: target
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert target.exists()
    assert "argus validate" in result.output
    # Second run refuses to overwrite without --force.
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_invalid_config_reports_clearly(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("nonsense_section: true\n")
    result = runner.invoke(app, ["list", "--config", str(bad)])
    assert result.exit_code == 2
    assert "CONFIGURATION ERROR" in result.output
