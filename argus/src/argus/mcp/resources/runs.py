"""``argus://runs`` resources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.mcpserver.exceptions import ResourceNotFoundError

from argus.mcp.context import ServerContext
from argus.mcp.schemas import RunListItem, RunStatusView, TestOutcome
from argus.reporting import run_result_to_dict
from argus.service.runs import RunRecord

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_run_resources(server: MCPServer, ctx: ServerContext) -> None:
    from argus.mcp.resources import JSON, dump

    def require(run_id: str) -> RunRecord:
        record = ctx.service.get_run(run_id)
        if record is None:
            raise ResourceNotFoundError(f"Unknown run {run_id!r}")
        return record

    @server.resource(
        "argus://runs",
        name="argus-runs",
        title="Recent runs",
        mime_type=JSON,
        description="Runs known to this server, newest first (bounded).",
    )
    def runs_index() -> str:
        limit = ctx.limits.max_results
        items = [
            RunListItem.from_summary(s).model_dump() for s in ctx.service.list_runs(limit=limit)
        ]
        return dump({"items": items, "total": len(items)})

    @server.resource(
        "argus://runs/{run_id}",
        name="argus-run",
        title="Run status and per-test outcomes",
        mime_type=JSON,
        description="Run status plus one entry per executed test (compact).",
    )
    def run_detail(run_id: str) -> str:
        record = require(run_id)
        view = RunStatusView.from_summary(record.summary()).model_dump()
        tests = (
            [TestOutcome.from_result(t).model_dump() for t in record.result.tests]
            if record.result
            else []
        )
        return dump({**view, "tests": tests})

    @server.resource(
        "argus://runs/{run_id}/report",
        name="argus-run-report",
        title="Run report",
        mime_type=JSON,
        description="The run's report.json content (same schema the CLI writes).",
    )
    def run_report(run_id: str) -> str:
        record = require(run_id)
        if record.result is None:
            raise ResourceNotFoundError(f"Run {run_id!r} has no report yet")
        return dump(run_result_to_dict(record.result))

    @server.resource(
        "argus://runs/{run_id}/test/{test_id}",
        name="argus-run-test",
        title="Test result within a run",
        mime_type=JSON,
        description="Every execution of one test in a run, with step results.",
    )
    def run_test_detail(run_id: str, test_id: str) -> str:
        record = require(run_id)
        executed = record.result.tests if record.result else []
        results = [t for t in executed if t.test_id == test_id]
        if not results:
            raise ResourceNotFoundError(f"Test {test_id!r} was not executed in run {run_id!r}")
        return dump([t.model_dump(mode="json") for t in results])
