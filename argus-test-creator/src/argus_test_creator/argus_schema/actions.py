"""Argus built-in actions and their parameters (argus.actions.builtin, v1.1.x)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str  # int | str | duration | mapping | list | any | bool | color
    required: bool = False
    default: object = None
    help: str = ""


@dataclass(frozen=True)
class ActionSpec:
    name: str
    label: str
    params: tuple[ParamSpec, ...] = ()
    #: RecorderCapabilities flags the target must have (any missing → warning).
    requires: tuple[str, ...] = ()
    category: str = "device"
    help: str = ""
    #: The Creator asks for explicit confirmation before authoring these.
    dangerous: bool = False

    @property
    def required_params(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.required)

    def param(self, name: str) -> ParamSpec | None:
        for p in self.params:
            if p.name == name:
                return p
        return None


def _p(name: str, type_: str, required: bool = False, default: object = None, help_: str = "") -> ParamSpec:  # noqa: E501
    return ParamSpec(name, type_, required, default, help_)


_XY = (_p("x", "int", True), _p("y", "int", True))
_FROM_TO = (
    _p("from_x", "int", True),
    _p("from_y", "int", True),
    _p("to_x", "int", True),
    _p("to_y", "int", True),
)

ACTIONS: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in (
        ActionSpec("device.tap", "Tap", _XY, ("tap",), help="Tap/click at screen pixel."),
        ActionSpec(
            "device.swipe",
            "Swipe",
            (*_FROM_TO, _p("duration", "duration", default="300ms")),
            ("swipe",),
        ),
        ActionSpec(
            "device.long_press",
            "Long press",
            (*_XY, _p("duration", "duration", default="1s")),
            ("long_press",),
        ),
        ActionSpec(
            "device.drag",
            "Drag",
            (
                *_FROM_TO,
                _p("hold", "duration", default="500ms"),
                _p("duration", "duration", default="500ms"),
            ),
            ("drag",),
        ),
        ActionSpec(
            "device.pinch",
            "Pinch",
            (
                *_XY,
                _p("from_distance", "int", True),
                _p("to_distance", "int", True),
                _p("duration", "duration", default="500ms"),
            ),
            ("pinch",),
        ),
        ActionSpec(
            "device.multi_touch",
            "Multi-touch",
            (_p("fingers", "list", True), _p("duration", "duration", default="500ms")),
            ("multi_touch",),
        ),
        ActionSpec(
            "device.key",
            "Key press",
            (_p("key", "str", True, help_="e.g. ENTER, BACK, DPAD_UP, a, Ctrl+s"),),
            ("keyboard",),
        ),
        ActionSpec("device.start", "Start application", (), ("app_lifecycle",), "lifecycle"),
        ActionSpec("device.stop", "Stop application", (), ("app_lifecycle",), "lifecycle"),
        ActionSpec("device.restart", "Restart application", (), ("app_lifecycle",), "lifecycle"),
        ActionSpec("device.reset", "Reset application", (), ("app_lifecycle",), "lifecycle"),
        ActionSpec(
            "wait_until",
            "Wait until",
            (
                _p("condition", "mapping", True),
                _p("timeout", "duration", default="10s"),
                _p("poll_interval", "duration", default="250ms"),
            ),
            (),
            "sync",
            "Poll a condition — the synchronization tool.",
        ),
        ActionSpec(
            "verify", "Verify", (_p("condition", "mapping", True),), (), "sync",
            "Evaluate a condition once; fails the test if false.",
        ),
        ActionSpec(
            "wait", "Fixed wait", (_p("duration", "duration", True),), (), "sync",
            "Fixed sleep — discouraged; prefer wait_until.",
        ),
        ActionSpec("screenshot", "Screenshot", (_p("file", "str", default="screenshot.png"),),
                   ("screenshot",), "misc"),
        ActionSpec("log", "Log message", (_p("message", "str", True),), (), "misc"),
        ActionSpec(
            "shell.run",
            "Run host command",
            (
                _p("command", "str", True),
                _p("args", "list"),
                _p("timeout", "duration", default="60s"),
                _p("cwd", "str"),
                _p("expect_exit", "int", default=0),
            ),
            (),
            "misc",
            dangerous=True,
        ),
        ActionSpec(
            "backend.set", "Set backend state",
            (_p("data", "mapping", True), _p("endpoint", "str")), ("backend",), "backend",
        ),
        *(
            ActionSpec(
                f"backend.{verb}",
                f"Backend {verb.upper()}",
                (
                    _p("endpoint", "str", True),
                    _p("data", "any"),
                    _p("params", "mapping"),
                    _p("expect_status", "int"),
                ),
                ("backend",),
                "backend",
            )
            for verb in ("get", "post", "put", "patch", "delete")
        ),
    )
}

#: Actions the wizard offers first (the rest live under "More…").
PRIMARY_ACTIONS: tuple[str, ...] = (
    "device.tap", "device.key", "device.swipe", "device.long_press", "device.drag",
    "wait_until", "verify", "device.start", "device.stop", "screenshot", "log",
)


def action_spec(name: str) -> ActionSpec | None:
    return ACTIONS.get(name)


def known_action(name: str) -> bool:
    return name in ACTIONS


def actions_for(capabilities: object) -> list[ActionSpec]:
    """Actions whose capability requirements the given RecorderCapabilities satisfy."""
    out: list[ActionSpec] = []
    for spec in ACTIONS.values():
        if all(getattr(capabilities, f"supports_{req}", False) for req in spec.requires):
            out.append(spec)
    return out


@dataclass(frozen=True)
class _Empty:
    pass


__all__ = ["ACTIONS", "PRIMARY_ACTIONS", "ActionSpec", "ParamSpec", "action_spec",
           "actions_for", "known_action"]
_ = field  # keep dataclasses.field import for future specs
