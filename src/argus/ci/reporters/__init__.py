"""CI reporters: provider-specific publishing (job summaries, annotations)."""

from __future__ import annotations

from argus.ci.reporters.base import CIReporter, ReporterRegistry
from argus.ci.reporters.generic import GenericReporter
from argus.ci.reporters.github import GitHubReporter


def default_reporter_registry() -> ReporterRegistry:
    """Built-in reporters; GitLab/Jenkins/Azure use the generic reporter until
    a dedicated one is registered here (register(name, reporter))."""
    registry = ReporterRegistry(GenericReporter())
    registry.register("github", GitHubReporter())
    return registry


__all__ = [
    "CIReporter",
    "GenericReporter",
    "GitHubReporter",
    "ReporterRegistry",
    "default_reporter_registry",
]
