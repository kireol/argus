"""``argus://tests`` resources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError

from argus.exceptions import UTFError
from argus.mcp.context import ServerContext
from argus.mcp.schemas import TestDetail, TestSummary

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_test_resources(server: MCPServer, ctx: ServerContext) -> None:
    from argus.mcp.resources import JSON, dump

    @server.resource(
        "argus://tests",
        name="argus-tests",
        title="Test index",
        mime_type=JSON,
        description="Index of every Argus test (bounded; use argus_list_tests to page).",
    )
    def tests_index() -> str:
        try:
            tests = sorted(ctx.service.load_tests(), key=lambda t: (t.feature.lower(), t.id))
        except UTFError as exc:
            raise ResourceError(str(exc)) from exc
        limit = ctx.limits.max_results
        return dump(
            {
                "items": [TestSummary.from_definition(t).model_dump() for t in tests[:limit]],
                "total": len(tests),
                "truncated": len(tests) > limit,
            }
        )

    @server.resource(
        "argus://tests/{test_id}",
        name="argus-test",
        title="Test definition",
        mime_type=JSON,
        description="Complete definition of one test.",
    )
    def test_detail(test_id: str) -> str:
        try:
            test = ctx.service.get_test(test_id)
        except UTFError as exc:
            raise ResourceError(str(exc)) from exc
        if test is None:
            raise ResourceNotFoundError(f"Unknown test {test_id!r}")
        return dump(TestDetail.from_definition(test, ctx.config.root_dir).model_dump())
