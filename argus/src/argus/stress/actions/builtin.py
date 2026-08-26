"""Built-in stress action types.

Each class is one focused component. Coordinates come from the
:class:`~argus.stress.targets.TargetSelector`; text from the scenario's typing
configuration; every random draw from ``context.rng``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from argus.stress.actions.base import StressActionRegistry, StressActionType
from argus.stress.models import StressAction, Target, TargetKind

if TYPE_CHECKING:
    from argus.stress.context import StressContext
    from argus.stress.targets import TargetSelector

_UNICODE_SAMPLES = ("héllo", "日本語", "😀", "Ünïcödé", "مرحبا", "𝒯𝑒𝓈𝓉")
_SPECIAL_SAMPLES = ("<script>", "' OR 1=1 --", "%s%n", "a\tb", "\\x00", "{{7*7}}", "\"quoted\"")


class _TargetedAction(StressActionType):
    targeted = True

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        target = targets.pick(context, self.name)
        return StressAction(action_type=self.name, target=target, parameters=dict(params))

    def _point(self, action: StressAction) -> tuple[int, int]:
        assert action.target is not None
        return action.target.x, action.target.y


class TapAction(_TargetedAction):
    name = "tap"
    requires = ("tap",)

    def perform(self, context: StressContext, action: StressAction) -> None:
        x, y = self._point(action)
        context.require_device().tap(x, y)


class DoubleTapAction(_TargetedAction):
    name = "double_tap"
    requires = ("tap",)

    def perform(self, context: StressContext, action: StressAction) -> None:
        x, y = self._point(action)
        device = context.require_device()
        device.tap(x, y)
        device.tap(x, y)


class LongPressAction(_TargetedAction):
    name = "long_press"
    requires = ("long_press",)

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        target = targets.pick(context, self.name)
        duration = int(params.get("duration_ms") or context.rng.randint(600, 1500))
        return StressAction(action_type=self.name, target=target,
                            parameters={"duration_ms": duration})

    def perform(self, context: StressContext, action: StressAction) -> None:
        x, y = self._point(action)
        context.require_device().long_press(x, y, int(action.parameters["duration_ms"]))


class SwipeAction(StressActionType):
    """A directional fling from a target toward a screen edge."""

    name = "swipe"
    requires = ("swipe",)
    targeted = True
    _DIRECTIONS: tuple[str, ...] = ("up", "down", "left", "right")

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        start = targets.pick(context, self.name)
        direction = str(params.get("direction") or context.rng.choice(self._DIRECTIONS))
        width, height = context.screen_size()
        span = context.rng.uniform(0.2, 0.6)
        dx = {"left": -width, "right": width}.get(direction, 0) * span
        dy = {"up": -height, "down": height}.get(direction, 0) * span
        end_x = min(max(int(start.x + dx), 0), width - 1)
        end_y = min(max(int(start.y + dy), 0), height - 1)
        duration = int(params.get("duration_ms") or context.rng.randint(120, 500))
        return StressAction(
            action_type=self.name, target=start,
            parameters={"direction": direction, "to_x": end_x, "to_y": end_y,
                        "duration_ms": duration},
        )

    def perform(self, context: StressContext, action: StressAction) -> None:
        assert action.target is not None
        p = action.parameters
        context.require_device().swipe(action.target.x, action.target.y, int(p["to_x"]),
                                       int(p["to_y"]), int(p["duration_ms"]))


class ScrollAction(SwipeAction):
    """A slower vertical swipe from the screen centre (list scrolling)."""

    name = "scroll"
    _DIRECTIONS = ("up", "down")

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        width, height = context.screen_size()
        centre = Target(x=width // 2, y=height // 2, kind=TargetKind.COORDINATE, label="centre")
        direction = str(params.get("direction") or context.rng.choice(self._DIRECTIONS))
        span = context.rng.uniform(0.25, 0.5)
        dy = int((-height if direction == "up" else height) * span)
        end_y = min(max(centre.y + dy, 0), height - 1)
        return StressAction(
            action_type=self.name, target=centre,
            parameters={"direction": direction, "to_x": centre.x, "to_y": end_y,
                        "duration_ms": int(params.get("duration_ms") or 400)},
        )


class _KeyAction(StressActionType):
    key = ""
    requires = ("keyboard",)

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        return StressAction(action_type=self.name, parameters={"key": self.key})

    def perform(self, context: StressContext, action: StressAction) -> None:
        context.require_device().press_key(str(action.parameters["key"]))


class BackAction(_KeyAction):
    name = "back"
    key = "BACK"


class HomeAction(_KeyAction):
    name = "home"
    key = "HOME"
    safe = False  # leaves the application


class MenuAction(_KeyAction):
    name = "menu"
    key = "MENU"


class EnterAction(_KeyAction):
    name = "enter"
    key = "ENTER"
    expects_change = False


class TypeTextAction(StressActionType):
    """Type a word (from the scenario list, or generated unicode/special/long text)."""

    name = "type_text"
    requires = ("keyboard",)
    expects_change = False

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        typing = context.config.monkey.typing
        rng = context.rng
        flavour = rng.weighted_choice(
            ["word", "unicode", "special", "long", "digits"],
            [6, 2 if typing.allow_unicode else 0, 2 if typing.allow_special else 0, 1, 1],
        )
        if flavour == "word" and typing.words:
            text = rng.choice(typing.words)
        elif flavour == "unicode":
            text = rng.choice(_UNICODE_SAMPLES)
        elif flavour == "special":
            text = rng.choice(_SPECIAL_SAMPLES)
        elif flavour == "long":
            text = rng.token(typing.max_length)
        else:
            text = str(rng.randint(0, 10**6))
        text = text[: typing.max_length]
        return StressAction(action_type=self.name, parameters={"text": text, "flavour": flavour})

    def perform(self, context: StressContext, action: StressAction) -> None:
        device = context.require_device()
        text = str(action.parameters["text"])
        typer = getattr(device, "type_text", None)
        if callable(typer):
            typer(text)
            return
        for char in text:
            device.press_key("SPACE" if char == " " else char)


class ClearTextAction(StressActionType):
    """Select-all + delete where the device offers it, else repeated backspaces."""

    name = "clear_text"
    requires = ("keyboard",)
    expects_change = False

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        return StressAction(action_type=self.name,
                            parameters={"backspaces": int(params.get("backspaces", 12))})

    def perform(self, context: StressContext, action: StressAction) -> None:
        device = context.require_device()
        clear = getattr(device, "clear_text", None)
        if callable(clear):
            clear()
            return
        for _ in range(int(action.parameters["backspaces"])):
            device.press_key("BACKSPACE")


class _LifecycleAction(StressActionType):
    requires = ("app_lifecycle",)
    operation = ""

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        return StressAction(action_type=self.name, parameters={"operation": self.operation})

    def perform(self, context: StressContext, action: StressAction) -> None:
        getattr(context.require_device(), self.operation)()


class RestartAction(_LifecycleAction):
    name = "restart"
    operation = "restart_application"
    safe = False


class ReloadAction(StressActionType):
    """``reload`` where the adapter offers it (browsers), else a full restart."""

    name = "reload"
    requires = ("app_lifecycle",)

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        return StressAction(action_type=self.name)

    def perform(self, context: StressContext, action: StressAction) -> None:
        device = context.require_device()
        reload = getattr(device, "reload", None)
        if callable(reload):
            reload()
        else:
            device.restart_application()


class BackgroundAction(StressActionType):
    """Send the app to the background (adapter method, else HOME key)."""

    name = "background"
    requires = ("keyboard",)
    safe = False

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        return StressAction(action_type=self.name)

    def perform(self, context: StressContext, action: StressAction) -> None:
        device = context.require_device()
        background = getattr(device, "background_application", None)
        if callable(background):
            background()
        else:
            device.press_key("HOME")


class ForegroundAction(StressActionType):
    """Bring the app back (adapter method, else start_application)."""

    name = "foreground"
    requires = ("app_lifecycle",)

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        return StressAction(action_type=self.name)

    def perform(self, context: StressContext, action: StressAction) -> None:
        device = context.require_device()
        foreground = getattr(device, "foreground_application", None)
        if callable(foreground):
            foreground()
        else:
            device.start_application()


class RotateAction(StressActionType):
    name = "rotate"
    requires = ("rotate",)
    _ORIENTATIONS = ("portrait", "landscape")

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        return StressAction(action_type=self.name,
                            parameters={"orientation": context.rng.choice(self._ORIENTATIONS)})

    def perform(self, context: StressContext, action: StressAction) -> None:
        rotate = getattr(context.require_device(), "rotate", None)
        if not callable(rotate):
            raise self._unsupported(context)
        rotate(str(action.parameters["orientation"]))

    @staticmethod
    def _unsupported(context: StressContext) -> Exception:
        from argus.exceptions import DeviceCapabilityError

        return DeviceCapabilityError(f"Device {context.device_name!r} cannot rotate.")


class WaitAction(StressActionType):
    """An explicit idle period (lets timers, animations and mutations land)."""

    name = "wait"
    requires = ()
    expects_change = False

    def generate(self, context: StressContext, targets: TargetSelector,
                 params: dict[str, Any]) -> StressAction | None:
        seconds = float(params.get("seconds") or round(context.rng.uniform(0.2, 1.5), 3))
        return StressAction(action_type=self.name, parameters={"seconds": seconds})

    def perform(self, context: StressContext, action: StressAction) -> None:
        context.sleep(float(action.parameters["seconds"]))


BUILTIN_ACTIONS: tuple[type[StressActionType], ...] = (
    TapAction, DoubleTapAction, LongPressAction, SwipeAction, ScrollAction, BackAction,
    HomeAction, MenuAction, EnterAction, TypeTextAction, ClearTextAction, RestartAction,
    ReloadAction, BackgroundAction, ForegroundAction, RotateAction, WaitAction,
)


def register(registry: StressActionRegistry) -> None:
    for cls in BUILTIN_ACTIONS:
        registry.register(cls())


__all__ = ["BUILTIN_ACTIONS", "register"]
