"""Deterministic, readable Argus YAML generation.

Rules: fixed key order (id, name, description, feature, tags, platforms,
priority, timeout, requires, parameters, retry, setup, steps, teardown),
no internal UI state, no unnecessary defaults, blank line between steps,
block-style lists, double-quoted strings only where YAML needs them.
"""

from __future__ import annotations

from typing import Any

import yaml

from argus_test_creator.models.authoring import AuthoringDocument, StepDraft

_TOP_ORDER = (
    "id", "name", "description", "feature", "tags", "platforms", "priority", "timeout",
    "requires", "parameters", "retry", "setup", "steps", "teardown",
)


class _Dumper(yaml.SafeDumper):
    """Indent block sequences (``- item`` under their key) like the Argus docs do."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:
        return super().increase_indent(flow, False)


def _str_presenter(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=">")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_presenter)


def _steps(steps: list[StepDraft], *, include_disabled: bool) -> list[dict[str, Any]]:
    return [s.to_argus() for s in steps if s.enabled or include_disabled]


def document_to_argus(document: AuthoringDocument, *, include_disabled: bool = False) -> dict[str, Any]:  # noqa: E501
    """The Argus test mapping (what ``TestDefinition`` would validate)."""
    meta = document.metadata
    data: dict[str, Any] = {
        "id": meta.id,
        "name": meta.name,
    }
    if meta.description:
        data["description"] = meta.description
    data["feature"] = meta.feature
    if meta.tags:
        data["tags"] = list(meta.tags)
    if meta.platforms:
        data["platforms"] = list(meta.platforms)
    if meta.priority:
        data["priority"] = meta.priority
    if meta.timeout:
        data["timeout"] = meta.timeout
    if meta.requires:
        data["requires"] = dict(meta.requires)
    if meta.parameters:
        data["parameters"] = dict(meta.parameters)
    if meta.retry_count:
        retry: dict[str, Any] = {"count": meta.retry_count}
        if meta.retry_only:
            retry["only"] = list(meta.retry_only)
        data["retry"] = retry
    if document.setup:
        data["setup"] = _steps(document.setup, include_disabled=include_disabled)
    data["steps"] = _steps(document.steps, include_disabled=include_disabled)
    if document.teardown:
        data["teardown"] = _steps(document.teardown, include_disabled=include_disabled)
    # Unknown top-level keys are preserved after the known ones.
    for key, value in document.unknown_fields.items():
        if key not in data:
            data[key] = value
    ordered = {key: data[key] for key in _TOP_ORDER if key in data}
    ordered.update({k: v for k, v in data.items() if k not in ordered})
    return ordered


def _dump(data: Any) -> str:
    return yaml.dump(
        data,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )


def document_to_yaml(document: AuthoringDocument, *, include_disabled: bool = False) -> str:
    """Render the document as Argus YAML text (stable across runs)."""
    data = document_to_argus(document, include_disabled=include_disabled)
    head = {k: v for k, v in data.items() if k not in ("setup", "steps", "teardown")}
    parts: list[str] = [_dump(head).rstrip("\n")]
    for section in ("setup", "steps", "teardown"):
        if section not in data:
            continue
        steps = data[section]
        if not steps:
            parts.append(f"{section}: []")
            continue
        lines = [f"{section}:"]
        for index, step in enumerate(steps):
            body = _dump([step]).rstrip("\n")
            indented = "\n".join("  " + line if line else line for line in body.splitlines())
            if index:
                lines.append("")
            lines.append(indented)
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"
