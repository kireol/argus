"""NormalizedAction → StepDraft (Argus actions). The only place that mapping lives."""

from __future__ import annotations

from argus_test_creator.models.authoring import Provenance, StepDraft
from argus_test_creator.models.capabilities import RecorderCapabilities
from argus_test_creator.models.common import format_duration
from argus_test_creator.models.recording import NormalizedAction, NormalizedActionKind

#: Characters that Argus's browser/desktop adapters accept as key names when
#: typed one at a time. Multi-character key names pass through unchanged.
_SPACE_KEY = "SPACE"


def actions_to_steps(
    actions: list[NormalizedAction],
    *,
    session_id: str | None,
    capabilities: RecorderCapabilities | None = None,
) -> tuple[list[StepDraft], list[str]]:
    """Convert actions into Argus steps; returns (steps, warnings)."""
    steps: list[StepDraft] = []
    warnings: list[str] = []
    for action in actions:
        prov = Provenance(
            source="recording", session_id=session_id, event_ids=action.source_event_ids,
            action_id=action.id, capture_id=action.capture_after,
        )
        steps.extend(_convert(action, prov, capabilities, warnings))
    return steps, warnings


def _convert(
    action: NormalizedAction,
    prov: Provenance,
    caps: RecorderCapabilities | None,
    warnings: list[str],
) -> list[StepDraft]:
    p = action.position
    e = action.position_end
    match action.kind:
        case NormalizedActionKind.TAP:
            assert p is not None
            return [StepDraft(action="device.tap", params={"x": p.x, "y": p.y}, provenance=prov)]
        case NormalizedActionKind.DOUBLE_TAP:
            assert p is not None
            second = prov.model_copy(update={"note": "second tap of a double-tap"})
            return [
                StepDraft(action="device.tap", params={"x": p.x, "y": p.y}, provenance=prov,
                          name=f"Double-tap ({p.x}, {p.y})"),
                StepDraft(action="device.tap", params={"x": p.x, "y": p.y}, provenance=second),
            ]
        case NormalizedActionKind.LONG_PRESS:
            assert p is not None
            params: dict[str, object] = {"x": p.x, "y": p.y}
            if action.duration_ms:
                params["duration"] = format_duration(action.duration_ms / 1000)
            return [StepDraft(action="device.long_press", params=params, provenance=prov)]
        case NormalizedActionKind.SWIPE | NormalizedActionKind.DRAG:
            assert p is not None and e is not None
            params = {"from_x": p.x, "from_y": p.y, "to_x": e.x, "to_y": e.y}
            if action.duration_ms:
                params["duration"] = format_duration(action.duration_ms / 1000)
            name = "device.swipe" if action.kind == NormalizedActionKind.SWIPE else "device.drag"
            if caps is not None and name == "device.drag" and not caps.supports_drag:
                name = "device.swipe"
                warnings.append("Drag converted to swipe: the target does not support drag.")
            return [StepDraft(action=name, params=params, provenance=prov)]
        case NormalizedActionKind.MULTI_TOUCH:
            fingers = action.metadata.get("fingers") or []
            if caps is not None and not caps.supports_multi_touch:
                warnings.append("Multi-touch gesture skipped: the target does not support "
                                "device.multi_touch.")
                return [StepDraft(action="log", provenance=prov, name="Multi-touch (unsupported)",
                                  params={"message": f"Recorded {len(fingers)}-finger gesture"})]
            params = {"fingers": [
                {"from_x": path[0]["x"], "from_y": path[0]["y"],
                 "to_x": path[-1]["x"], "to_y": path[-1]["y"]}
                for path in fingers if path
            ]}
            if action.duration_ms:
                params["duration"] = format_duration(action.duration_ms / 1000)
            return [StepDraft(action="device.multi_touch", params=params, provenance=prov,
                              name=f"Multi-touch ({len(fingers)} fingers)")]
        case NormalizedActionKind.KEY:
            return [StepDraft(action="device.key", params={"key": action.key}, provenance=prov)]
        case NormalizedActionKind.TYPE_TEXT:
            text = action.text or ""
            steps = []
            for index, char in enumerate(text):
                key = _SPACE_KEY if char == " " else char
                note = f"character {index + 1} of {text!r}"
                steps.append(StepDraft(
                    action="device.key", params={"key": key},
                    provenance=prov.model_copy(update={"note": note}),
                    name=f"Type {text!r}" if index == 0 else None,
                ))
            return steps
        case NormalizedActionKind.SCROLL:
            assert p is not None
            end = e or p
            params = {"from_x": p.x, "from_y": p.y, "to_x": end.x, "to_y": end.y}
            return [StepDraft(action="device.swipe", params=params, provenance=prov,
                              name="Scroll")]
        case NormalizedActionKind.NAVIGATE:
            return [StepDraft(action="log", params={"message": f"Navigated to {action.text}"},
                              provenance=prov, name=f"Navigate to {action.text}")]
        case NormalizedActionKind.APP_START:
            return [StepDraft(action="device.start", provenance=prov)]
        case NormalizedActionKind.APP_STOP:
            return [StepDraft(action="device.stop", provenance=prov)]
        case NormalizedActionKind.PAUSE:
            return []  # pauses never become fixed waits automatically
    return []
