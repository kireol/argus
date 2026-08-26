"""YAML test definition loading and validation.

A test file contains either a single test definition (mapping with an ``id``)
or several under a top-level ``tests:`` list, optionally alongside a top-level
``features:`` mapping of feature-level ``setup``/``teardown`` steps.
Duplicate test IDs and duplicate feature definitions fail fast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from argus.exceptions import TestDefinitionError
from argus.models.test_definition import FeatureDefinition, TestDefinition

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


@dataclass
class TestSuite:
    """Everything loaded from the test paths: tests plus feature lifecycles."""

    tests: list[TestDefinition] = field(default_factory=list)
    features: dict[str, FeatureDefinition] = field(default_factory=dict)  # keyed lower-case

    def feature_for(self, name: str) -> FeatureDefinition | None:
        """Feature lifecycle for a test's ``feature`` (case-insensitive)."""
        return self.features.get(name.strip().lower())


def _parse_file(path: Path) -> list[dict[str, Any]]:
    return _parse_file_full(path)[0]


def _parse_file_full(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return ``(raw_tests, raw_features)`` from one YAML file."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise TestDefinitionError(
            f"Invalid YAML in test file {path}: {exc}",
            remediation="Fix the YAML syntax error shown above.",
        ) from exc

    if data is None:
        return [], {}
    if isinstance(data, dict) and "id" not in data and ("tests" in data or "features" in data):
        tests = data.get("tests", [])
        if not isinstance(tests, list):
            raise TestDefinitionError(f"{path}: top-level 'tests' must be a list.")
        features = data.get("features", {})
        if not isinstance(features, dict):
            raise TestDefinitionError(f"{path}: top-level 'features' must be a mapping.")
        return tests, features
    if isinstance(data, dict):
        return [data], {}
    raise TestDefinitionError(
        f"{path}: expected a test definition mapping or a top-level 'tests' list."
    )


def load_tests(paths: list[Path]) -> list[TestDefinition]:
    """Load and validate every test definition under ``paths`` (see ``load_suite``)."""
    return load_suite(paths).tests


def load_suite(paths: list[Path]) -> TestSuite:
    """Load and validate every test and feature definition under ``paths``.

    Raises :class:`TestDefinitionError` on the first invalid definition or on
    duplicate IDs/features — a broken suite should never partially run.
    """
    definitions: list[TestDefinition] = []
    features: dict[str, FeatureDefinition] = {}
    seen: dict[str, Path] = {}
    seen_features: dict[str, Path] = {}

    for file in discover_test_files(paths):
        raw_tests, raw_features = _parse_file_full(file)
        for name, raw_feature in raw_features.items():
            key = str(name).strip().lower()
            if key in seen_features:
                raise TestDefinitionError(
                    f"Duplicate feature definition {name!r} in {file} "
                    f"(first defined in {seen_features[key]}).",
                    remediation="Define each feature's setup/teardown in one file only.",
                )
            payload = dict(raw_feature) if isinstance(raw_feature, dict) else raw_feature
            if isinstance(payload, dict):
                payload.setdefault("name", str(name))
            try:
                feature = FeatureDefinition.model_validate(payload)
            except ValidationError as exc:
                raise TestDefinitionError(
                    f"Invalid feature definition {name!r} in {file}:\n{exc}",
                    remediation="Fix the fields listed above; see docs/test-authoring.md.",
                ) from exc
            feature.source_file = str(file)
            seen_features[key] = file
            features[key] = feature

        for raw in raw_tests:
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

    return TestSuite(tests=definitions, features=features)
