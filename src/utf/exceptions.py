"""Framework exception hierarchy.

Every exception carries actionable information: what failed, why it likely
failed, and (where possible) how to fix it.
"""

from __future__ import annotations


class UTFError(Exception):
    """Base class for all framework errors."""

    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation

    def __str__(self) -> str:
        if self.remediation:
            return f"{self.message}\nRemediation: {self.remediation}"
        return self.message


class ConfigurationError(UTFError):
    """Invalid, missing, or unresolvable configuration."""


class PreflightError(UTFError):
    """A required pre-flight check failed."""


class DeviceConnectionError(UTFError):
    """Unable to connect to, or communicate with, a device."""


class DeviceCapabilityError(UTFError):
    """The device does not support the requested operation."""


class BackendError(UTFError):
    """Backend API request failed."""


class InstrumentationError(UTFError):
    """Application instrumentation request failed."""


class ScreenshotError(UTFError):
    """Screenshot capture failed."""


class VerificationError(UTFError):
    """A verifier could not evaluate the observation (distinct from a failed verification)."""


class TestDefinitionError(UTFError):
    """A test definition file is invalid."""


class TestExecutionError(UTFError):
    """An unrecoverable error occurred while executing a test."""


class ActionError(UTFError):
    """An action failed to execute."""


class ConditionError(UTFError):
    """A condition could not be evaluated."""


class TimeoutExceededError(UTFError):
    """An operation exceeded its timeout."""


class AssetError(UTFError):
    """A required test asset (e.g. reference image) is missing or invalid."""
