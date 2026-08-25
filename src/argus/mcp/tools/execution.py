"""Execution tools: start runs, poll status, read events.

Runs execute on Argus worker threads (see ``argus.service.runs``); the tool
call may wait up to ``wait_seconds`` for completion while streaming MCP
progress notifications, then returns the run's current state. Anything still
running is polled with ``argus_get_run`` / ``argus_get_run_events``.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Annotated

import anyio
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from argus.engine.filters import build_filter
from argus.engine.runner import FailurePolicy
from argus.mcp.context import ServerContext
from argus.mcp.errors import guarded
from argus.mcp.schemas import (
    RunEvents,
    RunEventView,
    RunList,
    RunListItem,
    RunOutcome,
    RunStatusView,
)
from argus.mcp.tools.discovery import require_test
from argus.service.runs import RunRecord, RunRequest

EXECUTES = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
)
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

_POLL_INTERVAL = 0.5
_MAX_ARTIFACT_REFS = 30

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_execution_tools(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="argus_run_test",
        annotations=EXECUTES,
        description=(
            "Run ONE Argus test. SIDE EFFECTS: performs the test's real actions — it may "
            "change the configured backend's state and send input to a physical device. "
            "Pre-flight checks run first unless skip_preflight. Optionally restrict to a "
            "platform or one device. The call waits up to wait_seconds (progress is "
            "reported); if the run is still going it returns completed=false and a run_id "
            "to poll with argus_get_run. Returns status, duration, failures, and artifact "
            "ids. On failure call argus_diagnose_run next. Use argus_preflight first when "
            "unsure the environment is ready."
        ),
    )
    @guarded("argus_run_test")
    async def argus_run_test(
        test_id: Annotated[str, Field(min_length=1, description="Test ID, e.g. MOV-001.")],
        context: Context,
        platform: Annotated[
            str | None, Field(description="Only run on this platform label.")
        ] = None,
        device: Annotated[
            str | None, Field(description="Run on this configured device only.")
        ] = None,
        skip_preflight: Annotated[bool, Field(description="Not recommended.")] = False,
        save_comparisons: Annotated[
            bool, Field(description="Keep actual/expected/diff images even when verifies pass.")
        ] = False,
        wait_seconds: Annotated[
            float, Field(ge=0, description="How long to wait for completion (0 = return at once).")
        ] = 120,
    ) -> RunOutcome:
        require_test(ctx, test_id)
        request = RunRequest(
            filters=build_filter(test_ids=[test_id], platforms=[platform] if platform else None),
            failure_policy=FailurePolicy(stop_on_failure=False),
            skip_preflight=skip_preflight,
            save_comparisons=save_comparisons,
            device=device,
            label=f"run_test {test_id}",
        )
        record = ctx.service.start_run(request)
        await _wait(ctx, record, wait_seconds, context)
        return _outcome(ctx, record)

    @server.tool(
        name="argus_run_tests",
        annotations=EXECUTES,
        description=(
            "Run a SELECTION of Argus tests (by ids, feature, tags/tag expression, "
            "platform; no filters = every test). SIDE EFFECTS: performs real test actions "
            "on devices and the backend. Tests run sequentially; by default the run stops "
            "at the first failure (continue_on_failure=true runs everything, max_failures "
            "stops after N). Waits up to wait_seconds, then returns the run state and a "
            "run_id for argus_get_run / argus_get_run_events. Prefer argus_run_test for a "
            "single test."
        ),
    )
    @guarded("argus_run_tests")
    async def argus_run_tests(
        context: Context,
        test_ids: Annotated[list[str] | None, Field(description="Exact test IDs.")] = None,
        feature: Annotated[str | None, Field(description="Feature filter.")] = None,
        tags: Annotated[list[str] | None, Field(description="Tags or one tag expression.")] = None,
        platform: Annotated[str | None, Field(description="Platform filter.")] = None,
        device: Annotated[str | None, Field(description="Run on this device only.")] = None,
        continue_on_failure: Annotated[
            bool, Field(description="Keep going after a failure (default: stop).")
        ] = False,
        max_failures: Annotated[
            int | None, Field(ge=1, description="Stop after this many failures.")
        ] = None,
        skip_preflight: Annotated[bool, Field(description="Not recommended.")] = False,
        save_comparisons: Annotated[bool, Field(description="Keep comparison images.")] = False,
        wait_seconds: Annotated[
            float, Field(ge=0, description="How long to wait for completion.")
        ] = 120,
    ) -> RunOutcome:
        filters = build_filter(
            test_ids=test_ids,
            features=[feature] if feature else None,
            tags=tags,
            platforms=[platform] if platform else None,
        )
        request = RunRequest(
            filters=filters,
            failure_policy=FailurePolicy(
                stop_on_failure=not continue_on_failure and max_failures is None,
                max_failures=max_failures,
            ),
            skip_preflight=skip_preflight,
            save_comparisons=save_comparisons,
            device=device,
            label="run_tests",
        )
        record = ctx.service.start_run(request)
        await _wait(ctx, record, wait_seconds, context)
        return _outcome(ctx, record)

    @server.tool(
        name="argus_get_run",
        annotations=READ_ONLY,
        description=(
            "Status of a run started by argus_run_test/argus_run_tests: state "
            "(queued/running/completed/errored), engine status, counts, the test currently "
            "executing, stop reason and results directory. Read-only and cheap — poll it "
            "for long runs. For per-test outcomes read resource argus://runs/{run_id}; for "
            "failure details call argus_diagnose_run."
        ),
    )
    @guarded("argus_get_run")
    def argus_get_run(
        run_id: Annotated[str, Field(min_length=1, description="Run ID (run-…).")],
    ) -> RunStatusView:
        return RunStatusView.from_summary(ctx.service.require_run(run_id).summary())

    @server.tool(
        name="argus_get_run_events",
        annotations=READ_ONLY,
        description=(
            "Engine events for a run (run_started, preflight_check, test_started, "
            "action_completed, test_failed, run_completed, …) in order, from the Argus "
            "EventBus. Read-only. Pass `after` (the previous next_after) to page through "
            "new events while a run is live. Bounded: the server keeps the most recent "
            "events only and reports how many were dropped."
        ),
    )
    @guarded("argus_get_run_events")
    def argus_get_run_events(
        run_id: Annotated[str, Field(min_length=1)],
        after: Annotated[int, Field(ge=0, description="Return events with seq > after.")] = 0,
        limit: Annotated[int | None, Field(ge=1, description="Max events (bounded).")] = None,
    ) -> RunEvents:
        record = ctx.service.require_run(run_id)
        events, more = record.events(after=after, limit=ctx.bounded_limit(limit))
        return RunEvents(
            run_id=run_id,
            state=record.summary().state.value,
            events=[RunEventView.from_event(e) for e in events],
            next_after=events[-1].seq if events else after,
            has_more=more,
            dropped=record.dropped_events,
        )

    @server.tool(
        name="argus_list_runs",
        annotations=READ_ONLY,
        description=(
            "Recent runs known to this server (newest first) with state, status and "
            "counts. Read-only. Use it to recover a run_id."
        ),
    )
    @guarded("argus_list_runs")
    def argus_list_runs(
        limit: Annotated[int | None, Field(ge=1, description="Max runs (bounded).")] = None,
    ) -> RunList:
        items = [
            RunListItem.from_summary(s)
            for s in ctx.service.list_runs(limit=ctx.bounded_limit(limit))
        ]
        return RunList(items=items, total=len(items))


async def _wait(
    ctx: ServerContext, record: RunRecord, wait_seconds: float, context: Context
) -> None:
    """Wait (bounded) for the run, forwarding progress; never blocks the event loop."""
    budget = min(wait_seconds, ctx.limits.max_wait_seconds)
    deadline = anyio.current_time() + budget
    last_progress = -1.0
    while not record.wait(0):
        if anyio.current_time() >= deadline:
            return
        summary = record.summary()
        progress = float(summary.executed + summary.skipped)
        if progress > last_progress:
            last_progress = progress
            with contextlib.suppress(Exception):  # progress is best-effort
                await context.report_progress(
                    progress,
                    total=float(summary.total_tests) if summary.total_tests else None,
                    message=summary_message(summary.current_test),
                )
        await anyio.sleep(_POLL_INTERVAL)


def summary_message(current: dict | None) -> str:
    if not current:
        return "waiting for next test"
    platform = f" on {current['platform']}" if current.get("platform") else ""
    return f"running {current['test_id']}{platform}"


def _outcome(ctx: ServerContext, record: RunRecord) -> RunOutcome:
    artifact_ids: list[str] = []
    if record.summary().results_dir:
        artifact_ids = [
            a.artifact_id for a in ctx.service.list_artifacts(record.run_id)
        ][:_MAX_ARTIFACT_REFS]
    return RunOutcome.from_record(record, artifact_ids)
