"""CaptureStore — screenshots on disk, thumbnails cached, nothing pinned in RAM.

``save`` writes the PNG immediately and returns a lightweight
:class:`ScreenCapture`; ``load``/``thumbnail`` decode lazily on demand.
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus_test_creator.core.errors import ScreenshotError
from argus_test_creator.core.ids import new_id
from argus_test_creator.models.recording import ScreenCapture

THUMBNAIL_SIZE = (320, 180)


class CaptureStore:
    def __init__(self, directory: Path, *, thumbnail_cache: int = 64) -> None:
        self._dir = directory
        self._thumbs = directory / "thumbnails"
        self._index: dict[str, ScreenCapture] = {}
        self._thumb_cache: OrderedDict[str, Image] = OrderedDict()
        self._thumb_cache_size = thumbnail_cache
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self._dir

    def save(
        self,
        image: Image | bytes,
        *,
        event_id: str | None = None,
        phase: str | None = None,
        metadata: dict[str, Any] | None = None,
        capture_id: str | None = None,
    ) -> ScreenCapture:
        """Persist a screenshot. ``metadata`` (e.g. fake OCR text) goes to a sidecar JSON."""
        self._dir.mkdir(parents=True, exist_ok=True)
        capture_id = capture_id or new_id("cap")
        path = self._dir / f"{capture_id}.png"
        try:
            if isinstance(image, bytes):
                data = image
                with PILImage.open(io.BytesIO(data)) as img:
                    width, height = img.size
            else:
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                data = buffer.getvalue()
                width, height = image.size
            path.write_bytes(data)
        except OSError as exc:
            raise ScreenshotError(f"Cannot save screenshot: {exc}") from exc
        if metadata:
            path.with_suffix(".json").write_text(json.dumps(metadata), encoding="utf-8")
        capture = ScreenCapture(
            id=capture_id,
            path=str(path),
            width=width,
            height=height,
            sha256=hashlib.sha256(data).hexdigest(),
            event_id=event_id,
            phase=phase,
        )
        with self._lock:
            self._index[capture.id] = capture
        return capture

    def register(self, capture: ScreenCapture) -> None:
        with self._lock:
            self._index[capture.id] = capture

    def get(self, capture_id: str) -> ScreenCapture | None:
        with self._lock:
            return self._index.get(capture_id)

    def all(self) -> list[ScreenCapture]:
        with self._lock:
            return list(self._index.values())

    def __len__(self) -> int:
        return len(self._index)

    def load(self, capture: ScreenCapture | str) -> Image:
        """Decode the full-resolution image (caller releases it)."""
        path = self._path_of(capture)
        try:
            with PILImage.open(path) as img:
                return img.convert("RGB")
        except OSError as exc:
            raise ScreenshotError(f"Cannot open screenshot {path}: {exc}") from exc

    def metadata(self, capture: ScreenCapture | str) -> dict[str, Any]:
        path = self._path_of(capture).with_suffix(".json")
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def thumbnail(self, capture: ScreenCapture | str, size: tuple[int, int] = THUMBNAIL_SIZE) -> Image:  # noqa: E501
        """A cached, downscaled preview; generated once and stored next to the capture."""
        capture_id = capture if isinstance(capture, str) else capture.id
        key = f"{capture_id}@{size[0]}x{size[1]}"
        with self._lock:
            cached = self._thumb_cache.get(key)
            if cached is not None:
                self._thumb_cache.move_to_end(key)
                return cached
        self._thumbs.mkdir(parents=True, exist_ok=True)
        thumb_path = self._thumbs / f"{key}.png"
        if thumb_path.is_file():
            with PILImage.open(thumb_path) as img:
                thumb = img.convert("RGB")
        else:
            with PILImage.open(self._path_of(capture)) as img:
                thumb = img.convert("RGB")
                thumb.thumbnail(size)
            thumb.save(thumb_path, format="PNG")
        with self._lock:
            self._thumb_cache[key] = thumb
            while len(self._thumb_cache) > self._thumb_cache_size:
                self._thumb_cache.popitem(last=False)
        return thumb

    def _path_of(self, capture: ScreenCapture | str) -> Path:
        if isinstance(capture, ScreenCapture):
            return Path(capture.path)
        found = self.get(capture)
        if found is None:
            candidate = self._dir / f"{capture}.png"
            if candidate.is_file():
                return candidate
            raise ScreenshotError(f"Unknown capture {capture!r}.")
        return Path(found.path)
