"""Argus built-in condition types (argus.conditions.builtin, v1.1.x)."""

from __future__ import annotations

from dataclasses import dataclass

from argus_test_creator.argus_schema.actions import ParamSpec


@dataclass(frozen=True)
class ConditionSpec:
    name: str
    label: str
    params: tuple[ParamSpec, ...] = ()
    requires: tuple[str, ...] = ()
    #: The condition inspects a screenshot (the Creator can author it visually).
    visual: bool = False
    negated_form: str | None = None
    help: str = ""

    @property
    def required_params(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.required)

    #: Parameters where at least one of the group must be present.
    one_of: tuple[tuple[str, ...], ...] = ()


def _p(name: str, type_: str, required: bool = False, default: object = None, help_: str = "") -> ParamSpec:  # noqa: E501
    return ParamSpec(name, type_, required, default, help_)


_IMAGE_PARAMS = (
    _p("image", "str", True, help_="Reference image (relative to asset paths)"),
    _p("threshold", "float", default=0.90),
    _p("region", "region"),
    _p("grayscale", "bool"),
    _p("scale_tolerance", "float"),
    _p("mask_background", "bool"),
    _p("mask_luminance", "int"),
)
_TEXT_PARAMS = (
    _p("text", "str", True),
    _p("region", "region"),
    _p("case_sensitive", "bool", default=False),
)
_KEY_VALUE = (_p("key", "str", True), _p("equals", "any"), _p("contains", "str"))

CONDITIONS: dict[str, ConditionSpec] = {
    spec.name: spec
    for spec in (
        ConditionSpec("text_present", "Text is visible", _TEXT_PARAMS, ("ocr",), True,
                      "text_not_present", "OCR finds the text on screen."),
        ConditionSpec("text_not_present", "Text is NOT visible", _TEXT_PARAMS, ("ocr",), True,
                      "text_present"),
        ConditionSpec("image_present", "Image is visible", _IMAGE_PARAMS, ("screenshot",), True,
                      "image_not_present", "Template match of a reference image."),
        ConditionSpec("image_not_present", "Image is NOT visible", _IMAGE_PARAMS,
                      ("screenshot",), True, "image_present"),
        ConditionSpec("screenshot_matches", "Screen/region matches reference", _IMAGE_PARAMS,
                      ("screenshot",), True, None,
                      "Whole-screen (or region) comparison against a reference image."),
        ConditionSpec(
            "pixel_matches", "Pixel matches color",
            (_p("x", "int", True), _p("y", "int", True), _p("color", "color", True),
             _p("tolerance", "int", default=10)),
            ("screenshot",), True,
        ),
        ConditionSpec("instrumentation_value", "Application status value", _KEY_VALUE,
                      ("instrumentation",), one_of=(("equals", "contains"),)),
        ConditionSpec("application_state", "Application state value", _KEY_VALUE,
                      ("instrumentation",), one_of=(("equals", "contains"),)),
        ConditionSpec("backend_value", "Backend value",
                      (_p("key", "str", True), _p("equals", "any", True), _p("endpoint", "str")),
                      ("backend",)),
        ConditionSpec(
            "log_contains", "Log contains",
            (_p("text", "str"), _p("pattern", "str"), _p("lines", "int", default=200),
             _p("case_sensitive", "bool", default=True)),
            ("logs",), one_of=(("text", "pattern"),),
        ),
        ConditionSpec(
            "now_playing", "Media is playing",
            (_p("state", "str"), _p("title", "str"), _p("app_id", "str"),
             _p("position_advancing", "bool"), _p("interval", "float", default=1.0)),
            ("playback_state",), one_of=(("state", "title", "app_id", "position_advancing"),),
        ),
    )
}

COMPOSITE_FORMS = ("all", "any", "not")


def condition_spec(name: str) -> ConditionSpec | None:
    return CONDITIONS.get(name)


def conditions_for(capabilities: object) -> list[ConditionSpec]:
    return [
        spec
        for spec in CONDITIONS.values()
        if all(getattr(capabilities, f"supports_{req}", False) for req in spec.requires)
    ]
