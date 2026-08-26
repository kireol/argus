"""Observation: screen captures, OCR, screen-change detection, assertion suggestions."""

from argus_test_creator.observation.captures import CaptureStore
from argus_test_creator.observation.diff import ScreenDiff, compare_images
from argus_test_creator.observation.ocr import (
    FakeOCRProvider,
    OCRProvider,
    TesseractOCRProvider,
    create_ocr_provider,
)
from argus_test_creator.observation.suggestions import AssertionCandidate, AssertionSuggester

__all__ = [
    "AssertionCandidate",
    "AssertionSuggester",
    "CaptureStore",
    "FakeOCRProvider",
    "OCRProvider",
    "ScreenDiff",
    "TesseractOCRProvider",
    "compare_images",
    "create_ocr_provider",
]
