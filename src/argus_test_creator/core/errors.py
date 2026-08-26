"""Structured error hierarchy.

Every Creator error carries a user-facing message plus an optional
``remediation`` so the UI can show "what to do next" without exposing stack
traces. Low-level exceptions (Playwright, ADB, OS errors) are wrapped at the
adapter/integration boundary, never leaked to the UI.
"""

from __future__ import annotations


class CreatorError(Exception):
    """Base class for all Creator errors."""

    def __init__(
        self,
        message: str,
        *,
        remediation: str | None = None,
        details: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation
        self.details = details

    def __str__(self) -> str:
        text = self.message
        if self.remediation:
            text += f"\nSuggested action: {self.remediation}"
        return text


class TargetConnectionError(CreatorError):
    """The recording target could not be reached."""


class ScreenshotError(CreatorError):
    """Screen capture failed."""


class RecordingError(CreatorError):
    """Recording could not start, continue, or stop."""


class OCRProviderError(CreatorError):
    """The OCR provider is unavailable or failed."""


class AssetError(CreatorError):
    """An image asset could not be created, resolved, or promoted."""


class SerializationError(CreatorError):
    """The document could not be converted to or from YAML."""


class ArgusIntegrationError(CreatorError):
    """Argus could not be located or invoked."""


class ValidationFailed(CreatorError):
    """A document failed validation (raised only when a caller demands validity)."""


class UnsupportedCapabilityError(CreatorError):
    """The selected target does not support the requested capability."""


class ProjectError(CreatorError):
    """The Creator project on disk is missing or malformed."""


class WorkerError(CreatorError):
    """A background job failed or was cancelled."""
