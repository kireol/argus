"""Screen-aware target selection.

    Action → TargetSelector → [ConfiguredTargets, OCRTargets, EntityTargets] → CoordinateFallback

Meaningful targets come first: named regions from the scenario, words the
OCR provider read from the latest observation, and on-screen labels of known
backend entities. Random coordinates remain the fallback so a run never
stalls, but they are chosen with the configured probability, not by default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from argus.stress.config import TargetsConfig
from argus.stress.models import Target, TargetKind

if TYPE_CHECKING:
    from argus.stress.context import StressContext


class TargetProvider(ABC):
    name: str = "provider"

    @abstractmethod
    def targets(self, context: StressContext) -> list[Target]:
        """Candidate targets for the *current* screen (may be empty)."""


class ConfiguredTargets(TargetProvider):
    name = "configured"

    def __init__(self, config: TargetsConfig) -> None:
        self._config = config

    def targets(self, context: StressContext) -> list[Target]:
        out: list[Target] = []
        for region in self._config.regions:
            out.append(Target(
                x=region.x + region.width // 2, y=region.y + region.height // 2,
                kind=TargetKind.CONFIGURED, label=region.name, width=region.width,
                height=region.height, metadata={"weight": region.weight,
                                                "actions": list(region.actions)},
            ))
        return out


class OCRTargets(TargetProvider):
    """Words read from the latest observation (refreshed every N actions)."""

    name = "ocr"

    def __init__(self, config: TargetsConfig) -> None:
        self._config = config
        self._cache: list[Target] = []
        self._cache_step = -1

    def targets(self, context: StressContext) -> list[Target]:
        if not self._config.use_ocr or context.ocr is None:
            return []
        record = context.last_observation
        if record is None:
            return []
        due = context.step - self._cache_step >= self._config.ocr_refresh_every
        if not due and self._cache_step >= 0:
            return self._cache
        result = context.ocr_for(record)
        self._cache_step = context.step
        self._cache = []
        if result is None:
            return self._cache
        avoid_phrases = [a.lower() for a in self._config.avoid_words]
        avoid_tokens = {t for a in avoid_phrases for t in a.split()}
        for word in result.words:
            text = word.text.strip()
            if len(text) < self._config.min_word_length or word.region is None:
                continue
            lowered = text.lower()
            # A word is avoided when it is (part of) an avoid phrase, or contains one.
            if lowered in avoid_tokens or any(a in lowered for a in avoid_phrases):
                continue
            region = word.region
            self._cache.append(Target(
                x=region.x + region.width // 2, y=region.y + region.height // 2,
                kind=TargetKind.TEXT, label=text, width=region.width, height=region.height,
                confidence=word.confidence,
            ))
        return self._cache


class EntityTargets(TargetProvider):
    """Labels of entities the context extractors found on screen (OCR-located)."""

    name = "entity"

    def targets(self, context: StressContext) -> list[Target]:
        out: list[Target] = []
        for ref in context.entity_context:
            region = ref.data.get("region")
            if not isinstance(region, dict):
                continue
            out.append(Target(
                x=int(region["x"]) + int(region.get("width", 1)) // 2,
                y=int(region["y"]) + int(region.get("height", 1)) // 2,
                kind=TargetKind.ENTITY, label=ref.label or ref.describe(),
                metadata={"entity_type": ref.entity_type, "entity_id": ref.entity_id},
            ))
        return out


class TargetSelector:
    """Picks a target for an action from the providers, falling back to coordinates."""

    def __init__(self, config: TargetsConfig, providers: list[TargetProvider] | None = None) -> None:  # noqa: E501
        self._config = config
        self._providers: list[TargetProvider] = providers if providers is not None else [
            ConfiguredTargets(config), EntityTargets(), OCRTargets(config),
        ]
        self.known_hits = 0
        self.fallback_hits = 0

    @property
    def providers(self) -> list[TargetProvider]:
        return list(self._providers)

    def add_provider(self, provider: TargetProvider, *, first: bool = False) -> None:
        if first:
            self._providers.insert(0, provider)
        else:
            self._providers.append(provider)

    def known_targets(self, context: StressContext, action: str | None = None) -> list[Target]:
        out: list[Target] = []
        for provider in self._providers:
            try:
                candidates = provider.targets(context)
            except Exception as exc:  # noqa: BLE001 - a provider must never stop the run
                context.logger.debug("target provider %s failed: %s", provider.name, exc)
                continue
            for target in candidates:
                allowed = target.metadata.get("actions") or []
                if action is not None and allowed and action not in allowed:
                    continue
                out.append(target)
        return out

    def random_point(self, context: StressContext) -> Target:
        width, height = context.screen_size()
        margin = min(self._config.edge_margin, max(width // 4, 0), max(height // 4, 0))
        rng = context.rng
        return Target(
            x=rng.randint(margin, max(width - 1 - margin, margin)),
            y=rng.randint(margin, max(height - 1 - margin, margin)),
            kind=TargetKind.COORDINATE,
        )

    def pick(self, context: StressContext, action: str | None = None) -> Target:
        known = self.known_targets(context, action)
        if known and context.rng.chance(self._config.prefer_known):
            weights = [float(t.metadata.get("weight", 1.0)) for t in known]
            self.known_hits += 1
            return context.rng.weighted_choice(known, weights)
        self.fallback_hits += 1
        return self.random_point(context)


__all__ = [
    "ConfiguredTargets", "EntityTargets", "OCRTargets", "TargetProvider", "TargetSelector",
]
