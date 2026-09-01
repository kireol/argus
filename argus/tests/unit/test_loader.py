"""YAML test loading and validation."""

from pathlib import Path

import pytest

from argus.engine.loader import load_tests
from argus.exceptions import TestDefinitionError

VALID_TEST = """
id: MOV-001
name: Movie artwork appears
feature: Movies
tags: [smoke]
platforms: [android]
steps:
  - action: log
    message: hello
"""

MULTI_TEST = """
tests:
  - id: A-001
    name: First
    feature: F
    steps:
      - action: log
        message: one
  - id: A-002
    name: Second
    feature: F
    steps:
      - action: log
        message: two
"""


def write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_load_single_test(tmp_path):
    write(tmp_path, "one.yaml", VALID_TEST)
    tests = load_tests([tmp_path])
    assert len(tests) == 1
    assert tests[0].id == "MOV-001"
    assert tests[0].source_file is not None


def test_load_multi_test_file(tmp_path):
    write(tmp_path, "multi.yaml", MULTI_TEST)
    tests = load_tests([tmp_path])
    assert [t.id for t in tests] == ["A-001", "A-002"]


def test_duplicate_ids_fail_fast(tmp_path):
    write(tmp_path, "one.yaml", VALID_TEST)
    write(tmp_path, "two.yaml", VALID_TEST)
    with pytest.raises(TestDefinitionError, match="Duplicate test ID"):
        load_tests([tmp_path])


def test_missing_required_field(tmp_path):
    write(tmp_path, "bad.yaml", "id: X-1\nname: No feature\nsteps:\n  - action: log\n")
    with pytest.raises(TestDefinitionError, match="feature"):
        load_tests([tmp_path])


def test_no_steps_rejected(tmp_path):
    write(tmp_path, "bad.yaml", "id: X-1\nname: n\nfeature: f\nsteps: []\n")
    with pytest.raises(TestDefinitionError):
        load_tests([tmp_path])


def test_invalid_yaml_syntax(tmp_path):
    write(tmp_path, "bad.yaml", "id: [unclosed\n")
    with pytest.raises(TestDefinitionError, match="Invalid YAML"):
        load_tests([tmp_path])


def test_unknown_top_level_field_rejected(tmp_path):
    write(tmp_path, "bad.yaml", VALID_TEST + "unexpected_field: true\n")
    with pytest.raises(TestDefinitionError):
        load_tests([tmp_path])


def test_invalid_timeout_rejected(tmp_path):
    write(tmp_path, "bad.yaml", VALID_TEST.replace("tags: [smoke]", "timeout: nonsense"))
    with pytest.raises(TestDefinitionError):
        load_tests([tmp_path])


def test_invalid_retry_category_rejected(tmp_path):
    content = VALID_TEST + "retry:\n  count: 1\n  only: [assertion]\n"
    write(tmp_path, "bad.yaml", content)
    with pytest.raises(TestDefinitionError, match="retry"):
        load_tests([tmp_path])


def test_empty_file_ignored(tmp_path):
    write(tmp_path, "empty.yaml", "")
    assert load_tests([tmp_path]) == []


def test_nonexistent_path_yields_nothing(tmp_path):
    assert load_tests([tmp_path / "nope"]) == []


# -- feature-level setup/teardown ----------------------------------------------------

FEATURE_SUITE = """
features:
  Movies:
    setup:
      - action: log
        message: feature setup
    teardown:
      - action: log
        message: feature teardown
tests:
  - id: M-001
    name: One
    feature: movies
    steps:
      - action: log
        message: one
"""


def test_load_suite_returns_features(tmp_path):
    from argus.engine.loader import load_suite

    write(tmp_path, "movies.yaml", FEATURE_SUITE)
    suite = load_suite([tmp_path])
    assert [t.id for t in suite.tests] == ["M-001"]
    feature = suite.feature_for("MOVIES")  # case-insensitive lookup
    assert feature is not None
    assert [s.action for s in feature.setup] == ["log"]
    assert [s.action for s in feature.teardown] == ["log"]
    assert suite.feature_for("Other") is None


def test_load_tests_ignores_features_block(tmp_path):
    write(tmp_path, "movies.yaml", FEATURE_SUITE)
    assert [t.id for t in load_tests([tmp_path])] == ["M-001"]


def test_duplicate_feature_definition_rejected(tmp_path):
    from argus.engine.loader import load_suite

    write(tmp_path, "a.yaml", FEATURE_SUITE)
    write(tmp_path, "b.yaml", FEATURE_SUITE.replace("M-001", "M-002"))
    with pytest.raises(TestDefinitionError, match="Duplicate feature"):
        load_suite([tmp_path])


def test_invalid_feature_step_rejected(tmp_path):
    from argus.engine.loader import load_suite

    write(tmp_path, "a.yaml", "features:\n  Movies:\n    setup:\n      - message: x\n")
    with pytest.raises(TestDefinitionError, match="Invalid feature definition 'Movies'"):
        load_suite([tmp_path])


def test_features_must_be_mapping(tmp_path):
    from argus.engine.loader import load_suite

    write(tmp_path, "a.yaml", "features: [1]\n")
    with pytest.raises(TestDefinitionError, match="'features' must be a mapping"):
        load_suite([tmp_path])


# -- suite-level setup/teardown ------------------------------------------------------

SUITE_LIFECYCLE = """
suite:
  setup:
    - action: log
      message: suite setup
  teardown:
    - action: log
      message: suite teardown
  device: fake_android
tests:
  - id: S-001
    name: One
    feature: movies
    steps:
      - action: log
        message: one
"""


def test_load_suite_returns_suite_lifecycle(tmp_path):
    from argus.engine.loader import load_suite

    write(tmp_path, "movies.yaml", SUITE_LIFECYCLE)
    suite = load_suite([tmp_path])
    assert suite.lifecycle is not None
    assert [s.action for s in suite.lifecycle.setup] == ["log"]
    assert [s.action for s in suite.lifecycle.teardown] == ["log"]
    assert suite.lifecycle.device == "fake_android"
    assert suite.lifecycle.source_file.endswith("movies.yaml")
    assert [t.id for t in suite.tests] == ["S-001"]
    assert load_suite([tmp_path / "nope"]).lifecycle is None


def test_duplicate_suite_lifecycle_rejected(tmp_path):
    from argus.engine.loader import load_suite

    write(tmp_path, "a.yaml", SUITE_LIFECYCLE)
    write(tmp_path, "b.yaml", SUITE_LIFECYCLE.replace("S-001", "S-002"))
    with pytest.raises(TestDefinitionError, match="Duplicate suite"):
        load_suite([tmp_path])


def test_invalid_suite_lifecycle_rejected(tmp_path):
    from argus.engine.loader import load_suite

    write(tmp_path, "a.yaml", "suite:\n  setup:\n    - message: x\n")
    with pytest.raises(TestDefinitionError, match="Invalid suite definition"):
        load_suite([tmp_path])
    write(tmp_path, "a.yaml", "suite: [1]\n")
    with pytest.raises(TestDefinitionError, match="'suite' must be a mapping"):
        load_suite([tmp_path])


SKIP_SUITE = """
features:
  Player:
    skip: player device is in the shop
    setup:
      - action: log
        message: never runs

tests:
  - id: S-001
    name: Skipped with flag
    feature: Movies
    skip: true
    steps:
      - action: log
        message: hi
  - id: S-002
    name: Skipped with reason
    feature: Movies
    skip: flaky until ARG-42 lands
    steps:
      - action: log
        message: hi
  - id: S-003
    name: Not skipped
    feature: Movies
    skip: false
    steps:
      - action: log
        message: hi
  - id: S-004
    name: Default
    feature: Player
    steps:
      - action: log
        message: hi
"""


def test_skip_true_gives_default_reason(tmp_path):
    write(tmp_path, "skip.yaml", SKIP_SUITE)
    tests = {t.id: t for t in load_tests([tmp_path])}
    assert tests["S-001"].skip_reason == "skipped"


def test_skip_string_is_the_reason(tmp_path):
    write(tmp_path, "skip.yaml", SKIP_SUITE)
    tests = {t.id: t for t in load_tests([tmp_path])}
    assert tests["S-002"].skip_reason == "flaky until ARG-42 lands"


def test_skip_false_and_absent_mean_not_skipped(tmp_path):
    write(tmp_path, "skip.yaml", SKIP_SUITE)
    tests = {t.id: t for t in load_tests([tmp_path])}
    assert tests["S-003"].skip_reason is None
    assert tests["S-004"].skip_reason is None


def test_feature_skip_is_loaded(tmp_path):
    from argus.engine.loader import load_suite

    write(tmp_path, "skip.yaml", SKIP_SUITE)
    suite = load_suite([tmp_path])
    assert suite.feature_for("player").skip_reason == "player device is in the shop"


def test_skip_rejects_non_bool_non_string(tmp_path):
    write(
        tmp_path,
        "bad.yaml",
        "id: B-1\nname: bad\nfeature: F\nskip: [a]\nsteps:\n  - action: log\n    message: x\n",
    )
    with pytest.raises(TestDefinitionError, match="skip"):
        load_tests([tmp_path])
