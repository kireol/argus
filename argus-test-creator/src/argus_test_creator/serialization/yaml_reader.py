"""Import an existing Argus YAML test into an AuthoringDocument.

Round-trip safety: unknown actions become ``custom`` steps carrying their
parameters verbatim; unknown top-level keys are kept in
``document.unknown_fields``; conditions are stored structurally so
``all``/``any``/``not`` survive untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from argus_test_creator.argus_schema import ACTIONS
from argus_test_creator.core.errors import SerializationError
from argus_test_creator.models.authoring import (
    AuthoringDocument,
    ConditionDraft,
    Provenance,
    StepDraft,
    TestMetadata,
)

_KNOWN_TOP = {
    "id", "name", "description", "feature", "tags", "platforms", "priority", "timeout",
    "requires", "parameters", "retry", "setup", "steps", "teardown",
}


def _step_from_argus(raw: Any, index: int, section: str) -> StepDraft:
    if not isinstance(raw, dict) or "action" not in raw:
        raise SerializationError(
            f"{section}[{index}] is not a step mapping with an 'action'.",
            remediation="Every step needs `action: <name>`.",
        )
    data = dict(raw)
    action = str(data.pop("action"))
    name = data.pop("name", None)
    condition: ConditionDraft | None = None
    if action in ("verify", "wait_until") and "condition" in data:
        try:
            condition = ConditionDraft.from_argus(data.pop("condition"))
        except ValueError as exc:
            raise SerializationError(
                f"{section}[{index}] has an invalid condition: {exc}",
                remediation="A condition needs `type:` or one of all/any/not.",
            ) from exc
    return StepDraft(
        action=action,
        name=str(name) if name is not None else None,
        params=data,
        condition=condition,
        custom=action not in ACTIONS,
        provenance=Provenance(source="import", note=f"{section}[{index}]"),
    )


def document_from_argus(raw: dict[str, Any], *, source_path: str | None = None) -> AuthoringDocument:  # noqa: E501
    if not isinstance(raw, dict):
        raise SerializationError("Test file must contain a mapping.")
    retry = raw.get("retry") or {}
    metadata = TestMetadata(
        id=str(raw.get("id", "") or ""),
        name=str(raw.get("name", "") or ""),
        description=str(raw.get("description", "") or ""),
        feature=str(raw.get("feature", "") or ""),
        tags=list(raw.get("tags") or []),
        platforms=list(raw.get("platforms") or []),
        priority=raw.get("priority"),
        timeout=str(raw["timeout"]) if raw.get("timeout") is not None else None,
        requires=dict(raw.get("requires") or {}),
        parameters=dict(raw.get("parameters") or {}),
        retry_count=int(retry.get("count", 0)) if isinstance(retry, dict) else 0,
        retry_only=list(retry.get("only", [])) if isinstance(retry, dict) else [],
    )
    doc = AuthoringDocument(metadata=metadata, source_path=source_path)
    for section in ("setup", "steps", "teardown"):
        items = raw.get(section) or []
        if not isinstance(items, list):
            raise SerializationError(f"'{section}' must be a list of steps.")
        setattr(doc, section, [_step_from_argus(s, i, section) for i, s in enumerate(items)])
    doc.unknown_fields = {k: v for k, v in raw.items() if k not in _KNOWN_TOP}
    return doc


def document_from_yaml(text: str, *, source_path: str | None = None) -> AuthoringDocument:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SerializationError(
            f"Invalid YAML: {exc}", remediation="Fix the YAML syntax error."
        ) from exc
    if data is None:
        raise SerializationError("The file is empty.")
    if isinstance(data, dict) and "id" not in data and "tests" in data:
        tests = data.get("tests") or []
        if len(tests) != 1:
            raise SerializationError(
                f"The file defines {len(tests)} tests; the Creator edits one test per file.",
                remediation="Split the file, or open a single-test YAML.",
            )
        data = tests[0]
    if not isinstance(data, dict):
        raise SerializationError("Expected a test definition mapping.")
    return document_from_argus(data, source_path=source_path)


def load_document(path: Path) -> AuthoringDocument:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SerializationError(f"Cannot read {path}: {exc}") from exc
    return document_from_yaml(text, source_path=str(path))
