"""Pluggable visual verification."""

from utf.verifiers.assets import AssetStore
from utf.verifiers.base import Expectation, Verifier
from utf.verifiers.image import (
    ImageAbsentVerifier,
    ImagePresentVerifier,
    ScreenshotMatchVerifier,
)
from utf.verifiers.text import TextAbsentVerifier, TextPresentVerifier

__all__ = [
    "AssetStore",
    "Expectation",
    "ImageAbsentVerifier",
    "ImagePresentVerifier",
    "ScreenshotMatchVerifier",
    "TextAbsentVerifier",
    "TextPresentVerifier",
    "Verifier",
]
