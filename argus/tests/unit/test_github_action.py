"""The GitHub Action stays a thin wrapper over ``argus ci run``."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # argus/ project
REPO_ROOT = ROOT.parent  # monorepo root: action.yml and .github/ live here


def test_action_is_a_thin_wrapper():
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    inputs = action["inputs"]
    for name in (
        "suite",
        "config",
        "provider",
        "workers",
        "retry",
        "upload-artifacts",
        "output-dir",
        "working-directory",
    ):
        assert name in inputs, name
    outputs = action["outputs"]
    for name in ("status", "exit-code", "report-json", "junit-xml", "report-html"):
        assert name in outputs, name
    steps = action["runs"]["steps"]
    run_step = next(s for s in steps if s.get("id") == "run")
    script = run_step["run"]
    assert "ci run" in script
    # Inputs reach the CLI as flags; no execution logic is duplicated here.
    for flag in ("--suite", "--config", "--provider", "--workers", "--retry", "--output-dir"):
        assert flag in script, flag
    assert 'exit "$code"' in script  # exit code propagates
    upload = next(s for s in steps if "upload-artifact" in str(s.get("uses", "")))
    assert "always()" in upload["if"]


def test_example_workflow_and_repo_workflow_parse():
    example = yaml.safe_load((ROOT / "examples" / "ci" / "github-workflow.yml").read_text())
    assert "kireol/argus@v1" in str(example)
    repo = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "argus-ci.yml").read_text())
    assert {"action-success", "action-test-failure", "action-config-failure"} <= set(repo["jobs"])
