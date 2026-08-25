"""Failure diagnostics tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from argus.mcp.context import ServerContext
from argus.mcp.errors import guarded
from argus.mcp.schemas import DiagnosisView

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_diagnostic_tools(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="argus_diagnose_run",
        annotations=READ_ONLY,
        description=(
            "Structured facts about what failed in a run: per failed test the failing "
            "step, failure category, what was expected (verifier, image/text, threshold) "
            "versus observed (confidence, location), device platform, instrumentation "
            "state captured at failure, artifact ids, and a category hint. Also lists "
            "failed pre-flight checks with their remediation. Read-only; nothing is "
            "inferred — it reports what Argus recorded. Follow up with argus_get_artifact "
            "on the actual/expected/diff images."
        ),
    )
    @guarded("argus_diagnose_run")
    def argus_diagnose_run(
        run_id: Annotated[str, Field(min_length=1)],
        test_id: Annotated[str | None, Field(description="Only this test.")] = None,
    ) -> DiagnosisView:
        return DiagnosisView.from_diagnosis(ctx.service.diagnose_run(run_id, test_id=test_id))
