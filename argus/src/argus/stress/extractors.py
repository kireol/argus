"""Context extractors — which backend entities are on screen right now?

    StressContext.entity_context ← CompositeExtractor ← [State, OCR, Configured]

* :class:`StateContextExtractor` reads ``entity.current_key`` from the backend
  state document (e.g. ``movieId: 123`` → movies/123).
* :class:`OCRContextExtractor` matches the display field of known entities
  against words the OCR provider read from the latest observation.
* Scenario-specific extractors register through ``argus.stress.extractors``.

No universal application parser: where automatic extraction is impossible,
scenarios configure ``current_key`` / display fields explicitly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib import metadata
from typing import TYPE_CHECKING, Any

from argus.stress.models import EntityRef

if TYPE_CHECKING:
    from argus.stress.context import StressContext
    from argus.stress.mutations.scheduler import MutationScheduler


class ContextExtractor(ABC):
    name: str = "extractor"

    @abstractmethod
    def extract(self, context: StressContext) -> list[EntityRef]: ...


class StateContextExtractor(ContextExtractor):
    name = "state"

    def __init__(self, scheduler: MutationScheduler) -> None:
        self._scheduler = scheduler

    def extract(self, context: StressContext) -> list[EntityRef]:
        schema = self._scheduler.schema(context)
        if schema is None or context.backend is None:
            return []
        keys = {e.name: e for e in schema.entities.values() if e.current_key}
        if not keys:
            return []
        try:
            state = context.backend.get_state()
        except Exception as exc:  # noqa: BLE001 - state is optional evidence
            context.logger.debug("state extraction failed: %s", exc)
            return []
        if not isinstance(state, dict):
            return []
        refs: list[EntityRef] = []
        for name, entity in keys.items():
            assert entity.current_key is not None
            value = state.get(entity.current_key)
            if value in (None, "", [], {}):
                continue
            entity_id = str(value.get(entity.id_field)) if isinstance(value, dict) else str(value)
            label = None
            item = next((i for i in self._scheduler.entities(context, name)
                         if str(i.get(entity.id_field)) == entity_id), None)
            if item is not None and entity.display_field:
                label = str(item.get(entity.display_field, "")) or None
            refs.append(EntityRef(entity_type=name, entity_id=entity_id, label=label,
                                  source="state", confidence=1.0, data=dict(item or {})))
        return refs


class OCRContextExtractor(ContextExtractor):
    name = "ocr"

    def __init__(self, scheduler: MutationScheduler, *, min_label_length: int = 3) -> None:
        self._scheduler = scheduler
        self._min_label = min_label_length

    def extract(self, context: StressContext) -> list[EntityRef]:
        schema = self._scheduler.schema(context)
        record = context.last_observation
        if schema is None or record is None or context.ocr is None:
            return []
        result = context.ocr_for(record)
        if result is None or not result.text.strip():
            return []
        text = result.text.lower()
        refs: list[EntityRef] = []
        for entity in schema.entities.values():
            field = entity.display_field
            if field is None:
                continue
            for item in self._scheduler.entities(context, entity.name):
                label = str(item.get(field, "") or "")
                if len(label) < self._min_label or label.lower() not in text:
                    continue
                entity_id = item.get(entity.id_field)
                if entity_id is None:
                    continue
                region = _locate(result, label)
                data: dict[str, Any] = dict(item)
                if region is not None:
                    data["region"] = region
                refs.append(EntityRef(entity_type=entity.name, entity_id=str(entity_id),
                                      label=label, source="ocr", confidence=0.8, data=data))
        return refs


class CompositeExtractor(ContextExtractor):
    name = "composite"
    ENTRY_POINT_GROUP = "argus.stress.extractors"

    def __init__(self, extractors: list[ContextExtractor], *, load_entry_points: bool = True) -> None:  # noqa: E501
        self._extractors = list(extractors)
        if load_entry_points:
            self._load_entry_points()

    def add(self, extractor: ContextExtractor) -> None:
        self._extractors.append(extractor)

    def extract(self, context: StressContext) -> list[EntityRef]:
        seen: set[tuple[str, str]] = set()
        refs: list[EntityRef] = []
        for extractor in self._extractors:
            try:
                found = extractor.extract(context)
            except Exception as exc:  # noqa: BLE001 - extraction is best-effort
                context.logger.debug("extractor %s failed: %s", extractor.name, exc)
                continue
            for ref in found:
                key = (ref.entity_type, ref.entity_id)
                if key in seen:
                    continue
                seen.add(key)
                refs.append(ref)
        return refs

    def update(self, context: StressContext) -> list[EntityRef]:
        context.entity_context = self.extract(context)
        return context.entity_context

    def _load_entry_points(self) -> None:
        try:
            entry_points = list(metadata.entry_points(group=self.ENTRY_POINT_GROUP))
        except Exception:  # noqa: BLE001
            return
        for entry_point in entry_points:
            try:
                extractor = entry_point.load()()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(extractor, ContextExtractor):
                self._extractors.append(extractor)


def _locate(result: Any, label: str) -> dict[str, int] | None:
    """Bounding box of the OCR words spelling ``label`` (best effort)."""
    words = [w for w in getattr(result, "words", []) if w.region is not None]
    parts = label.lower().split()
    if not parts or not words:
        return None
    for start in range(len(words)):
        window = words[start:start + len(parts)]
        if len(window) < len(parts):
            break
        if all(w.text.lower().strip(".,:;!?") == p.strip(".,:;!?") for w, p in zip(window, parts, strict=True)):  # noqa: E501
            xs = [w.region.x for w in window]
            ys = [w.region.y for w in window]
            rights = [w.region.x + w.region.width for w in window]
            bottoms = [w.region.y + w.region.height for w in window]
            return {"x": min(xs), "y": min(ys), "width": max(rights) - min(xs),
                    "height": max(bottoms) - min(ys)}
    return None


__all__ = ["CompositeExtractor", "ContextExtractor", "OCRContextExtractor",
           "StateContextExtractor"]
