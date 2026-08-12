"""Reference image asset resolution and caching.

Reference images are resolved by name across configured asset paths and
cached in memory — a wait_until loop polling at 250ms must never reload the
same PNG from disk on every poll.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image as PILImage

from utf.exceptions import AssetError


class AssetStore:
    def __init__(self, asset_paths: list[Path]) -> None:
        self._paths = asset_paths
        self._resolved: dict[str, Path] = {}
        # Cache key: (resolved path, grayscale). Values are OpenCV-style BGR/gray arrays.
        self._images: dict[tuple[Path, bool], np.ndarray] = {}

    def resolve(self, name: str) -> Path:
        """Resolve an asset name (or relative path) to a file."""
        cached = self._resolved.get(name)
        if cached is not None:
            return cached

        candidate = Path(name)
        candidates = [candidate] if candidate.is_absolute() else [
            base / name for base in self._paths
        ]
        for path in candidates:
            if path.is_file():
                self._resolved[name] = path
                return path

        searched = ", ".join(str(p) for p in self._paths) or "<no asset paths configured>"
        raise AssetError(
            f"Reference image {name!r} not found.",
            remediation=f"Place the file under one of the asset paths: {searched}.",
        )

    def load_array(self, name: str, *, grayscale: bool = False) -> np.ndarray:
        """Load an asset as a numpy array (BGR or grayscale), cached."""
        path = self.resolve(name)
        key = (path, grayscale)
        cached = self._images.get(key)
        if cached is not None:
            return cached

        try:
            with PILImage.open(path) as img:
                converted = img.convert("L" if grayscale else "RGB")
                array = np.asarray(converted)
        except OSError as exc:
            raise AssetError(f"Cannot load reference image {path}: {exc}") from exc

        if not grayscale:
            array = array[:, :, ::-1].copy()  # RGB -> BGR for OpenCV
        self._images[key] = array
        return array

    def exists(self, name: str) -> bool:
        try:
            self.resolve(name)
            return True
        except AssetError:
            return False

    def clear_cache(self) -> None:
        self._resolved.clear()
        self._images.clear()
