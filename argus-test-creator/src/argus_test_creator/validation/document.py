"""Creator-side validation (fast, synchronous, actionable).

Levels:
1. Metadata (id/name/feature required, id pattern, timeout format).
2. Steps (known action, required params, param types, durations).
3. Conditions (known type, required params, one-of groups, region shape).
4. Assets (referenced images exist in the document or on disk).
5. Target capabilities (action/condition supported by the selected target).

Argus's own validation runs separately through the integration layer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from argus_test_creator.argus_schema import ACTIONS, CONDITIONS, ParamSpec
from argus_test_creator.argus_schema.conditions import COMPOSITE_FORMS
from argus_test_creator.models.authoring import (
    AuthoringDocument,
    ConditionDraft,
    StepDraft,
    ValidationIssue,
)
from argus_test_creator.models.capabilities import RecorderCapabilities
from argus_test_creator.models.common import Rect, parse_duration

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_RETRY_CATEGORIES = {"timeout", "device_connection", "backend", "screenshot"}
_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


class DocumentValidator:
    def __init__(self, *, asset_root: Path | None = None) -> None:
        self._asset_root = asset_root

    def validate(self, document: AuthoringDocument) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        issues.extend(self._metadata(document))
        capabilities = document.target.capabilities if document.target else None
        for section in ("setup", "steps", "teardown"):
            for step in getattr(document, section):
                issues.extend(self._step(step, section, capabilities))
        issues.extend(self._assets(document))
        if not [s for s in document.steps if s.enabled]:
            issues.append(ValidationIssue(
                code="no_steps", message="The test has no enabled steps.",
                fix="Record or add at least one step.",
            ))
        return issues

    # -- metadata ---------------------------------------------------------------

    def _metadata(self, document: AuthoringDocument) -> Iterable[ValidationIssue]:
        meta = document.metadata
        if not meta.id:
            yield ValidationIssue(code="id_required", message="ID is required.", field="id",
                                  fix="Give the test a unique ID such as MOV-001.")
        elif not _ID_RE.match(meta.id):
            yield ValidationIssue(
                code="id_format", field="id",
                message=f"ID {meta.id!r} must start with a letter and use only letters, "
                        "digits, '_' or '-'.",
                fix="Use an ID like MOV-001 or login_smoke.",
            )
        if not meta.name:
            yield ValidationIssue(code="name_required", message="Name is required.",
                                  field="name", fix="Describe what the test verifies.")
        if not meta.feature:
            yield ValidationIssue(code="feature_required", message="Feature is required.",
                                  field="feature", fix="Name the feature area, e.g. Movies.")
        if meta.timeout:
            try:
                parse_duration(meta.timeout)
            except ValueError as exc:
                yield ValidationIssue(code="timeout_format", message=str(exc), field="timeout",
                                      fix="Use a duration like 60s or 2m.")
        unknown = set(meta.retry_only) - _RETRY_CATEGORIES
        if unknown:
            yield ValidationIssue(
                code="retry_categories", field="retry",
                message=f"Unknown retry categories: {sorted(unknown)}.",
                fix=f"Allowed: {sorted(_RETRY_CATEGORIES)}.",
            )
        if not 0 <= meta.retry_count <= 10:
            yield ValidationIssue(code="retry_count", field="retry",
                                  message="Retry count must be between 0 and 10.")
        devices = meta.requires.get("devices")
        if devices is not None and not isinstance(devices, (list, str)):
            yield ValidationIssue(code="requires_devices", field="requires",
                                  message="requires.devices must be a list of device names.")

    # -- steps ------------------------------------------------------------------------

    def _step(
        self, step: StepDraft, section: str, capabilities: RecorderCapabilities | None
    ) -> Iterable[ValidationIssue]:
        label = step.display_name()
        spec = ACTIONS.get(step.action)
        if spec is None:
            yield ValidationIssue(
                severity="warning", code="unknown_action", step_id=step.id,
                message=f"Step {label!r} uses action {step.action!r} that the Creator does not "
                        "know. It is preserved verbatim.",
                fix="Make sure the action is provided by an Argus plugin.",
            )
            return
        for param in spec.params:
            if param.name == "condition":
                continue
            if param.required and param.name not in step.params:
                yield ValidationIssue(
                    code="missing_param", step_id=step.id, field=param.name,
                    message=f"Step {label!r} ({step.action}) is missing '{param.name}'.",
                    fix=f"Set '{param.name}' in the step editor.",
                )
            elif param.name in step.params:
                yield from self._param_type(step, param, step.params[param.name])
        if step.action in ("verify", "wait_until"):
            if step.condition is None:
                yield ValidationIssue(
                    code="missing_condition", step_id=step.id, field="condition",
                    message=f"Step {label!r} has no condition.",
                    fix="Choose a verification type (text, image, ...).",
                )
            else:
                yield from self._condition(step, step.condition, capabilities)
        if step.action == "wait" and section == "steps":
            yield ValidationIssue(
                severity="warning", code="fixed_wait", step_id=step.id,
                message=f"Step {label!r} is a fixed wait.",
                fix="Prefer 'wait_until' with a condition describing what you are waiting for.",
            )
        if step.action == "shell.run":
            yield ValidationIssue(
                severity="info", code="shell_run", step_id=step.id,
                message=f"Step {label!r} runs a host command; review it before sharing.",
            )
        if capabilities is not None:
            missing = [r for r in spec.requires if not capabilities.has(r)]
            if missing:
                yield ValidationIssue(
                    severity="warning", code="unsupported_action", step_id=step.id,
                    message=f"Step {label!r}: the selected target does not support "
                            f"{', '.join(missing)}.",
                    fix="Choose another action or a target that supports it.",
                )

    def _param_type(self, step: StepDraft, param: ParamSpec, value: Any) -> Iterable[ValidationIssue]:  # noqa: E501
        label = step.display_name()
        if isinstance(value, str) and "${" in value:
            return  # variable reference; resolved by Argus
        match param.type:
            case "int":
                if isinstance(value, bool) or not isinstance(value, int):
                    try:
                        int(value)
                    except (TypeError, ValueError):
                        yield ValidationIssue(
                            code="param_type", step_id=step.id, field=param.name,
                            message=f"Step {label!r}: '{param.name}' must be a whole number.",
                        )
            case "duration":
                try:
                    parse_duration(value)
                except (TypeError, ValueError):
                    yield ValidationIssue(
                        code="param_type", step_id=step.id, field=param.name,
                        message=f"Step {label!r}: '{param.name}' must be a duration (e.g. 500ms).",
                    )
            case "list":
                if not isinstance(value, list):
                    yield ValidationIssue(
                        code="param_type", step_id=step.id, field=param.name,
                        message=f"Step {label!r}: '{param.name}' must be a list.",
                    )
            case "mapping":
                if not isinstance(value, dict):
                    yield ValidationIssue(
                        code="param_type", step_id=step.id, field=param.name,
                        message=f"Step {label!r}: '{param.name}' must be a mapping.",
                    )

    # -- conditions -----------------------------------------------------------------------

    def _condition(
        self, step: StepDraft, condition: ConditionDraft, capabilities: RecorderCapabilities | None
    ) -> Iterable[ValidationIssue]:
        label = step.display_name()
        form = condition.form
        if form == "empty":
            yield ValidationIssue(
                code="condition_form", step_id=step.id, field="condition",
                message=f"Step {label!r}: a condition needs 'type' or one of "
                        f"{', '.join(COMPOSITE_FORMS)}.",
            )
            return
        if form in COMPOSITE_FORMS:
            children = condition.all or condition.any or (
                [condition.not_] if condition.not_ else []
            )
            if not children:
                yield ValidationIssue(
                    code="condition_empty_composite", step_id=step.id, field="condition",
                    message=f"Step {label!r}: '{form}' has no child conditions.",
                )
            for child in children:
                yield from self._condition(step, child, capabilities)
            return
        assert condition.type is not None
        spec = CONDITIONS.get(condition.type)
        if spec is None:
            yield ValidationIssue(
                severity="warning", code="unknown_condition", step_id=step.id, field="condition",
                message=f"Step {label!r}: condition type {condition.type!r} is unknown to the "
                        "Creator (kept verbatim).",
            )
            return
        params = condition.params
        for name in spec.required_params:
            if name not in params or params[name] in ("", None):
                yield ValidationIssue(
                    code="condition_missing_param", step_id=step.id, field=name,
                    message=f"Invalid condition: {condition.type}\nMissing: {name}\n"
                            f"Step: {label!r}",
                    fix=_fix_for(condition.type, name),
                )
        for group in spec.one_of:
            if not any(g in params for g in group):
                yield ValidationIssue(
                    code="condition_one_of", step_id=step.id, field="condition",
                    message=f"Step {label!r}: {condition.type} needs one of "
                            f"{', '.join(group)}.",
                )
        threshold = params.get("threshold")
        if threshold is not None and not (isinstance(threshold, str) and "${" in threshold):
            try:
                t = float(threshold)
            except (TypeError, ValueError):
                yield ValidationIssue(code="threshold_type", step_id=step.id, field="threshold",
                                      message=f"Step {label!r}: threshold must be 0..1.")
            else:
                if not 0.0 <= t <= 1.0:
                    yield ValidationIssue(code="threshold_range", step_id=step.id,
                                          field="threshold",
                                          message=f"Step {label!r}: threshold must be 0..1.")
                elif t < 0.6:
                    yield ValidationIssue(
                        severity="warning", code="threshold_low", step_id=step.id,
                        field="threshold",
                        message=f"Step {label!r}: threshold {t} is very low and may match "
                                "unrelated content.",
                        fix="Use 0.85–0.95 unless you have a reason not to.",
                    )
        region = params.get("region")
        if isinstance(region, dict):
            try:
                Rect.from_any(region)
            except Exception as exc:  # noqa: BLE001 - pydantic error text is what we want
                yield ValidationIssue(
                    code="region_shape", step_id=step.id, field="region",
                    message=f"Step {label!r}: region must have x, y, width, height ({exc}).",
                )
        elif region is not None and not isinstance(region, str):
            yield ValidationIssue(code="region_shape", step_id=step.id, field="region",
                                  message=f"Step {label!r}: region must be a mapping or a name.")
        color = params.get("color")
        if condition.type == "pixel_matches" and color is not None:
            ok = (isinstance(color, str) and _COLOR_RE.match(color)) or (
                isinstance(color, list) and len(color) == 3
            )
            if not ok:
                yield ValidationIssue(code="color_format", step_id=step.id, field="color",
                                      message=f"Step {label!r}: color must be #rrggbb or [r,g,b].")
        if capabilities is not None:
            missing = [r for r in spec.requires if not capabilities.has(r)]
            if missing:
                yield ValidationIssue(
                    severity="warning", code="unsupported_condition", step_id=step.id,
                    message=f"Step {label!r}: the selected target does not support "
                            f"{', '.join(missing)} (needed by {condition.type}).",
                )

    # -- assets ---------------------------------------------------------------------------

    def _assets(self, document: AuthoringDocument) -> Iterable[ValidationIssue]:
        for image in sorted(document.referenced_images()):
            if "${" in image:
                continue
            if document.asset_by_path(image) is not None:
                continue
            if self._asset_root is not None and (self._asset_root / image).is_file():
                continue
            yield ValidationIssue(
                code="missing_asset", field="image",
                message=f"Image asset {image!r} is not in the project.",
                fix="Select a region on a screenshot to create the asset, or copy the file "
                    "into assets/images.",
            )


def _fix_for(condition_type: str, param: str) -> str:
    if param == "image":
        return "Select an image asset."
    if param == "text":
        return "Enter the text to look for (or pick it from the OCR results)."
    return f"Provide '{param}'."


def validate_document(document: AuthoringDocument, *, asset_root: Path | None = None) -> list[ValidationIssue]:  # noqa: E501
    return DocumentValidator(asset_root=asset_root).validate(document)
