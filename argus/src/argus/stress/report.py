"""Human-readable summaries for stress runs (console) and the JSON report."""

from __future__ import annotations

import json
from pathlib import Path

from argus.logging import redact
from argus.stress.models import Failure, FailureSeverity, StressRunRecord, TraceEvent

_SEVERITY_ORDER = (FailureSeverity.CRITICAL, FailureSeverity.ERROR, FailureSeverity.WARNING,
                   FailureSeverity.INFO)


def format_duration(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{seconds:.1f}s" if seconds < 10 else f"{secs}s"


def render_summary(record: StressRunRecord) -> str:
    s = record.summary
    lines = [
        "Argus Stress Run",
        "----------------",
        "",
        f"Run ID:       {record.run_id}",
        f"Seed:         {record.seed}",
        f"Scenario:     {record.scenario_name}",
        f"Device:       {record.device or '—'}" + (f" ({record.device_type})" if record.device_type else ""),  # noqa: E501
        f"Duration:     {format_duration(s.duration)}",
        f"Status:       {record.status}" + (f" — {s.stop_reason}" if s.stop_reason else ""),
    ]
    if record.dry_run:
        lines.append("Mode:         DRY RUN (no mutations were applied)")
    lines += [
        "",
        f"Actions:      {s.actions:,}",
        f"Mutations:    {s.mutations:,}" + (f"  (blocked: {s.mutations_blocked:,})" if s.mutations_blocked else ""),  # noqa: E501
        f"Faults:       {s.faults:,}",
        f"Observations: {s.observations:,}",
        "",
        "Failures:",
    ]
    app_failures = [f for f in record.failures if f.category.is_application]
    for severity in _SEVERITY_ORDER:
        count = len([f for f in app_failures if f.severity == severity])
        lines.append(f"  {severity.value.title() + ':':<12}{count}")
    infra = [f for f in record.failures if not f.category.is_application]
    lines += ["", f"Reproducible failures:\n  {s.reproducible_failures}"]
    if infra or record.infrastructure_errors:
        lines += ["", f"Infrastructure issues (not application bugs): "
                  f"{len(infra) + len(record.infrastructure_errors)}"]
        for message in record.infrastructure_errors[:5]:
            lines.append(f"  - {message}")
    if record.artifacts_dir:
        lines += ["", "Artifacts:", f"  {record.artifacts_dir}"]
        if record.trace_path:
            lines.append(f"  {record.trace_path}")
    lines += ["", "Replay:", f"  {record.replay_command}",
              "Seed reproduction:", f"  {record.seed_command}"]
    return "\n".join(lines)


def render_failure(record: StressRunRecord, failure: Failure, trace: list[TraceEvent] | None,
                   *, index: int) -> str:
    lines = [
        f"Failure #{index}  ({failure.failure_id})",
        f"Category: {failure.category.value.replace('_', ' ').title()}",
        f"Severity: {failure.severity.value.upper()}",
        f"Detector: {failure.detector}  (confidence {failure.confidence:.0%})",
        "",
        "Sequence:",
    ]
    events = trace or []
    wanted = set(failure.recent_sequence[-6:]) if failure.recent_sequence else set()
    shown = [e for e in events if e.sequence in wanted] if wanted else []
    if not shown and events:
        shown = [e for e in events if e.sequence <= failure.step + 1][-6:]
    for event in shown:
        lines.append(f"  {event.sequence:>4} {event.describe()}")
    if not shown:
        if failure.action is not None:
            lines.append(f"  {failure.step:>4} {failure.action.describe()}")
        if failure.mutation is not None:
            lines.append(f"       MUTATION   {failure.mutation.describe()}")
    lines += ["", "Likely issue:", f"  {failure.message}", "", "Seed:", f"  {record.seed}",
              "", "Replay:", f"  {record.replay_command}"]
    if failure.evidence.get("dir"):
        lines += ["", "Evidence:", f"  {failure.evidence['dir']}"]
    return "\n".join(lines)


def render_dry_run(trace: list[TraceEvent], *, seed: int) -> str:
    lines = [f"Seed: {seed}", ""]
    for event in trace:
        if event.event_type.value in ("run_started", "run_finished", "observation"):
            continue
        lines.append(f"{event.sequence:02d} {event.describe()}")
    return "\n".join(lines)


def write_json_report(record: StressRunRecord, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(json.dumps(record.model_dump(mode="json"), indent=2, default=str)),
                    encoding="utf-8")
    return path


__all__ = ["format_duration", "render_dry_run", "render_failure", "render_summary",
           "write_json_report"]
