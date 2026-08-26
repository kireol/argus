"""``argus stress`` CLI: run, dry-run, replay, minimize, list, exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from argus.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_argus_logging():
    """The CLI configures a stderr handler on CliRunner's captured stream; drop it after."""
    import logging

    yield
    logging.getLogger("argus").handlers.clear()

SCENARIO = """
backend: {{type: stress_demo}}
devices:
  store: {{type: stress_demo, platform: fake, buggy: true, crash_on_text: "<script>"}}
stress:
  name: cli-test
  device: store
  duration: 10m
  max_actions: {max_actions}
  results_dir: {results}
  monkey:
    delay: {{min: 0.001, max: 0.002}}
    actions: {{tap: 40, back: 10, type_text: 10}}
    typing: {{words: ["<script>", "batman"]}}
    targets:
      regions:
        - {{name: "Checkout", x: 40, y: 1080, width: 640, height: 80, weight: 3}}
  backend_mutations:
    enabled: true
    probability: 0.3
    operations: {{delete: {{enabled: true, weight: 1}}, update: {{weight: 1}}}}
    entities:
      products:
        state_key: products
        current_key: current_product
        fields:
          title: {{type: string, display: true}}
          price: {{type: number, min: 0, max: 100}}
    reconcile_timeout: 0.001s
  failures: {{error_words: ["not running"]}}
  safety: {{allow_destructive_mutations: true, environment: test, allowed_entities: [products]}}
"""


def _scenario(tmp_path: Path, max_actions: int = 60) -> Path:
    path = tmp_path / "scenario.yaml"
    path.write_text(SCENARIO.format(max_actions=max_actions,
                                    results=(tmp_path / "results").as_posix()),
                    encoding="utf-8")
    return path


def test_help_lists_stress_commands():
    result = runner.invoke(app, ["stress", "--help"])
    assert result.exit_code == 0
    for word in ("--seed", "--dry-run", "--scenario", "replay", "minimize", "list"):
        assert word in result.output


def test_run_reports_seed_and_exits_nonzero_on_failures(tmp_path):
    scenario = _scenario(tmp_path)
    result = runner.invoke(app, ["stress", "--scenario", str(scenario), "--seed", "84729163",
                                 "--verbosity", "quiet"])
    assert "Seed:         84729163" in result.output
    assert "Argus Stress Run" in result.output
    assert "Replay:" in result.output and "argus stress replay" in result.output
    runs = list((tmp_path / "results").iterdir())
    assert len(runs) == 1 and (runs[0] / "run.json").is_file()
    record = json.loads((runs[0] / "run.json").read_text())
    assert record["seed"] == 84729163
    assert result.exit_code == (1 if any(
        f["category"] not in ("infrastructure", "backend", "unsupported", "unsafe", "device",
                              "configuration") and f["severity"] in ("error", "critical")
        for f in record["failures"]) else 0)


def test_dry_run_prints_plan_and_writes_nothing(tmp_path):
    scenario = _scenario(tmp_path, max_actions=8)
    result = runner.invoke(app, ["stress", "--scenario", str(scenario), "--seed", "5",
                                 "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output and "Seed: 5" in result.output
    assert "MUTATION" in result.output and "BLOCKED: dry run" in result.output
    assert not (tmp_path / "results").exists()


def test_replay_and_minimize_and_list(tmp_path):
    scenario = _scenario(tmp_path, max_actions=50)
    run = runner.invoke(app, ["stress", "--scenario", str(scenario), "--seed", "11",
                              "--verbosity", "quiet"])
    assert "Run ID:" in run.output
    listing = runner.invoke(app, ["stress", "--scenario", str(scenario), "list"])
    assert listing.exit_code == 0 and "cli-test" in listing.output
    replay = runner.invoke(app, ["stress", "--scenario", str(scenario), "replay", "latest"])
    assert "Replaying" in replay.output, replay.output
    assert "Reproduced" in replay.output or "Failures:" in replay.output
    record = json.loads(next((tmp_path / "results").glob("*/run.json")).read_text())
    if any(f["category"] == "crash" for f in record["failures"]):
        minimized = runner.invoke(app, ["stress", "--scenario", str(scenario), "minimize",
                                        record["run_id"], "--failure", "crash:crash",
                                        "--max-iterations", "12"])
        assert minimized.exit_code == 0, minimized.output
        assert "Reduced" in minimized.output and "Minimal sequence" in minimized.output


def test_unknown_run_and_bad_scenario_exit_with_config_code(tmp_path):
    scenario = _scenario(tmp_path)
    result = runner.invoke(app, ["stress", "--scenario", str(scenario), "replay", "nope"])
    assert result.exit_code == 2
    result = runner.invoke(app, ["stress", "--scenario", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 2


def test_existing_commands_still_work():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0 and "stress" in result.output and "ci" in result.output
    assert runner.invoke(app, ["version"]).exit_code == 0
