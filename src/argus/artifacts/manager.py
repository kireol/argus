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
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image as PILImage
from PIL.Image import Image

from argus.config.models import ResultsConfig
from argus.logging import redact


class TestArtifacts:
    """Artifact sink for one test."""

    def __init__(self, directory: Path, *, save_enabled: bool = True) -> None:
        self.directory = directory
        self._save_enabled = save_enabled
        self._created = False

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

    def __init__(self, config: ResultsConfig, root_dir: Path) -> None:
        self._config = config
        base = Path(config.dir)
        if not base.is_absolute():
            base = root_dir / base
        self._base = base
        self._run_dir: Path | None = None

    @property
    def has_run_dir(self) -> bool:
        return self._run_dir is not None

    @property
    def run_dir(self) -> Path:
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
        return TestArtifacts(self.run_dir / test_id)

    def finalize_test(self, artifacts: TestArtifacts, *, passed: bool) -> None:
        """Apply retention policy after a test finishes."""
        if passed and not self._config.retain_on_success:
            artifacts.discard()

    def save_run_report(self, name: str, content: str) -> Path:
        path = self.run_dir / name
        path.write_text(redact(content), encoding="utf-8")
        return path
