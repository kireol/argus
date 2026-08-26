"""OCR providers.

``FakeOCRProvider`` reads text the fake recorder embedded as capture
metadata (deterministic for tests). ``TesseractOCRProvider`` wraps
pytesseract with word boxes. Both run through the worker pool — never on the
UI thread.
"""

from __future__ import annotations

from typing import Any, Protocol

from PIL.Image import Image

from argus_test_creator.core.errors import OCRProviderError
from argus_test_creator.models.common import Rect
from argus_test_creator.models.recording import OCRObservation, OCRWordObservation


class OCRProvider(Protocol):
    name: str

    def is_available(self) -> tuple[bool, str]: ...

    def extract(
        self, image: Image, *, capture_id: str, region: Rect | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OCRObservation: ...


class FakeOCRProvider:
    """Returns the ``visible_text`` entries recorded in capture metadata."""

    name = "fake"

    def is_available(self) -> tuple[bool, str]:
        return True, ""

    def extract(
        self, image: Image, *, capture_id: str, region: Rect | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OCRObservation:
        words: list[OCRWordObservation] = []
        for entry in (metadata or {}).get("visible_text", []):
            rect = Rect.model_validate(entry["region"]) if entry.get("region") else None
            if region is not None and rect is not None and not _overlaps(region, rect):
                continue
            words.append(OCRWordObservation(text=str(entry["text"]), confidence=0.99, region=rect))
        text = "\n".join(w.text for w in words)
        return OCRObservation(capture_id=capture_id, provider=self.name, text=text,
                              words=tuple(words), region=region)


class TesseractOCRProvider:
    name = "tesseract"

    def __init__(self, language: str = "eng") -> None:
        self._language = language

    def is_available(self) -> tuple[bool, str]:
        try:
            import pytesseract
        except ImportError:
            return False, "pytesseract is not installed (pip install 'argus-test-creator[ocr]')"
        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:  # noqa: BLE001
            return False, f"tesseract binary not found ({exc})"
        return True, ""

    def extract(
        self, image: Image, *, capture_id: str, region: Rect | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OCRObservation:
        try:
            import pytesseract
        except ImportError as exc:
            raise OCRProviderError(
                "pytesseract is not installed.",
                remediation="pip install 'argus-test-creator[ocr]' and install tesseract.",
            ) from exc
        source = image.crop(region.as_box()) if region is not None else image
        offset_x, offset_y = (region.x, region.y) if region is not None else (0, 0)
        try:
            data = pytesseract.image_to_data(
                source, lang=self._language, output_type=pytesseract.Output.DICT
            )
        except Exception as exc:  # noqa: BLE001 - tesseract raises many types
            raise OCRProviderError(
                f"OCR failed: {exc}", remediation="Check the tesseract installation."
            ) from exc
        words: list[OCRWordObservation] = []
        lines: dict[tuple[int, int, int], list[str]] = {}
        for i, text in enumerate(data.get("text", [])):
            text = str(text).strip()
            if not text:
                continue
            conf = float(data["conf"][i]) if data["conf"][i] not in ("-1", -1) else None
            rect = Rect(
                x=int(data["left"][i]) + offset_x, y=int(data["top"][i]) + offset_y,
                width=max(int(data["width"][i]), 1), height=max(int(data["height"][i]), 1),
            )
            words.append(OCRWordObservation(text=text, confidence=conf, region=rect))
            key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
            lines.setdefault(key, []).append(text)
        full_text = "\n".join(" ".join(parts) for parts in lines.values())
        return OCRObservation(capture_id=capture_id, provider=self.name, text=full_text,
                              words=tuple(words), region=region)


def create_ocr_provider(name: str = "tesseract", **options: Any) -> OCRProvider:
    if name == "fake":
        return FakeOCRProvider()
    if name == "tesseract":
        return TesseractOCRProvider(language=str(options.get("language", "eng")))
    raise OCRProviderError(f"Unknown OCR provider {name!r}.", remediation="Use tesseract or fake.")


def _overlaps(a: Rect, b: Rect) -> bool:
    return not (b.x >= a.right or b.right <= a.x or b.y >= a.bottom or b.bottom <= a.y)


def group_lines(observation: OCRObservation) -> list[tuple[str, Rect | None]]:
    """OCR lines with a bounding box (union of word boxes) for UI pick-lists."""
    out: list[tuple[str, Rect | None]] = []
    for line in observation.lines():
        tokens = set(line.split())
        boxes = [w.region for w in observation.words
                 if w.region and (w.text == line or w.text in tokens)]
        if boxes:
            x = min(b.x for b in boxes)
            y = min(b.y for b in boxes)
            right = max(b.right for b in boxes)
            bottom = max(b.bottom for b in boxes)
            out.append((line, Rect(x=x, y=y, width=right - x, height=bottom - y)))
        else:
            out.append((line, None))
    return out
