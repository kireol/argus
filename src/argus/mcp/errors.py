"""Error mapping between Argus and MCP.

Every anticipated failure reaches the model as an ``is_error`` tool result
carrying both a readable message and a structured ``ErrorInfo`` — the Argus
exception class, its failure category, the remediation hint the exception
already carries, and whether retrying is sensible. Unexpected exceptions are
deliberately *not* mapped: the SDK logs their traceback server-side and the
client sees only a generic message, so no internals leak.
"""

from __future__ import annotations

import functools
import inspect
import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from argus.exceptions import ConfigurationError, UTFError
from argus.logging import get_logger
from argus.service.runs import RunConflictError

_F = TypeVar("_F", bound=Callable[..., Any])

_CATEGORIES: dict[str, tuple[str, bool]] = {
    # exception class -> (category, retryable)
    "ConfigurationError": ("configuration", False),
    "PreflightError": ("preflight", False),
    "DeviceConnectionError": ("device_connection", True),
    "DeviceCapabilityError": ("device_capability", False),
    "BackendError": ("backend", True),
    "InstrumentationError": ("instrumentation", True),
    "ScreenshotError": ("screenshot", True),
    "VerificationError": ("verification", False),
    "TestDefinitionError": ("test_definition", False),
    "TestExecutionError": ("test_execution", False),
    "ActionError": ("action", False),
    "ConditionError": ("condition", False),
    "TimeoutExceededError": ("timeout", True),
    "AssetError": ("asset", False),
    "RunConflictError": ("busy", True),
    "InvalidArgumentError": ("invalid_argument", False),
}


class InvalidArgumentError(UTFError):
    """A tool argument passed schema validation but is semantically invalid."""


class ErrorInfo(BaseModel):
    """Structured error payload attached to ``is_error`` tool results."""

    type: str = Field(description="Argus exception class name.")
    category: str = Field(description="Stable failure category.")
    message: str
    remediation: str | None = None
    retryable: bool = Field(description="Whether retrying the same call may succeed.")
    operation: str = Field(description="Tool that failed.")


def error_info(exc: UTFError, operation: str) -> ErrorInfo:
    name = type(exc).__name__
    category, retryable = _CATEGORIES.get(name, ("error", False))
    return ErrorInfo(
        type=name,
        category=category,
        message=exc.message,
        remediation=exc.remediation,
        retryable=retryable,
        operation=operation,
    )


def error_result(exc: UTFError, operation: str) -> Any:
    """Build the ``CallToolResult`` for an anticipated failure."""
    from mcp.types import CallToolResult, TextContent

    info = error_info(exc, operation)
    lines = [f"{info.type}: {info.message}"]
    if info.remediation:
        lines.append(f"Remediation: {info.remediation}")
    lines.append(f"Retryable: {'yes' if info.retryable else 'no'}")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structured_content={"error": info.model_dump(mode="json")},
        is_error=True,
    )


def guarded(operation: str) -> Callable[[_F], _F]:
    """Decorate a tool function: map Argus errors, log operation and outcome.

    The wrapper keeps the original signature (via ``functools.wraps``) so the
    SDK still derives the input/output schemas from the tool itself.
    """

    log = get_logger("argus.mcp")

    def decorate(fn: _F) -> _F:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.monotonic()
                try:
                    result = await fn(*args, **kwargs)
                except (UTFError, RunConflictError) as exc:
                    _log_outcome(log, operation, started, "error", exc)
                    return error_result(exc, operation)
                _log_outcome(log, operation, started, "ok")
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                result = fn(*args, **kwargs)
            except (UTFError, RunConflictError) as exc:
                _log_outcome(log, operation, started, "error", exc)
                return error_result(exc, operation)
            _log_outcome(log, operation, started, "ok")
            return result

        return wrapper  # type: ignore[return-value]

    return decorate


def _log_outcome(
    log: Any, operation: str, started: float, outcome: str, exc: Exception | None = None
) -> None:
    duration_ms = round((time.monotonic() - started) * 1000)
    extra = {"tool": operation, "operation": "mcp.tool"}
    if exc is None:
        log.info("%s ok in %dms", operation, duration_ms, extra=extra)
    else:
        log.info("%s failed in %dms: %s", operation, duration_ms, exc, extra=extra)


def require_sdk() -> None:
    """Raise a remediated ConfigurationError when the MCP SDK is not installed."""
    try:
        import mcp.server  # noqa: F401
    except ImportError as exc:
        raise ConfigurationError(
            "MCP support requires the optional 'mcp' package.",
            remediation='Install it with: pip install "argus[mcp]"',
        ) from exc


__all__ = [
    "ErrorInfo",
    "InvalidArgumentError",
    "error_info",
    "error_result",
    "guarded",
    "require_sdk",
]
