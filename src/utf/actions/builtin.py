"""Built-in actions."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from utf.actions.base import Action, ActionRegistry, ActionResult
from utf.exceptions import (
    BackendError,
    DeviceCapabilityError,
    DeviceConnectionError,
    ScreenshotError,
)
from utf.models.test_definition import ConditionSpec
from utf.utilities.duration import parse_duration

if TYPE_CHECKING:
    from utf.engine.context import TestContext


# -- backend actions ----------------------------------------------------------------


class _BackendRequestAction(Action):
    def __init__(self, name: str, method: str) -> None:
        self.name = name
        self._method = method

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        endpoint = self.require_param(params, "endpoint")
        kwargs: dict[str, Any] = {}
        if "data" in params:
            kwargs["json"] = params["data"]
        if "params" in params:
            kwargs["params"] = params["params"]
        try:
            response = context.require_backend().request(self._method, endpoint, **kwargs)
        except BackendError as exc:
            return ActionResult.failed(str(exc), category="backend")
        expected_status = params.get("expect_status")
        if expected_status is not None and response.status_code != int(expected_status):
            return ActionResult.failed(
                f"{self._method} {endpoint} returned {response.status_code}, "
                f"expected {expected_status}",
                category="backend",
                status_code=response.status_code,
            )
        if expected_status is None and not response.is_success:
            return ActionResult.failed(
                f"{self._method} {endpoint} returned {response.status_code}",
                category="backend",
                status_code=response.status_code,
                body=response.text[:500],
            )
        return ActionResult.ok(
            f"{self._method} {endpoint} -> {response.status_code}",
            status_code=response.status_code,
        )


class _BackendSetAction(Action):
    """``backend.set`` — set backend state (the canonical state-driving step)."""

    name = "backend.set"

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        data = self.require_param(params, "data")
        endpoint = params.get("endpoint")
        try:
            context.require_backend().set_state(data, endpoint)
        except BackendError as exc:
            return ActionResult.failed(str(exc), category="backend")
        return ActionResult.ok(f"Backend state set: {data}")


# -- device actions --------------------------------------------------------------------


class _DeviceLifecycleAction(Action):
    def __init__(self, name: str, operation: str) -> None:
        self.name = name
        self._operation = operation

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        device = context.require_device()
        try:
            getattr(device, self._operation)()
        except (DeviceConnectionError, DeviceCapabilityError) as exc:
            return ActionResult.failed(str(exc), category="device_connection")
        return ActionResult.ok(f"{self._operation} on {device.name}")


class _DeviceTapAction(Action):
    name = "device.tap"

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        x = int(self.require_param(params, "x"))
        y = int(self.require_param(params, "y"))
        device = context.require_device()
        try:
            device.tap(x, y)
        except (DeviceConnectionError, DeviceCapabilityError) as exc:
            return ActionResult.failed(str(exc), category="device_connection")
        return ActionResult.ok(f"tap({x}, {y}) on {device.name}")


class _DeviceSwipeAction(Action):
    name = "device.swipe"

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        x1 = int(self.require_param(params, "from_x"))
        y1 = int(self.require_param(params, "from_y"))
        x2 = int(self.require_param(params, "to_x"))
        y2 = int(self.require_param(params, "to_y"))
        duration_ms = int(parse_duration(params.get("duration", "300ms")) * 1000)
        device = context.require_device()
        try:
            device.swipe(x1, y1, x2, y2, duration_ms)
        except (DeviceConnectionError, DeviceCapabilityError) as exc:
            return ActionResult.failed(str(exc), category="device_connection")
        return ActionResult.ok(f"swipe({x1},{y1} -> {x2},{y2}) on {device.name}")


class _DeviceKeyAction(Action):
    name = "device.key"

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        key = str(self.require_param(params, "key"))
        device = context.require_device()
        try:
            device.press_key(key)
        except (DeviceConnectionError, DeviceCapabilityError) as exc:
            return ActionResult.failed(str(exc), category="device_connection")
        return ActionResult.ok(f"key({key}) on {device.name}")


# -- synchronization ----------------------------------------------------------------------


class _WaitAction(Action):
    """Explicit fixed delay. Discouraged — prefer wait_until."""

    name = "wait"

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        seconds = parse_duration(self.require_param(params, "duration"))
        context.logger.warning(
            "Fixed 'wait' of %.2fs used — prefer 'wait_until' with a condition", seconds
        )
        time.sleep(seconds)
        return ActionResult.ok(f"waited {seconds:.2f}s")


class _WaitUntilAction(Action):
    name = "wait_until"

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        from utf.engine.wait import wait_until

        spec = ConditionSpec.model_validate(self.require_param(params, "condition"))
        condition = context.conditions.build(spec, context)
        timeout = parse_duration(params.get("timeout", context.config.wait.default_timeout))
        poll = parse_duration(
            params.get("poll_interval", context.config.wait.default_poll_interval)
        )
        try:
            outcome = wait_until(context, condition, timeout=timeout, poll_interval=poll)
        except ScreenshotError as exc:
            return ActionResult.failed(str(exc), category="screenshot")
        if outcome.passed:
            return ActionResult(
                passed=True, message=outcome.message, verification=outcome.last_result
            )
        return ActionResult.failed(
            outcome.message,
            category="timeout",
            verification=outcome.last_result,
            attempts=outcome.attempts,
        )


class _VerifyAction(Action):
    """Evaluate a condition once; the assertion step of a test."""

    name = "verify"

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        spec = ConditionSpec.model_validate(self.require_param(params, "condition"))
        condition = context.conditions.build(spec, context)
        observation = context.observe() if condition.needs_observation else None
        result = condition.evaluate(context, observation)
        if result.passed:
            return ActionResult(passed=True, message=result.message, verification=result)
        return ActionResult.failed(
            result.message, category="assertion", verification=result
        )


# -- miscellaneous ----------------------------------------------------------------------------


class _ScreenshotAction(Action):
    name = "screenshot"

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        try:
            observation = context.observe()
        except ScreenshotError as exc:
            return ActionResult.failed(str(exc), category="screenshot")
        filename = str(params.get("file", "screenshot.png"))
        if not filename.endswith(".png"):
            filename += ".png"
        path = context.artifacts.save_image(filename, observation.image)
        return ActionResult.ok(f"screenshot saved: {path}", path=str(path))


class _LogAction(Action):
    name = "log"

    def execute(self, context: TestContext, params: dict[str, Any]) -> ActionResult:
        message = str(self.require_param(params, "message"))
        context.logger.info(message)
        return ActionResult.ok(message)


def register(registry: ActionRegistry) -> None:
    registry.register(_BackendRequestAction("backend.get", "GET"))
    registry.register(_BackendRequestAction("backend.post", "POST"))
    registry.register(_BackendRequestAction("backend.put", "PUT"))
    registry.register(_BackendRequestAction("backend.patch", "PATCH"))
    registry.register(_BackendRequestAction("backend.delete", "DELETE"))
    registry.register(_BackendSetAction())
    registry.register(_DeviceLifecycleAction("device.start", "start_application"))
    registry.register(_DeviceLifecycleAction("device.stop", "stop_application"))
    registry.register(_DeviceLifecycleAction("device.restart", "restart_application"))
    registry.register(_DeviceLifecycleAction("device.reset", "reset_application"))
    registry.register(_DeviceTapAction())
    registry.register(_DeviceSwipeAction())
    registry.register(_DeviceKeyAction())
    registry.register(_WaitAction())
    registry.register(_WaitUntilAction())
    registry.register(_VerifyAction())
    registry.register(_ScreenshotAction())
    registry.register(_LogAction())
