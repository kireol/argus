"""Reporter interface and registry (provider-specific publishing)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from argus.ci.artifacts import CIArtifactLayout
from argus.ci.result import CIRunResult


class CIReporter(ABC):
    """Publishes a finished run to a CI provider (summary, annotations...)."""

    name: str = ""

    @abstractmethod
    def publish(
        self,
        result: CIRunResult,
        layout: CIArtifactLayout | None,
        environment: Mapping[str, str],
        *,
        summary: bool = True,
        annotations: bool = True,
        max_annotations: int = 20,
    ) -> list[str]:
        """Publish and return human-readable notes about what was published.

        Implementations must never raise for a missing optional integration
        (e.g. no ``GITHUB_STEP_SUMMARY``); they degrade to no-ops.
        """


class ReporterRegistry:
    """Provider name -> reporter; unknown providers get the generic reporter."""

    def __init__(self, fallback: CIReporter) -> None:
        self._fallback = fallback
        self._reporters: dict[str, CIReporter] = {}

    def register(self, provider_name: str, reporter: CIReporter) -> None:
        self._reporters[provider_name] = reporter

    def for_provider(self, provider_name: str) -> CIReporter:
        return self._reporters.get(provider_name, self._fallback)

    def names(self) -> list[str]:
        return sorted(self._reporters)
