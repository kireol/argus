"""AssetManager.

Temporary crops/previews live in a workspace directory; only *promoted*
assets are copied into the project's ``assets/images`` (the Argus asset
path). File names are derived from the user's label plus a short content
hash, so re-cropping identical pixels never creates duplicates.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from PIL import Image as PILImage
from PIL.Image import Image

from argus_test_creator.core.errors import AssetError
from argus_test_creator.models.authoring import AssetReference
from argus_test_creator.models.common import Rect

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slugify(label: str, fallback: str = "asset") -> str:
    slug = _SLUG_RE.sub("_", label.lower()).strip("_")
    return slug[:40] or fallback


class AssetManager:
    def __init__(self, project_assets_dir: Path, workspace_dir: Path) -> None:
        self._assets_dir = project_assets_dir
        self._workspace = workspace_dir

    @property
    def assets_dir(self) -> Path:
        return self._assets_dir

    @property
    def workspace_dir(self) -> Path:
        return self._workspace

    # -- workspace ---------------------------------------------------------------

    def crop(self, source: Path | Image, region: Rect, *, label: str = "crop") -> Path:
        """Crop ``region`` from a screenshot into the workspace; returns the PNG path."""
        self._workspace.mkdir(parents=True, exist_ok=True)
        try:
            image = source if isinstance(source, Image) else PILImage.open(source)
            with image:
                width, height = image.size
                if region.right > width or region.bottom > height:
                    raise AssetError(
                        f"Region {region.to_argus()} exceeds the {width}x{height} screenshot.",
                        remediation="Select a region inside the screenshot.",
                    )
                cropped = image.convert("RGB").crop(region.as_box())
                data = _png_bytes(cropped)
        except OSError as exc:
            raise AssetError(f"Cannot read screenshot: {exc}") from exc
        digest = content_hash(data)[:10]
        path = self._workspace / f"{slugify(label)}_{digest}.png"
        path.write_bytes(data)
        return path

    # -- promotion ------------------------------------------------------------------

    def promote(
        self,
        workspace_path: Path,
        *,
        label: str,
        source_capture_id: str | None = None,
        source_region: Rect | None = None,
    ) -> AssetReference:
        """Copy a workspace image into the project assets (deduplicated by content)."""
        try:
            data = workspace_path.read_bytes()
        except OSError as exc:
            raise AssetError(f"Cannot read {workspace_path}: {exc}") from exc
        digest = content_hash(data)
        existing = self.find_by_hash(digest)
        if existing is not None:
            relative = existing.name
        else:
            relative = f"{slugify(label)}_{digest[:8]}.png"
            self._assets_dir.mkdir(parents=True, exist_ok=True)
            target = self._assets_dir / relative
            if not target.exists():
                shutil.copyfile(workspace_path, target)
        with PILImage.open(self._assets_dir / relative) as img:
            width, height = img.size
        return AssetReference(
            relative_path=relative,
            sha256=digest,
            width=width,
            height=height,
            source_capture_id=source_capture_id,
            source_region=source_region.to_argus() if source_region else None,
        )

    def promote_image(self, image: Image, *, label: str, **meta: object) -> AssetReference:
        self._workspace.mkdir(parents=True, exist_ok=True)
        data = _png_bytes(image.convert("RGB"))
        tmp = self._workspace / f"{slugify(label)}_{content_hash(data)[:10]}.png"
        tmp.write_bytes(data)
        return self.promote(tmp, label=label, **meta)  # type: ignore[arg-type]

    def find_by_hash(self, digest: str) -> Path | None:
        if not self._assets_dir.is_dir():
            return None
        for path in sorted(self._assets_dir.glob("*.png")):
            try:
                if content_hash(path.read_bytes()) == digest:
                    return path
            except OSError:
                continue
        return None

    def exists(self, relative_path: str) -> bool:
        return (self._assets_dir / relative_path).is_file()

    def list_assets(self) -> list[Path]:
        if not self._assets_dir.is_dir():
            return []
        return sorted(p for p in self._assets_dir.rglob("*.png") if p.is_file())

    def clear_workspace(self) -> None:
        if self._workspace.is_dir():
            shutil.rmtree(self._workspace, ignore_errors=True)


def _png_bytes(image: Image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()
