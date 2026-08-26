"""``argus ci run`` through Typer's test runner."""

import json

import pytest
from tests.ci.conftest import assertion_failing_test, passing_test
from typer.testing import CliRunner

from argus.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_ci_help_lists_run():
    result = runner.invoke(app, ["ci", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output


def test_ci_run_help_documents_options_and_exit_codes():
    result = runner.invoke(app, ["ci", "run", "--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0
    out = result.output
    for flag in (
        "--suite",
        "--provider",
        "--config",
        "--dry-run",
        "--no-report",
        "--no-artifacts",
        "--fail-fast",
        "--retry",
        "--workers",
        "--verbose",
        "--tag",
        "--feature",
        "--platform",
        "--output-dir",
    ):
        assert flag in out, flag
    assert "Exit codes" in out and "6 policy" in out


def test_ci_dry_run(project):
    project.write_tests([passing_test("P-001")])
    project.write_config(ci={"suites": {"pr": {"tags": ["smoke"]}}})
    result = runner.invoke(
        app,
        ["ci", "run", "--config", str(project.config_path), "--suite", "pr", "--dry-run"],
        env={"GITHUB_ACTIONS": "", "CI": ""},
    )
    assert result.exit_code == 0, result.output
    assert "Argus CI Dry Run" in result.output
    assert "Provider:  Local" in result.output
    assert "No tests were executed." in result.output
    assert not project.output.exists()


def test_ci_run_success_and_reports(project):
    project.write_tests([passing_test("P-001")])
    project.write_config()
    result = runner.invoke(
        app, ["ci", "run", "--config", str(project.config_path), "--provider", "local"]
    )
    assert result.exit_code == 0, result.output
    assert "Argus CI Result" in result.output and "Result: PASSED" in result.output
    report = json.loads((project.output / "report.json").read_text())
    assert report["summary"]["passed"] == 1
    assert (project.output / "junit.xml").exists() and (project.output / "report.html").exists()


def test_ci_run_failure_exit_code(project):
    project.write_tests([assertion_failing_test("A-001")])
    project.write_config()
    result = runner.invoke(
        app, ["ci", "run", "--config", str(project.config_path), "--provider", "local"]
    )
    assert result.exit_code == 1, result.output
    assert "A-001" in result.output


def test_ci_run_unknown_suite_exit_code(project):
    project.write_tests([passing_test()])
    project.write_config()
    result = runner.invoke(
        app,
        [
            "ci",
            "run",
            "--config",
            str(project.config_path),
            "--suite",
            "nope",
            "--provider",
            "local",
        ],
    )
    assert result.exit_code == 2
    assert "Unknown suite" in result.output


def test_ci_run_invalid_config_exit_code(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("ci:\n  retry:\n    max_attempts: many\n")
    result = runner.invoke(app, ["ci", "run", "--config", str(bad)])
    assert result.exit_code == 2
    assert "CONFIGURATION ERROR" in result.output


def test_ci_run_unknown_provider_exit_code(project):
    project.write_tests([passing_test()])
    project.write_config()
    result = runner.invoke(
        app, ["ci", "run", "--config", str(project.config_path), "--provider", "bamboo"]
    )
    assert result.exit_code == 2
    assert "Unknown CI provider" in result.output


def test_ci_disabled(project):
    project.write_tests([passing_test()])
    project.write_config(ci={"enabled": False})
    result = runner.invoke(app, ["ci", "run", "--config", str(project.config_path)])
    assert result.exit_code == 2
    assert "ci.enabled" in result.output


def test_selectors_narrow_the_suite(project):
    project.write_tests([passing_test("P-001"), passing_test("P-002", feature="Settings")])
    project.write_config(ci={"suites": {"pr": {"tags": ["smoke"]}}})
    result = runner.invoke(
        app,
        [
            "ci",
            "run",
            "--config",
            str(project.config_path),
            "--suite",
            "pr",
            "--feature",
            "Settings",
            "--provider",
            "local",
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads((project.output / "report.json").read_text())
    assert [t["test_id"] for t in report["tests"]] == ["P-002"]
    assert report["run"]["selection"] == {
        "features": ["Settings"],
        "tags": ["smoke"],
        "suite": "pr",
    }
