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

from argus.models.common import Region
from argus.models.observation import Observation
from argus.models.results import VerificationResult


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
    # Ignore near-black reference pixels so icon crops work on any wallpaper.
    mask_background: bool = False
    mask_luminance: int | None = Field(default=None, ge=0, le=255)

    @property
    def extras(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Verifier(ABC):
    """Compares an observation against an expectation."""

    name: str = "verifier"

    @abstractmethod
    def verify(self, observation: Observation, expectation: Expectation) -> VerificationResult:
        ...
