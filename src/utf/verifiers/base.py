"""Verifier abstraction.

A verifier compares an :class:`Observation` (what was actually captured)
against an :class:`Expectation` (what the test author declared) and returns a
:class:`VerificationResult`. Verifiers are pure with respect to devices —
they never capture anything themselves, which lets one observation feed many
verifiers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from utf.models.common import Region
from utf.models.observation import Observation
from utf.models.results import VerificationResult


class Expectation(BaseModel):
    """Declarative expectation extracted from a test step."""

    model_config = ConfigDict(extra="allow")

    image: str | None = None
    text: str | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    region: Region | str | None = None
    grayscale: bool | None = None
    case_sensitive: bool = False
    scale_tolerance: float | None = None

    @property
    def extras(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Verifier(ABC):
    """Compares an observation against an expectation."""

    name: str = "verifier"

    @abstractmethod
    def verify(self, observation: Observation, expectation: Expectation) -> VerificationResult:
        ...
