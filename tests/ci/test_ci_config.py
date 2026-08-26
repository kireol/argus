"""CI configuration validation (fails early, names the path and allowed values)."""

from pathlib import Path

import pytest

from argus.config.loader import load_config
from argus.config.models import AppConfig
from argus.exceptions import ConfigurationError


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_defaults_without_ci_section():
    ci = AppConfig().ci
    assert ci.enabled and ci.provider == "auto"
    assert ci.suites == {}
    assert not ci.retry.enabled and ci.retry.max_attempts == 2
    assert ci.execution.workers == 1 and ci.execution.strategy == "balanced"
    assert ci.artifacts.directory == "argus-results"
    assert ci.policy.failures.action == "fail"
    assert ci.policy.known_failure.action == "warn"
    assert ci.policy.flaky.action == "warn"


def test_valid_configuration(tmp_path):
    path = write(
        tmp_path,
        """
ci:
  provider: GitHub
  suites:
    pr: {tags: [smoke]}
    merge: {extends: pr, tags: [critical]}
  retry: {enabled: true, max_attempts: 3, on: [device_timeout, connection_error]}
  execution: {workers: 4, strategy: balanced}
  policy:
    required: [pr]
    flaky: {action: ignore}
  known_failures:
    - {test: MOV-002, reason: "tracked"}
""",
    )
    config = load_config(path, root_dir=tmp_path, env={})
    assert config.ci.provider == "github"
    assert config.ci.suites["merge"].extends == "pr"
    # YAML parses a bare `on:` as True; the model normalizes it and canonicalizes aliases.
    assert config.ci.retry.on == ["timeout", "connection_error"]
    assert config.ci.execution.workers == 4
    assert config.ci.known_failures[0].test == "MOV-002"


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("ci:\n  retry:\n    max_attempts: many\n", "ci.retry.max_attempts"),
        ("ci:\n  retry:\n    max_attempts: 0\n", "ci.retry.max_attempts"),
        ("ci:\n  retry:\n    on: [assertion_failure]\n", "unknown retry category"),
        ("ci:\n  policy:\n    failures:\n      action: explode\n", "unknown policy action"),
        ("ci:\n  execution:\n    workers: 0\n", "ci.execution.workers"),
        ("ci:\n  execution:\n    strategy: random\n", "unknown scheduling strategy"),
        ("ci:\n  suites:\n    pr:\n      extends: nope\n", "extends unknown suite"),
        ("ci:\n  suites:\n    pr:\n      colour: red\n", "colour"),
        ("ci:\n  artifacts:\n    directory: '  '\n", "must not be empty"),
        ("ci:\n  provider: ''\n", "must not be empty"),
        ("ci:\n  unknown: 1\n", "unknown"),
    ],
)
def test_invalid_configuration_is_reported(tmp_path, snippet, expected):
    path = write(tmp_path, snippet)
    with pytest.raises(ConfigurationError) as exc:
        load_config(path, root_dir=tmp_path, env={})
    assert expected in str(exc.value)


def test_malformed_yaml(tmp_path):
    path = write(tmp_path, "ci: [unclosed\n")
    with pytest.raises(ConfigurationError) as exc:
        load_config(path, root_dir=tmp_path, env={})
    assert "Invalid YAML" in str(exc.value)
