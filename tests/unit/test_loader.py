"""YAML test loading and validation."""

from pathlib import Path

import pytest

from utf.engine.loader import load_tests
from utf.exceptions import TestDefinitionError

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
