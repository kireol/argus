"""Artifact directories and failure diagnostics.

Layout::

    results/
      2026-08-12_10-42-31/
        run.json
        MOV-001/
          actual.png
          expected.png
          diff.png
          logs.txt
          instrumentation.json
          metadata.json
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image as PILImage
from PIL.Image import Image

from argus.config.models import ResultsConfig
from argus.logging import redact

_UNSAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_path_component(value: str, *, fallback: str = "unnamed", max_length: int = 120) -> str:
    """Reduce an arbitrary string (test id, platform, branch...) to a safe file name.

    The result never contains path separators, ``..`` segments, or control
    characters, so it can be joined under an output directory without escaping it.
    """
    cleaned = _UNSAFE_COMPONENT_RE.sub("_", value.strip())
    cleaned = cleaned.strip("._")
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "_")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length]


class TestArtifacts:
    """Artifact sink for one test."""

    def __init__(self, directory: Path, *, save_enabled: bool = True) -> None:
        self.directory = directory
        self._save_enabled = save_enabled
        self._created = False
        self.saved_comparisons = False

    def _ensure_dir(self) -> None:
        if not self._created:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._created = True

    def save_image(self, name: str, image: Image | np.ndarray) -> Path | None:
        if not self._save_enabled:
            return None
        self._ensure_dir()
        path = self.directory / name
        if isinstance(image, np.ndarray):
            array = image
            if array.ndim == 3:
                array = array[:, :, ::-1]  # BGR -> RGB
            PILImage.fromarray(array).save(path)
        else:
            image.save(path)
        return path

    def save_comparison_set(
        self,
        *,
        actual: Image | np.ndarray | None,
        expected: Image | np.ndarray | None,
        diff: Image | np.ndarray | None,
        prefix: str = "",
        also_canonical: bool = False,
    ) -> None:
        """Write actual/expected/diff PNGs for one image comparison.

        When ``prefix`` is set (e.g. ``icn_tt_battery_red``), files are named
        ``{prefix}_actual.png`` etc. ``also_canonical`` also writes the plain
        ``actual.png`` / ``expected.png`` / ``diff.png`` names used by the HTML
        report's primary gallery.
        """
        stem = prefix.strip().rstrip("_")
        pairs: list[tuple[str, Image | np.ndarray | None]] = [
            ("actual", actual),
            ("expected", expected),
            ("diff", diff),
        ]
        wrote = False
        for kind, image in pairs:
            if image is None:
                continue
            names = []
            if stem:
                names.append(f"{stem}_{kind}.png")
            if also_canonical or not stem:
                names.append(f"{kind}.png")
            for name in names:
                if self.save_image(name, image) is not None:
                    wrote = True
        if wrote:
            self.saved_comparisons = True

    def save_text(self, name: str, content: str) -> Path | None:
        if not self._save_enabled:
            return None
        self._ensure_dir()
        path = self.directory / name
        path.write_text(redact(content), encoding="utf-8")
        return path

    def save_json(self, name: str, data: Any) -> Path | None:
        return self.save_text(name, json.dumps(data, indent=2, default=str))

    def discard(self) -> None:
        """Remove this test's artifacts (used for retained-on-failure-only mode)."""
        if self._created and self.directory.exists():
            shutil.rmtree(self.directory, ignore_errors=True)
            self._created = False


class ArtifactManager:
    """Creates per-run and per-test artifact directories."""

    def __init__(
        self, config: ResultsConfig, root_dir: Path, *, run_dir: Path | None = None
    ) -> None:
        self._config = config
        base = Path(config.dir)
        if not base.is_absolute():
            base = root_dir / base
        self._base = base
        # A caller (e.g. the CI layer) may pin the run directory instead of
        # letting the manager mint a timestamped one under ``results.dir``.
        self._fixed_run_dir = run_dir
        self._run_dir: Path | None = None

    @property
    def has_run_dir(self) -> bool:
        return self._run_dir is not None

    @property
    def run_dir(self) -> Path:
        if self._run_dir is None and self._fixed_run_dir is not None:
            self._fixed_run_dir.mkdir(parents=True, exist_ok=True)
            self._run_dir = self._fixed_run_dir
        if self._run_dir is None:
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            run_dir = self._base / stamp
            suffix = 1
            while run_dir.exists():
                suffix += 1
                run_dir = self._base / f"{stamp}_{suffix}"
            run_dir.mkdir(parents=True)
            self._run_dir = run_dir
        return self._run_dir

    def for_test(self, test_id: str) -> TestArtifacts:
        return TestArtifacts(
            self.run_dir / safe_path_component(test_id),
            save_enabled=True,
        )

    def finalize_test(self, artifacts: TestArtifacts, *, passed: bool) -> None:
        """Apply retention policy after a test finishes."""
        keep = (
            not passed
            or self._config.retain_on_success
            or (
                self._config.save_comparison_images and artifacts.saved_comparisons
            )
        )
        if not keep:
            artifacts.discard()

    def save_run_report(self, name: str, content: str) -> Path:
        path = self.run_dir / name
        path.write_text(redact(content), encoding="utf-8")
        return path
