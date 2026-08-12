"""Event system for decoupled progress reporting (console, JSON, future GUI)."""

from argus.events.bus import EventBus
from argus.events.events import (
    ActionCompleted,
    ActionStarted,
    Event,
    PreflightCheckCompleted,
    PreflightCompleted,
    PreflightStarted,
    TestFailed,
    TestPassed,
    TestRunCompleted,
    TestRunStarted,
    TestSkipped,
    TestStarted,
    VerificationCompleted,
    VerificationStarted,
)

__all__ = [
    "ActionCompleted",
    "ActionStarted",
    "Event",
    "EventBus",
    "PreflightCheckCompleted",
    "PreflightCompleted",
    "PreflightStarted",
    "TestFailed",
    "TestPassed",
    "TestRunCompleted",
    "TestRunStarted",
    "TestSkipped",
    "TestStarted",
    "VerificationCompleted",
    "VerificationStarted",
]
