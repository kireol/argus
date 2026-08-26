"""CI providers: detection and context normalization (see ``base.py``)."""

from __future__ import annotations

from argus.ci.providers.azure import AzureProvider
from argus.ci.providers.base import CIProvider, ProviderCapabilities, ProviderRegistry
from argus.ci.providers.generic import GenericProvider, LocalProvider
from argus.ci.providers.github import GitHubProvider
from argus.ci.providers.gitlab import GitLabProvider
from argus.ci.providers.jenkins import JenkinsProvider


def default_provider_registry() -> ProviderRegistry:
    """Built-in providers, most specific first; ``local`` always matches last."""
    registry = ProviderRegistry()
    registry.register(GitHubProvider())
    registry.register(GitLabProvider())
    registry.register(JenkinsProvider())
    registry.register(AzureProvider())
    registry.register(GenericProvider())
    registry.register(LocalProvider())
    return registry


__all__ = [
    "AzureProvider",
    "CIProvider",
    "GenericProvider",
    "GitHubProvider",
    "GitLabProvider",
    "JenkinsProvider",
    "LocalProvider",
    "ProviderCapabilities",
    "ProviderRegistry",
    "default_provider_registry",
]
