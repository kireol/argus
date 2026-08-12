"""YAML test definition loading and validation.

A test file contains either a single test definition (mapping with an ``id``)
or several under a top-level ``tests:`` list. Duplicate IDs fail fast.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from utf.exceptions import TestDefinitionError
from utf.models.test_definition import TestDefinition

_YAML_SUFFIXES = {".yaml", ".yml"}


def discover_test_files(paths: list[Path]) -> list[Path]:
    """Find all YAML test files under the given paths (files or directories)."""
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix in _YAML_SUFFIXES:
            files.append(path)
        elif path.is_dir():
            files.extend(
                sorted(
                    p
                    for p in path.rglob("*")
                    if p.is_file() and p.suffix in _YAML_SUFFIXES
                )
            )
    return files


def _parse_file(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise TestDefinitionError(
            f"Invalid YAML in test file {path}: {exc}",
            remediation="Fix the YAML syntax error shown above.",
        ) from exc

    if data is None:
        return []
    if isinstance(data, dict) and "tests" in data and "id" not in data:
        tests = data["tests"]
        if not isinstance(tests, list):
            raise TestDefinitionError(f"{path}: top-level 'tests' must be a list.")
        return tests
    if isinstance(data, dict):
        return [data]
    raise TestDefinitionError(
        f"{path}: expected a test definition mapping or a top-level 'tests' list."
    )


def load_tests(paths: list[Path]) -> list[TestDefinition]:
    """Load and validate every test definition under ``paths``.

    Raises :class:`TestDefinitionError` on the first invalid definition or on
    duplicate IDs — a broken suite should never partially run.
    """
    definitions: list[TestDefinition] = []
    seen: dict[str, Path] = {}

    for file in discover_test_files(paths):
        for raw in _parse_file(file):
            try:
                test = TestDefinition.model_validate(raw)
            except ValidationError as exc:
                test_id = raw.get("id", "<missing id>") if isinstance(raw, dict) else "?"
                raise TestDefinitionError(
                    f"Invalid test definition {test_id!r} in {file}:\n{exc}",
                    remediation="Fix the fields listed above; see docs/test-authoring.md.",
                ) from exc

            if test.id in seen:
                raise TestDefinitionError(
                    f"Duplicate test ID {test.id!r} in {file} "
                    f"(first defined in {seen[test.id]}).",
                    remediation="Test IDs must be unique across the whole suite.",
                )
            seen[test.id] = file
            test.source_file = str(file)
            definitions.append(test)

    return definitions
