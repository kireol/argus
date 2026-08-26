"""CI artifact layout — deterministic, path-safe, secret-free.

Layout (subdirectories exist only when used)::

    argus-results/
    ├── report.json            canonical machine-readable report (schema v1)
    ├── junit.xml
    ├── report.html
    ├── tests/<TEST-ID>[_platform][_attemptN]/   engine evidence per test
    │       actual.png expected.png diff.png logs.txt metadata.json ...
    ├── logs/argus/argus.log   structured (JSON lines) run log
    └── metadata/
        ├── ci.json            normalized CI context
        ├── git.json           repository facts (best effort)
        ├── environment.json   whitelisted environment facts
        └── preflight.json     pre-flight results (when preflight ran)
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from argus import __version__
from argus.artifacts.manager import safe_path_component
from argus.ci.context import CIContext
from argus.ci.result import ArtifactEntry
from argus.exceptions import ConfigurationError
from argus.logging import redact
from argus.service.facade import classify_artifact

# Files/directories Argus owns inside the output directory; a fresh run
# removes only these so stale results never mix with new ones.
_OWNED = ("report.json", "junit.xml", "report.html", "tests", "logs", "metadata")

# Non-secret environment facts worth recording. Never dump the whole environment.
_ENVIRONMENT_WHITELIST = ("CI", "TZ", "LANG", "RUNNER_OS", "RUNNER_ARCH", "ImageOS")


class CIArtifactLayout:
    """Resolves and validates the CI output directory and its well-known paths."""

    def __init__(self, directory: str | Path, root_dir: Path) -> None:
        base = Path(directory)
        if not base.is_absolute():
            base = root_dir / base
        self.root_dir = root_dir.resolve()
        self.directory = base.resolve()
        self._validate()

    def _validate(self) -> None:
        out = self.directory
        if out == Path(out.anchor) or out == Path.home().resolve():
            raise ConfigurationError(
                f"Invalid configuration: ci.artifacts.directory\n\nRefusing to use {out} "
                "as the artifact directory.",
                remediation="Point ci.artifacts.directory at a dedicated folder such as "
                "argus-results.",
            )
        if out == self.root_dir or out in self.root_dir.parents:
            raise ConfigurationError(
                f"Invalid configuration: ci.artifacts.directory\n\n{out} is the project root "
                "(or one of its parents); Argus would delete project files when cleaning it.",
                remediation="Use a subdirectory such as argus-results.",
            )

    # -- well-known paths --------------------------------------------------------------

    @property
    def report_json(self) -> Path:
        return self.directory / "report.json"

    @property
    def junit_xml(self) -> Path:
        return self.directory / "junit.xml"

    @property
    def report_html(self) -> Path:
        return self.directory / "report.html"

    @property
    def tests_dir(self) -> Path:
        return self.directory / "tests"

    @property
    def logs_dir(self) -> Path:
        return self.directory / "logs" / "argus"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "argus.log"

    @property
    def metadata_dir(self) -> Path:
        return self.directory / "metadata"

    # -- lifecycle ---------------------------------------------------------------------

    def prepare(self) -> None:
        """Create the directory and remove Argus-owned leftovers from earlier runs."""
        self.directory.mkdir(parents=True, exist_ok=True)
        for name in _OWNED:
            path = self.directory / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists() or path.is_symlink():
                path.unlink(missing_ok=True)

    def relative(self, path: str | Path) -> str:
        """POSIX path of ``path`` relative to the output directory (or as given)."""
        try:
            return Path(path).resolve().relative_to(self.directory).as_posix()
        except ValueError:
            return Path(path).as_posix()

    def safe_child(self, *parts: str) -> Path:
        """A path under the output directory built from sanitized components."""
        target = self.directory
        for part in parts:
            target = target / safe_path_component(part)
        return target

    # -- metadata ----------------------------------------------------------------------

    def write_json(self, name: str, data: Any, *, subdir: str = "metadata") -> Path:
        target = self.safe_child(subdir, name) if subdir else self.safe_child(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(redact(json.dumps(data, indent=2, default=str)), encoding="utf-8")
        return target

    def write_metadata(
        self, context: CIContext, environment: dict[str, str] | None = None
    ) -> list[Path]:
        env = dict(os.environ if environment is None else environment)
        written = [
            self.write_json("ci.json", context.to_dict()),
            self.write_json("environment.json", environment_metadata(env)),
        ]
        git = git_metadata(self.root_dir)
        if git:
            written.append(self.write_json("git.json", git))
        return written

    # -- inventory ----------------------------------------------------------------------

    def inventory(
        self, owners: dict[str, tuple[str, str | None]] | None = None
    ) -> list[ArtifactEntry]:
        """Every file under the output directory (sorted, relative paths)."""
        entries: list[ArtifactEntry] = []
        if not self.directory.is_dir():
            return entries
        owners = owners or {}
        for path in sorted(p for p in self.directory.rglob("*") if p.is_file()):
            rel = path.relative_to(self.directory).as_posix()
            kind, _ = classify_artifact(path.name)
            test_id, test_platform = (None, None)
            parts = rel.split("/")
            if len(parts) >= 3 and parts[0] == "tests":
                test_id, test_platform = owners.get(parts[1], (None, None))
            entries.append(
                ArtifactEntry(
                    path=rel,
                    kind=kind,
                    size=path.stat().st_size,
                    test_id=test_id,
                    platform=test_platform,
                )
            )
        return entries


def environment_metadata(environment: dict[str, str]) -> dict[str, Any]:
    """Whitelisted facts about the execution environment (no secrets, no dump)."""
    return {
        "argus_version": __version__,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "executable": Path(sys.executable).name,
        "variables": {k: environment[k] for k in _ENVIRONMENT_WHITELIST if k in environment},
    }


def git_metadata(root: Path) -> dict[str, Any]:
    """``git`` facts for ``root`` (empty when git or the repository is unavailable)."""
    if shutil.which("git") is None:
        return {}

    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    sha = run("rev-parse", "HEAD")
    if sha is None:
        return {}
    return {
        "commit": sha,
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "subject": run("log", "-1", "--pretty=%s"),
        "author": run("log", "-1", "--pretty=%an"),
        "committed_at": run("log", "-1", "--pretty=%cI"),
        "dirty": bool(run("status", "--porcelain")),
    }
