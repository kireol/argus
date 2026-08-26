"""Environment validation and pre-flight tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from argus.engine.filters import build_filter
from argus.mcp.context import ServerContext
from argus.mcp.errors import guarded
from argus.mcp.schemas import PreflightOutcome, ValidationResult

# Validation connects to devices/backends (read-only probes) but changes nothing.
PROBE = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_validation_tools(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="argus_validate",
        annotations=PROBE,
        description=(
            "Validate the Argus installation and environment, like `argus validate`. "
            "framework_only=true checks only the installation (Python, dependencies, test "
            "definitions, OpenCV, OCR) and touches no device. framework_only=false also "
            "probes the backend and connects to every configured device (health, "
            "screenshot, instrumentation) — slower, and it takes a short exclusive lease "
            "on the devices. Returns structured checks plus failures, warnings and "
            "remediation. Use it when a run reports environment problems."
        ),
    )
    @guarded("argus_validate")
    def argus_validate(
        framework_only: Annotated[
            bool, Field(description="Installation checks only; no devices or backend.")
        ] = False,
    ) -> ValidationResult:
        report = ctx.service.validate(framework_only=framework_only)
        return ValidationResult.from_report(report, framework_only=framework_only)

    @server.tool(
        name="argus_preflight",
        annotations=PROBE,
        description=(
            "Run the exact pre-flight checks a real run of the selected tests would run "
            "(assets, backend, devices, screenshots, instrumentation, OCR) without "
            "executing any test — the equivalent of `argus --dry-run` for a selection. "
            "Connects to the required devices (exclusive lease while it runs) but changes "
            "nothing. Returns the requirements it derived from the tests, every check with "
            "pass/fail and remediation. Call this before argus_run_test when unsure the "
            "environment is ready."
        ),
    )
    @guarded("argus_preflight")
    def argus_preflight(
        test_ids: Annotated[list[str] | None, Field(description="Exact test IDs.")] = None,
        feature: Annotated[str | None, Field(description="Feature filter.")] = None,
        tags: Annotated[list[str] | None, Field(description="Tags or one tag expression.")] = None,
        platform: Annotated[str | None, Field(description="Platform filter.")] = None,
        device: Annotated[
            str | None, Field(description="Restrict to one configured device (implies platform)")
        ] = None,
    ) -> PreflightOutcome:
        filters = build_filter(
            test_ids=test_ids,
            features=[feature] if feature else None,
            tags=tags,
            platforms=[platform] if platform else None,
        )
        report = ctx.service.preflight(filters, device=device)
        return PreflightOutcome.from_report(report)
