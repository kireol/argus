"""CreatorProject — a directory Argus can run as-is.

Layout::

    my-project/
    ├── argus.yaml              # Argus configuration (devices, test_paths, asset_paths)
    ├── tests/<TEST-ID>.yaml    # generated Argus tests (portable; no Creator needed)
    ├── assets/images/*.png     # promoted image assets
    ├── results/                # Argus run output
    └── .argus-creator/         # Creator-only state (safe to delete)
        ├── project.json
        ├── documents/<TEST-ID>.json   # authoring documents with provenance
        ├── sessions/<session-id>/     # recording journals + screenshots
        └── workspace/                 # temporary crops/previews

The generated YAML is the contract; ``.argus-creator`` only adds provenance.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from argus_test_creator.core.errors import ProjectError
from argus_test_creator.core.paths import atomic_write_json, atomic_write_text
from argus_test_creator.models.authoring import AuthoringDocument
from argus_test_creator.models.capabilities import TargetProfile
from argus_test_creator.serialization import document_to_yaml, load_document

CREATOR_DIR = ".argus-creator"
CONFIG_FILE = "argus.yaml"
TESTS_DIR = "tests"
ASSETS_DIR = Path("assets") / "images"
PROJECT_FORMAT = 1


@dataclass(frozen=True)
class ProjectInfo:
    root: Path
    name: str
    test_ids: list[str]


class CreatorProject:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    # -- layout -------------------------------------------------------------------

    @property
    def creator_dir(self) -> Path:
        return self.root / CREATOR_DIR

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILE

    @property
    def tests_dir(self) -> Path:
        return self.root / TESTS_DIR

    @property
    def assets_dir(self) -> Path:
        return self.root / ASSETS_DIR

    @property
    def documents_dir(self) -> Path:
        return self.creator_dir / "documents"

    @property
    def sessions_dir(self) -> Path:
        return self.creator_dir / "sessions"

    @property
    def workspace_dir(self) -> Path:
        return self.creator_dir / "workspace"

    @property
    def results_dir(self) -> Path:
        return self.root / "results"

    @property
    def exists(self) -> bool:
        return self.config_path.is_file() or (self.creator_dir / "project.json").is_file()

    # -- lifecycle --------------------------------------------------------------------

    @classmethod
    def create(cls, root: Path, *, name: str | None = None) -> CreatorProject:
        project = cls(root)
        if project.exists:
            raise ProjectError(
                f"{root} already contains a project.", remediation="Open it instead."
            )
        for path in (project.tests_dir, project.assets_dir, project.documents_dir,
                     project.sessions_dir, project.workspace_dir):
            path.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            project.creator_dir / "project.json",
            {"format": PROJECT_FORMAT, "name": name or root.name, "targets": {}},
        )
        (project.creator_dir / ".gitignore").write_text("workspace/\nsessions/\n", encoding="utf-8")
        if not project.config_path.exists():
            project.write_argus_config(None)
        return project

    @classmethod
    def open(cls, root: Path) -> CreatorProject:
        project = cls(root)
        if not project.exists:
            raise ProjectError(
                f"{root} is not an Argus Test Creator project.",
                remediation="Create a new project, or open a folder containing argus.yaml.",
            )
        for path in (project.documents_dir, project.sessions_dir, project.workspace_dir):
            path.mkdir(parents=True, exist_ok=True)
        return project

    def info(self) -> ProjectInfo:
        name = self.root.name
        meta = self.creator_dir / "project.json"
        if meta.is_file():
            try:
                name = json.loads(meta.read_text(encoding="utf-8")).get("name", name)
            except (OSError, ValueError):
                pass
        return ProjectInfo(root=self.root, name=name, test_ids=self.list_test_ids())

    # -- documents ---------------------------------------------------------------------

    def list_test_ids(self) -> list[str]:
        if not self.tests_dir.is_dir():
            return []
        return sorted(p.stem for p in self.tests_dir.glob("*.yaml"))

    def test_path(self, test_id: str) -> Path:
        return self.tests_dir / f"{test_id}.yaml"

    def document_path(self, test_id: str) -> Path:
        return self.documents_dir / f"{test_id}.json"

    def save_document(self, document: AuthoringDocument) -> Path:
        """Write the Argus YAML (the contract) and the authoring document (provenance)."""
        test_id = document.metadata.id
        if not test_id:
            raise ProjectError("The test needs an ID before it can be saved.",
                               remediation="Set an ID in the test details.")
        self.tests_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = self.test_path(test_id)
        atomic_write_text(yaml_path, document_to_yaml(document))
        document.source_path = str(yaml_path)
        atomic_write_json(self.document_path(test_id), document.model_dump(mode="json"))
        if document.target is not None:
            self.write_argus_config(document.target)
        return yaml_path

    def load_document(self, test_id: str) -> AuthoringDocument:
        """Prefer the authoring document (keeps provenance); fall back to the YAML."""
        json_path = self.document_path(test_id)
        yaml_path = self.test_path(test_id)
        if json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                document = AuthoringDocument.model_validate(data)
            except (OSError, ValueError) as exc:
                raise ProjectError(f"Cannot read {json_path}: {exc}") from exc
            # The YAML is authoritative when it was edited outside the Creator.
            if yaml_path.is_file() and yaml_path.stat().st_mtime > json_path.stat().st_mtime + 1:
                imported = load_document(yaml_path)
                imported.target = document.target
                imported.assets = document.assets
                imported.session_ids = document.session_ids
                return imported
            document.source_path = str(yaml_path)
            return document
        if yaml_path.is_file():
            return load_document(yaml_path)
        raise ProjectError(f"No test {test_id!r} in {self.root}.")

    def delete_document(self, test_id: str) -> None:
        for path in (self.test_path(test_id), self.document_path(test_id)):
            if path.exists():
                path.unlink()

    # -- Argus configuration ---------------------------------------------------------------

    def write_argus_config(self, target: TargetProfile | None) -> Path:
        """Create/refresh ``argus.yaml`` so ``argus run --config argus.yaml`` works.

        Existing devices are preserved; the target's device entry is upserted.
        """
        config: dict[str, Any] = {}
        if self.config_path.is_file():
            try:
                loaded = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    config = loaded
            except yaml.YAMLError as exc:
                raise ProjectError(f"argus.yaml is not valid YAML: {exc}") from exc
        config.setdefault("test_paths", [TESTS_DIR])
        config.setdefault("asset_paths", [ASSETS_DIR.as_posix()])
        config.setdefault("results", {"dir": "results", "retain_on_success": True})
        if target is not None:
            devices = config.setdefault("devices", {})
            device = {"type": target.argus_device_type, "platform": target.platform}
            device.update(target.argus_device_options)
            devices[target.argus_device_name] = device
        text = "# Generated by Argus Test Creator. Safe to edit; the Creator only upserts devices\n"
        text += yaml.safe_dump(config, sort_keys=False, default_flow_style=False)
        atomic_write_text(self.config_path, text)
        return self.config_path

    def read_argus_config(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            return {}
        try:
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ProjectError(f"argus.yaml is not valid YAML: {exc}") from exc
        return data if isinstance(data, dict) else {}

    # -- housekeeping ------------------------------------------------------------------------

    def cleanup_sessions(self, *, keep_ids: set[str] | None = None, older_than_days: int = 7) -> int:  # noqa: E501
        """Remove abandoned recording sessions; returns the number removed."""
        if not self.sessions_dir.is_dir():
            return 0
        removed = 0
        cutoff = time.time() - older_than_days * 86400
        for path in self.sessions_dir.iterdir():
            if not path.is_dir() or (keep_ids and path.name in keep_ids):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed
