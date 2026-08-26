"""CI provider interface and registry.

A provider knows how to *detect* a CI environment and *normalize* its
metadata into :class:`~argus.ci.context.CIContext`. Publishing (job
summaries, annotations) lives in :mod:`argus.ci.reporters`, selected by
provider name — so the core never branches on a provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from argus.ci.context import CIContext, Environment
from argus.exceptions import ConfigurationError


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider's reporter can do; the runner adapts instead of assuming."""

    supports_summary: bool = False
    supports_annotations: bool = False
    supports_checks: bool = False
    supports_artifacts: bool = False
    supports_pr_comments: bool = False


class CIProvider(ABC):
    """Detects one CI system and collects its normalized context."""

    #: Stable identifier used in configuration (``ci.provider``) and reports.
    name: str = ""
    #: Human-readable name for console output.
    display_name: str = ""
    capabilities: ProviderCapabilities = ProviderCapabilities()

    @abstractmethod
    def detect(self, environment: Environment) -> bool:
        """True when ``environment`` identifies this CI system."""

    @abstractmethod
    def collect_context(self, environment: Environment) -> CIContext:
        """Normalize the environment (never raises; missing values stay ``None``)."""


class ProviderRegistry:
    """Ordered provider registry (first ``detect`` match wins).

    Register specific providers before broad ones: the generic provider
    matches any ``CI=true`` environment and the local provider matches
    everything, so they come last.
    """

    def __init__(self) -> None:
        self._providers: list[CIProvider] = []

    def register(self, provider: CIProvider) -> None:
        if not provider.name:
            raise ValueError("CI providers need a non-empty name")
        self._providers = [p for p in self._providers if p.name != provider.name]
        self._providers.append(provider)

    def names(self) -> list[str]:
        return [p.name for p in self._providers]

    def get(self, name: str) -> CIProvider:
        for provider in self._providers:
            if provider.name == name:
                return provider
        raise ConfigurationError(
            f"Invalid configuration: ci.provider\n\nUnknown CI provider {name!r}.\n"
            f"Allowed: auto, {', '.join(self.names())}",
            remediation="Set ci.provider to 'auto' or one of the listed providers.",
        )

    def detect(self, environment: Environment) -> CIProvider:
        """The first registered provider whose ``detect`` matches (O(#providers))."""
        for provider in self._providers:
            if provider.detect(environment):
                return provider
        raise ConfigurationError(
            "No CI provider matched the environment and no fallback is registered."
        )

    def resolve(self, name: str, environment: Environment) -> CIProvider:
        """``auto`` -> detection; otherwise the named provider (validated)."""
        if name == "auto":
            return self.detect(environment)
        return self.get(name)
