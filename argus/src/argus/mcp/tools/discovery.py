"""Discovery tools: find tests and read one definition."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from argus.engine.filters import build_filter
from argus.mcp.context import ServerContext
from argus.mcp.errors import InvalidArgumentError, guarded
from argus.mcp.pagination import paginate
from argus.mcp.schemas import TestDetail, TestList, TestSummary
from argus.models.test_definition import TestDefinition

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_discovery_tools(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="argus_list_tests",
        annotations=READ_ONLY,
        description=(
            "List Argus tests (id, name, feature, tags, platforms, short description) "
            "matching optional filters. Read-only. Use it first to find candidate tests; "
            "then argus_get_test for full steps. Filters combine with AND; a tag containing "
            "and/or/not is a boolean tag expression. Results are paginated: pass next_cursor "
            "back as cursor."
        ),
    )
    @guarded("argus_list_tests")
    def argus_list_tests(
        feature: Annotated[str | None, Field(description="Feature name (case-insensitive)")] = None,
        tags: Annotated[
            list[str] | None,
            Field(description='Tags, e.g. ["smoke"], or an expression "smoke and not slow"'),
        ] = None,
        platform: Annotated[str | None, Field(description="Platform label, e.g. android.")] = None,
        test_ids: Annotated[list[str] | None, Field(description="Exact test IDs.")] = None,
        query: Annotated[
            str | None, Field(description="Case-insensitive substring of id, name or description.")
        ] = None,
        limit: Annotated[int | None, Field(ge=1, description="Page size (server-bounded)")] = None,
        cursor: Annotated[str | None, Field(description="next_cursor of previous page")] = None,
    ) -> TestList:
        filters = build_filter(
            test_ids=test_ids,
            features=[feature] if feature else None,
            tags=tags,
            platforms=[platform] if platform else None,
        )
        tests = ctx.service.select_tests(filters)
        if query:
            needle = query.lower()
            tests = [
                t
                for t in tests
                if needle in t.id.lower()
                or needle in t.name.lower()
                or needle in t.description.lower()
            ]
        tests.sort(key=lambda t: (t.feature.lower(), t.id))
        page = paginate(tests, cursor=cursor, limit=ctx.bounded_limit(limit))
        return TestList(
            items=[TestSummary.from_definition(t) for t in page.items],
            total=page.total,
            truncated=page.truncated,
            next_cursor=page.next_cursor,
        )

    @server.tool(
        name="argus_get_test",
        annotations=READ_ONLY,
        description=(
            "Return the complete definition of one Argus test: metadata, requirements, "
            "parameters, retry policy, and every setup/step/teardown action with its "
            "parameters and conditions. Read-only. Use before running or editing a test."
        ),
    )
    @guarded("argus_get_test")
    def argus_get_test(
        test_id: Annotated[str, Field(min_length=1, description="Test ID, e.g. MOV-001.")],
    ) -> TestDetail:
        test = require_test(ctx, test_id)
        return TestDetail.from_definition(test, ctx.config.root_dir)


def require_test(ctx: ServerContext, test_id: str) -> TestDefinition:
    test = ctx.service.get_test(test_id)
    if test is None:
        known = [t.id for t in ctx.service.load_tests()]
        prefix = test_id.split("-", 1)[0].lower()
        similar = difflib.get_close_matches(test_id, known, n=5, cutoff=0.5) or [
            k for k in known if k.lower().startswith(prefix)
        ]
        hint = f" Similar IDs: {', '.join(similar[:5])}." if similar else ""
        raise InvalidArgumentError(
            f"Unknown test ID {test_id!r}.",
            remediation=f"Use argus_list_tests to find valid IDs.{hint}",
        )
    return test
