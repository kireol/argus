"""Deterministic assertion suggestions.

After an action with a meaningful screen change, compare OCR before/after and
propose "wait until text X appears" for newly visible text, or an image
region assertion for the changed area. Suggestions are candidates only —
nothing becomes a test step until the user accepts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from argus_test_creator.models.authoring import ConditionDraft
from argus_test_creator.models.common import Rect
from argus_test_creator.models.recording import OCRObservation
from argus_test_creator.observation.diff import ScreenDiff

_MIN_TEXT_LEN = 3
_MAX_SUGGESTIONS = 3


@dataclass(frozen=True)
class AssertionCandidate:
    condition: ConditionDraft
    reason: str
    capture_id: str | None = None
    #: For image candidates: the region to crop from ``capture_id``.
    region: Rect | None = None
    #: Suggest wait_until (synchronization) rather than a one-shot verify.
    synchronize: bool = True
    evidence: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        if self.condition.type == "image_present" and "image" not in self.condition.params:
            r = self.region
            where = f" at ({r.x}, {r.y}) {r.width}x{r.height}" if r else ""
            return f"Image region{where} is visible"
        return self.condition.describe()


class AssertionSuggester:
    def __init__(self, *, max_suggestions: int = _MAX_SUGGESTIONS) -> None:
        self._max = max_suggestions

    def suggest(
        self,
        *,
        diff: ScreenDiff | None,
        ocr_before: OCRObservation | None,
        ocr_after: OCRObservation | None,
        capture_after: str | None,
        screen_size: tuple[int, int] | None = None,
    ) -> list[AssertionCandidate]:
        candidates: list[AssertionCandidate] = []
        if diff is not None and not diff.significant:
            return candidates
        before_lines = set(ocr_before.lines()) if ocr_before else set()
        after_lines = ocr_after.lines() if ocr_after else []
        new_text = [t for t in after_lines if t not in before_lines and len(t) >= _MIN_TEXT_LEN]
        # Longer strings are more specific (and less likely to be chrome like "Back").
        new_text.sort(key=len, reverse=True)
        for text in new_text[: self._max]:
            region = _region_for(ocr_after, text) if ocr_after else None
            params: dict[str, object] = {"text": text}
            candidates.append(AssertionCandidate(
                condition=ConditionDraft(type="text_present", params=params),
                reason=f'New text detected: "{text}"',
                capture_id=capture_after,
                region=region,
                evidence={"source": "ocr", "provider": ocr_after.provider if ocr_after else ""},
            ))
        if diff is not None and diff.changed_region is not None and capture_after:
            region = diff.changed_region
            fraction = 1.0
            if screen_size:
                fraction = region.area / float(screen_size[0] * screen_size[1])
            if 0.0005 <= fraction <= 0.5 and len(candidates) < self._max:
                candidates.append(AssertionCandidate(
                    condition=ConditionDraft(type="image_present", params={"threshold": 0.9}),
                    reason="New image region detected",
                    capture_id=capture_after,
                    region=region,
                    evidence={"source": "diff", "changed": f"{diff.changed_fraction:.1%}"},
                ))
        return candidates[: self._max]


def _region_for(observation: OCRObservation, text: str) -> Rect | None:
    tokens = text.split()
    boxes = [w.region for w in observation.words if w.region is not None and w.text in tokens]
    if not boxes:
        return None
    x = min(b.x for b in boxes)
    y = min(b.y for b in boxes)
    right = max(b.right for b in boxes)
    bottom = max(b.bottom for b in boxes)
    pad = 8
    return Rect(x=max(x - pad, 0), y=max(y - pad, 0), width=right - x + 2 * pad,
                height=bottom - y + 2 * pad)
