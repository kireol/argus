"""StressRunStore — persist and load run records (``results/stress/<run_id>/``)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from argus.artifacts.manager import safe_path_component
from argus.exceptions import UTFError
from argus.logging import redact
from argus.stress.models import StressRunRecord, TraceEvent
from argus.stress.trace import load_trace

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def new_run_id(now: datetime | None = None, *, suffix: str | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d_%H-%M-%S")
    return f"{stamp}-{suffix}" if suffix else stamp


class StressRunStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def run_dir(self, run_id: str, *, create: bool = False) -> Path:
        if not _RUN_ID_RE.match(run_id):
            raise UTFError(f"Invalid run id {run_id!r}.")
        path = self.base_dir / safe_path_component(run_id)
        if create:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            suffix = 1
            candidate = path
            while True:
                try:
                    candidate.mkdir()  # atomic: concurrent runs never share a directory
                    return candidate
                except FileExistsError:
                    suffix += 1
                    candidate = self.base_dir / f"{safe_path_component(run_id)}_{suffix}"
        return path

    def save(self, record: StressRunRecord, run_dir: Path) -> Path:
        path = run_dir / "run.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(redact(json.dumps(record.model_dump(mode="json"), indent=2, default=str)),
                       encoding="utf-8")
        tmp.replace(path)  # atomic: a crash never leaves a torn run.json
        return path

    def load(self, run_id: str) -> StressRunRecord:
        run_dir = self.resolve(run_id)
        path = run_dir / "run.json"
        if not path.is_file():
            raise UTFError(f"Run {run_id!r} has no run.json in {run_dir}.",
                           remediation="Use `argus stress list` to see recorded runs.")
        try:
            return StressRunRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            raise UTFError(f"Run record {path} is unreadable: {exc}") from exc

    def trace(self, run_id: str) -> list[TraceEvent]:
        return load_trace(self.resolve(run_id) / "trace.jsonl")

    def resolve(self, run_id: str) -> Path:
        """Accept a full id, a unique prefix, or ``latest``."""
        runs = self.list_runs()
        if run_id == "latest":
            if not runs:
                raise UTFError("No stress runs recorded yet.",
                               remediation="Run `argus stress` first.")
            return runs[-1]
        exact = self.base_dir / safe_path_component(run_id)
        if exact.is_dir():
            return exact
        matches = [p for p in runs if p.name.startswith(run_id)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise UTFError(f"Unknown stress run {run_id!r}.",
                           remediation=f"Recorded runs live under {self.base_dir}.")
        raise UTFError(f"Run id {run_id!r} is ambiguous: {', '.join(p.name for p in matches)}.")

    def list_runs(self) -> list[Path]:
        if not self.base_dir.is_dir():
            return []
        return sorted(p for p in self.base_dir.iterdir() if (p / "run.json").is_file())

    def records(self) -> list[StressRunRecord]:
        out: list[StressRunRecord] = []
        for path in self.list_runs():
            try:
                out.append(self.load(path.name))
            except UTFError:
                continue
        return out


__all__ = ["StressRunStore", "new_run_id"]
