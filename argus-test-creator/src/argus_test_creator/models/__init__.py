"""Domain models (Pydantic). Independent of Qt, Playwright, ADB, and Argus internals."""

from argus_test_creator.models.authoring import (
    AssetReference,
    AuthoringDocument,
    AuthoringWarning,
    ConditionDraft,
    Provenance,
    StepDraft,
    TestMetadata,
    ValidationIssue,
)
from argus_test_creator.models.capabilities import RecorderCapabilities, TargetProfile
from argus_test_creator.models.common import Point, Rect, format_duration, parse_duration
from argus_test_creator.models.recording import (
    NormalizedAction,
    NormalizedActionKind,
    OCRObservation,
    OCRWordObservation,
    RecordingEvent,
    RecordingEventType,
    RecordingMode,
    ScreenCapture,
)

__all__ = [
    "AssetReference",
    "AuthoringDocument",
    "AuthoringWarning",
    "ConditionDraft",
    "NormalizedAction",
    "NormalizedActionKind",
    "OCRObservation",
    "OCRWordObservation",
    "Point",
    "Provenance",
    "Rect",
    "RecorderCapabilities",
    "RecordingEvent",
    "RecordingEventType",
    "RecordingMode",
    "ScreenCapture",
    "StepDraft",
    "TargetProfile",
    "TestMetadata",
    "ValidationIssue",
    "format_duration",
    "parse_duration",
]
