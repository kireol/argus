"""The Argus boundary.

Contract used (all documented/public):

* ``argus --version`` / ``argus version``       → installed version
* ``argus validate --config <file> --framework-only`` → exit 0 ready / 3 not ready;
  test definitions are loaded and validated here (definition errors → exit 3)
* ``argus list --config <file>``                  → exit 2 on definition errors
* ``argus run --config <file> --test <id>``       → exit 0 pass / 1 fail / 2 def error /
  3 preflight; writes ``<results.dir>/<stamp>/report.json`` (schema_version 1)
* ``argus.service`` Python API is *not* imported — the Creator works with any
  Argus installation, including one in a different virtualenv.

Discovery order: explicit path → ``ARGUS_EXECUTABLE`` → project ``.venv`` →
``PATH`` → the Python environment running the Creator.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus_test_creator.core.errors import ArgusIntegrationError
from argus_test_creator.core.logging import get_logger
from argus_test_creator.models.authoring import ValidationIssue

_log = get_logger("argus")
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")

INSTALL_HINT = (
    "Install Argus (pip install argus, or the Argus repo's install script) and either put "
    "`argus` on PATH, set ARGUS_EXECUTABLE, or configure the executable in Settings."
)


@dataclass(frozen=True)
class ArgusInfo:
    executable: Path
    version: str
    source: str  # configured | env | project-venv | path | sys.prefix


@dataclass
class ArgusValidationResult:
    ready: bool
    output: str
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class ArgusRunResult:
    exit_code: int
    status: str  # passed | failed | definition_error | preflight_failed | error | cancelled
    output: str
    report_path: Path | None = None
    report: dict[str, Any] | None = None
    html_report: Path | None = None
    results_dir: Path | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def test_results(self) -> list[dict[str, Any]]:
        if not self.report:
            return []
        return list(self.report.get("run", {}).get("tests", []))


def discover_argus(
    *, configured: str | Path | None = None, project_root: Path | None = None,
    env: dict[str, str] | None = None,
) -> ArgusInfo | None:
    env = dict(os.environ if env is None else env)
    candidates: list[tuple[Path, str]] = []
    if configured:
        candidates.append((Path(configured).expanduser(), "configured"))
    if env.get("ARGUS_EXECUTABLE"):
        candidates.append((Path(env["ARGUS_EXECUTABLE"]).expanduser(), "env"))
    if project_root is not None:
        for venv in (".venv", "venv"):
            for name in ("bin/argus", "Scripts/argus.exe"):
                candidates.append((project_root / venv / name, "project-venv"))
    on_path = shutil.which("argus", path=env.get("PATH"))
    if on_path:
        candidates.append((Path(on_path), "path"))
    for name in ("argus", "argus.exe"):
        candidates.append((Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin") / name,
                           "sys.prefix"))
    for path, source in candidates:
        if not path.is_file():
            continue
        version = _probe_version(path)
        if version is not None:
            return ArgusInfo(executable=path, version=version, source=source)
    return None


def _probe_version(executable: Path) -> str | None:
    try:
        completed = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True, timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION_RE.search(completed.stdout + completed.stderr)
    return match.group(1) if match else None


class ArgusIntegration:
    def __init__(
        self, *, executable: str | Path | None = None, project_root: Path | None = None,
        timeout: float = 600.0,
    ) -> None:
        self._configured = executable
        self._project_root = project_root
        self._timeout = timeout
        self._info: ArgusInfo | None = None
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    # -- discovery --------------------------------------------------------------------

    def info(self, *, refresh: bool = False) -> ArgusInfo | None:
        if self._info is None or refresh:
            self._info = discover_argus(configured=self._configured,
                                        project_root=self._project_root)
        return self._info

    def require(self) -> ArgusInfo:
        info = self.info()
        if info is None:
            raise ArgusIntegrationError("Argus is not installed or not found.",
                                        remediation=INSTALL_HINT)
        return info

    @property
    def available(self) -> bool:
        return self.info() is not None

    # -- validate -------------------------------------------------------------------------

    def validate(self, config_path: Path, *, test_id: str | None = None) -> ArgusValidationResult:  # noqa: E501
        """Framework-only validation: loads every test in the config's test_paths."""
        info = self.require()
        argv = [str(info.executable), "validate", "--config", str(config_path),
                "--framework-only"]
        completed = self._run(argv, cwd=config_path.parent, timeout=120)
        output = completed.stdout + completed.stderr
        issues = _parse_definition_errors(output, test_id)
        ready = completed.returncode == 0
        if ready and test_id:
            # `validate` loads definitions; `list` confirms the test is discoverable.
            listed = self._run([str(info.executable), "list", "--config", str(config_path)],
                               cwd=config_path.parent, timeout=60)
            if listed.returncode == 2:
                ready = False
                issues.extend(_parse_definition_errors(listed.stdout + listed.stderr, test_id))
            elif test_id not in listed.stdout:
                ready = False
                issues.append(ValidationIssue(
                    code="argus_not_listed", source="argus",
                    message=f"Argus did not list test {test_id!r}.",
                    fix="Check the test file is under the configured test_paths.",
                ))
        return ArgusValidationResult(ready=ready, output=output, issues=issues)

    # -- run ------------------------------------------------------------------------------

    def run_test(
        self,
        config_path: Path,
        test_id: str,
        *,
        on_output: Callable[[str], None] | None = None,
        extra_args: list[str] | None = None,
    ) -> ArgusRunResult:
        info = self.require()
        argv = [str(info.executable), "run", "--config", str(config_path), "--test", test_id,
                "--no-logs", *(extra_args or [])]
        results_dir = self._results_dir(config_path)
        before = _list_run_dirs(results_dir)
        started = time.time()
        _log.info("running %s", " ".join(argv))
        try:
            process = subprocess.Popen(
                argv, cwd=str(config_path.parent), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "TERM": "dumb"},
            )
        except OSError as exc:
            raise ArgusIntegrationError(f"Cannot start Argus: {exc}",
                                        remediation=INSTALL_HINT) from exc
        with self._lock:
            self._process = process
        lines: list[str] = []
        assert process.stdout is not None
        try:
            for line in process.stdout:
                lines.append(line)
                if on_output is not None:
                    on_output(line.rstrip("\n"))
                if time.time() - started > self._timeout:
                    process.kill()
                    lines.append(
                        f"\n[creator] Argus run exceeded {self._timeout}s and was killed.\n"
                    )
                    break
            exit_code = process.wait(timeout=30)
        finally:
            with self._lock:
                self._process = None
        output = "".join(lines)
        status = _status_for(exit_code)
        run_dir = _newest_run_dir(results_dir, exclude=before)
        report_path = run_dir / "report.json" if run_dir else None
        report = None
        if report_path and report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = None
        html = run_dir / "report.html" if run_dir and (run_dir / "report.html").is_file() else None
        return ArgusRunResult(
            exit_code=exit_code, status=status, output=output, report_path=report_path,
            report=report, html_report=html, results_dir=run_dir,
        )

    def cancel(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    # -- schema inspection ------------------------------------------------------------------

    def inspect_schema(self) -> dict[str, list[str]] | None:
        """Ask the *installed* Argus for its action/condition names (best effort).

        Runs a tiny script in Argus's own interpreter so the Creator never
        imports Argus internals into its process.
        """
        info = self.info()
        if info is None:
            return None
        python = _python_for(info.executable)
        if python is None:
            return None
        script = (
            "import json\n"
            "from argus.actions.base import ActionRegistry\n"
            "from argus.conditions.base import ConditionFactory\n"
            "from argus.conditions.builtin import register\n"
            "f = ConditionFactory(); register(f)\n"
            "print(json.dumps({'actions': ActionRegistry().names(), 'conditions': f.types()}))\n"
        )
        try:
            completed = subprocess.run([str(python), "-c", script], capture_output=True,
                                       text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        try:
            data = json.loads(completed.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None
        return {"actions": list(data.get("actions", [])),
                "conditions": list(data.get("conditions", []))}

    # -- internals ------------------------------------------------------------------------------

    def _run(self, argv: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:  # noqa: E501
        try:
            return subprocess.run(
                argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False,
                env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
            )
        except subprocess.TimeoutExpired as exc:
            raise ArgusIntegrationError(
                f"Argus did not respond within {timeout:.0f}s ({argv[1]}).",
                remediation="Check that configured devices are reachable, then retry.",
            ) from exc
        except OSError as exc:
            raise ArgusIntegrationError(f"Cannot run Argus: {exc}", remediation=INSTALL_HINT) from exc  # noqa: E501

    def _results_dir(self, config_path: Path) -> Path:
        try:
            import yaml

            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            results = (data.get("results") or {}).get("dir", "results")
        except Exception:  # noqa: BLE001 - fall back to the Argus default
            results = "results"
        path = Path(results)
        return path if path.is_absolute() else config_path.parent / path


def _python_for(executable: Path) -> Path | None:
    for name in ("python", "python3", "python.exe"):
        candidate = executable.parent / name
        if candidate.is_file():
            return candidate
    return None


def _status_for(exit_code: int) -> str:
    return {0: "passed", 1: "failed", 2: "definition_error", 3: "preflight_failed"}.get(
        exit_code, "error" if exit_code >= 0 else "cancelled"
    )


def _list_run_dirs(results_dir: Path) -> set[Path]:
    if not results_dir.is_dir():
        return set()
    return {p for p in results_dir.iterdir() if p.is_dir()}


def _newest_run_dir(results_dir: Path, *, exclude: set[Path]) -> Path | None:
    candidates = [p for p in _list_run_dirs(results_dir) if p not in exclude]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _parse_definition_errors(output: str, test_id: str | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    marker = "Test definitions"
    for line in output.splitlines():
        if "✗" in line and marker in line:
            detail = line.split(marker, 1)[1].strip()
            issues.append(ValidationIssue(
                code="argus_definition", source="argus",
                message=f"Argus rejected the test definitions: {detail}",
                fix="Open the YAML preview and fix the field Argus names.",
            ))
    if "TEST DEFINITION ERROR" in output:
        detail = output.split("TEST DEFINITION ERROR", 1)[1].strip()
        issues.append(ValidationIssue(
            code="argus_definition", source="argus",
            message=f"Argus rejected the test definition: {detail[:600]}",
        ))
    return issues
