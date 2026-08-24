"""Built-in condition types."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from argus.conditions.base import Condition, ConditionFactory, resolve_region
from argus.exceptions import ConditionError
from argus.models.observation import Observation
from argus.models.results import VerificationResult
from argus.verifiers.base import Expectation, Verifier

if TYPE_CHECKING:
    from argus.engine.context import TestContext


class _VerifierCondition(Condition):
    """Adapts a visual verifier into a condition."""

    needs_observation = True

    def __init__(self, verifier: Verifier, expectation: Expectation) -> None:
        self.name = verifier.name
        self._verifier = verifier
        self._expectation = expectation

    def evaluate(
        self, context: TestContext, observation: Observation | None
    ) -> VerificationResult:
        if observation is None:
            observation = context.observe()
        return self._verifier.verify(observation, self._expectation)


def _expectation_from(params: dict[str, Any], context: TestContext) -> Expectation:
    params = dict(params)
    if "region" in params:
        params["region"] = resolve_region(params["region"], context.config.regions)
    return Expectation.model_validate(params)


def _image_present(params: dict[str, Any], context: TestContext) -> Condition:
    return _VerifierCondition(context.verifiers.image_present, _expectation_from(params, context))


def _image_absent(params: dict[str, Any], context: TestContext) -> Condition:
    return _VerifierCondition(context.verifiers.image_absent, _expectation_from(params, context))


def _screenshot_matches(params: dict[str, Any], context: TestContext) -> Condition:
    return _VerifierCondition(
        context.verifiers.screenshot_match, _expectation_from(params, context)
    )


def _text_present(params: dict[str, Any], context: TestContext) -> Condition:
    return _VerifierCondition(context.verifiers.text_present, _expectation_from(params, context))


def _text_absent(params: dict[str, Any], context: TestContext) -> Condition:
    return _VerifierCondition(context.verifiers.text_absent, _expectation_from(params, context))


class _PixelMatchesCondition(Condition):
    """Checks the color of a single pixel (with tolerance)."""

    name = "pixel_matches"
    needs_observation = True

    def __init__(self, params: dict[str, Any]) -> None:
        try:
            self._x = int(params["x"])
            self._y = int(params["y"])
            color = params["color"]
        except KeyError as exc:
            raise ConditionError(
                f"pixel_matches requires parameter {exc.args[0]!r} (x, y, color)."
            ) from exc
        if isinstance(color, str):
            color = color.lstrip("#")
            self._color = tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))
        else:
            self._color = tuple(int(c) for c in color)
        self._tolerance = int(params.get("tolerance", 10))

    def evaluate(
        self, context: TestContext, observation: Observation | None
    ) -> VerificationResult:
        if observation is None:
            observation = context.observe()
        rgb = observation.image.convert("RGB")
        width, height = rgb.size
        if not (0 <= self._x < width and 0 <= self._y < height):
            raise ConditionError(
                f"Pixel ({self._x}, {self._y}) outside {width}x{height} screenshot."
            )
        actual = rgb.getpixel((self._x, self._y))
        assert isinstance(actual, tuple)
        delta = max(abs(a - e) for a, e in zip(actual, self._color, strict=False))
        passed = delta <= self._tolerance
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            message=(
                f"Pixel ({self._x},{self._y}) is {actual}, expected "
                f"{self._color} ±{self._tolerance} (max delta {delta})"
            ),
            details={"actual": actual, "expected": self._color, "delta": delta},
        )


class _InstrumentationValueCondition(Condition):
    """Compares a value reported by application instrumentation."""

    name = "instrumentation_value"

    def __init__(self, params: dict[str, Any]) -> None:
        self._key = params.get("key") or params.get("field")
        if not self._key:
            raise ConditionError("instrumentation_value requires a 'key' parameter.")
        if "equals" not in params and "contains" not in params:
            raise ConditionError(
                "instrumentation_value requires 'equals' or 'contains'."
            )
        self._equals = params.get("equals")
        self._contains = params.get("contains")

    def evaluate(
        self, context: TestContext, observation: Observation | None
    ) -> VerificationResult:
        assert self._key is not None  # guaranteed by __init__
        status = context.require_instrumentation().status()
        actual = status.get(self._key)
        if self._contains is not None:
            passed = isinstance(actual, str) and str(self._contains) in actual
            expected_desc = f"contains {self._contains!r}"
        else:
            passed = actual == self._equals or str(actual) == str(self._equals)
            expected_desc = f"== {self._equals!r}"
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            message=f"instrumentation.{self._key} is {actual!r} (expected {expected_desc})",
            details={"key": self._key, "actual": actual},
        )


class _ApplicationStateCondition(_InstrumentationValueCondition):
    """Alias of instrumentation_value against the /test/state document."""

    name = "application_state"

    def evaluate(
        self, context: TestContext, observation: Observation | None
    ) -> VerificationResult:
        state = context.require_instrumentation().state()
        actual: Any = state
        assert self._key is not None
        for part in self._key.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        if self._contains is not None:
            passed = isinstance(actual, str) and str(self._contains) in actual
            expected_desc = f"contains {self._contains!r}"
        else:
            passed = actual == self._equals or str(actual) == str(self._equals)
            expected_desc = f"== {self._equals!r}"
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            message=f"application state {self._key} is {actual!r} (expected {expected_desc})",
            details={"key": self._key, "actual": actual},
        )


class _BackendValueCondition(Condition):
    """Compares a value from the backend state endpoint."""

    name = "backend_value"

    def __init__(self, params: dict[str, Any]) -> None:
        self._key = params.get("key")
        if not self._key:
            raise ConditionError("backend_value requires a 'key' parameter.")
        if "equals" not in params:
            raise ConditionError("backend_value requires an 'equals' parameter.")
        self._equals = params["equals"]
        self._endpoint = params.get("endpoint")

    def evaluate(
        self, context: TestContext, observation: Observation | None
    ) -> VerificationResult:
        assert self._key is not None  # guaranteed by __init__
        state = context.require_backend().get_state(self._endpoint)
        actual: Any = state
        for part in self._key.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        passed = actual == self._equals or str(actual) == str(self._equals)
        return VerificationResult(
            passed=passed,
            verifier=self.name,
            message=f"backend {self._key} is {actual!r} (expected {self._equals!r})",
            details={"key": self._key, "actual": actual},
        )


class _LogContainsCondition(Condition):
    """True when recent device logs contain a substring or regex match.

    Reads ``Device.get_logs(lines)`` on every evaluation, so it works in
    ``wait_until`` poll loops. Negate with ``not:`` composition.
    """

    name = "log_contains"

    def __init__(self, params: dict[str, Any]) -> None:
        text = params.get("text")
        pattern = params.get("pattern")
        if (text is None) == (pattern is None):
            raise ConditionError(
                "log_contains requires exactly one of 'text' or 'pattern'.",
                remediation="Use 'text' for a literal substring or 'pattern' for a regex.",
            )
        self._case_sensitive = bool(params.get("case_sensitive", True))
        self._lines = int(params.get("lines", 200))
        flags = re.MULTILINE if self._case_sensitive else re.MULTILINE | re.IGNORECASE
        source = str(pattern) if pattern is not None else re.escape(str(text))
        self._describe = (
            f"pattern {pattern!r}" if pattern is not None else f"text {text!r}"
        )
        try:
            self._regex = re.compile(source, flags)
        except re.error as exc:
            raise ConditionError(
                f"Invalid regex for log_contains: {pattern!r} ({exc}).",
                remediation=(
                    "Check the 'pattern' value is a valid Python regular expression."
                ),
            ) from exc

    def evaluate(
        self, context: TestContext, observation: Observation | None
    ) -> VerificationResult:
        device = context.require_device()
        if not device.capabilities.supports_logs:
            raise ConditionError(
                f"Device {device.name!r} does not support logs; log_contains cannot run.",
                remediation=(
                    "Use a device type with log support (android, yocto with "
                    "log_command, browser, fake)."
                ),
            )
        logs = device.get_logs(self._lines)
        match = self._regex.search(logs)
        lines_scanned = len(logs.splitlines())
        if match is not None:
            return VerificationResult(
                passed=True,
                verifier=self.name,
                message=(
                    f"Logs contain {self._describe} (matched {match.group(0)!r})"
                ),
                details={"match": match.group(0), "lines_scanned": lines_scanned},
            )
        return VerificationResult(
            passed=False,
            verifier=self.name,
            message=f"Logs do not contain {self._describe} in last {self._lines} lines",
            details={"match": None, "lines_scanned": lines_scanned},
        )


def register(factory: ConditionFactory) -> None:
    factory.register("image_present", _image_present)
    factory.register("image_not_present", _image_absent)
    factory.register("screenshot_matches", _screenshot_matches)
    factory.register("text_present", _text_present)
    factory.register("text_not_present", _text_absent)
    factory.register("pixel_matches", lambda p, c: _PixelMatchesCondition(p))
    factory.register("instrumentation_value", lambda p, c: _InstrumentationValueCondition(p))
    factory.register("application_state", lambda p, c: _ApplicationStateCondition(p))
    factory.register("backend_value", lambda p, c: _BackendValueCondition(p))
    factory.register("log_contains", lambda p, c: _LogContainsCondition(p))
