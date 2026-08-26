"""Machine-readable JSON report (the contract for the future GUI/CI)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus import __version__
from argus.models.results import RunResult


def run_result_to_dict(result: RunResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "framework_version": __version__,
        "run": result.model_dump(mode="json"),
        "summary": {
            "status": result.status.value,
            "executed": result.executed,
            "passed": result.passed_count,
            "failed": result.failed_count,
            "skipped": result.skipped_count,
            "duration": result.duration,
        },
    }


def write_json_report(result: RunResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(run_result_to_dict(result), indent=2, default=str), encoding="utf-8"
    )
    return path
