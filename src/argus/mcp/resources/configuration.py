"""``argus://configuration`` resource — the effective configuration, redacted."""

from __future__ import annotations

from typing import TYPE_CHECKING

from argus import __version__
from argus.mcp import MCP_API_VERSION
from argus.mcp.context import ServerContext

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_configuration_resources(server: MCPServer, ctx: ServerContext) -> None:
    from argus.mcp.resources import JSON, dump

    @server.resource(
        "argus://configuration",
        name="argus-configuration",
        title="Effective configuration",
        mime_type=JSON,
        description="Merged Argus configuration with credentials, tokens and keys redacted.",
    )
    def configuration() -> str:
        data = ctx.service.redacted_config()
        data["mcp"].pop("auth", None)
        return dump(
            {
                "argus_version": __version__,
                "mcp_api_version": MCP_API_VERSION,
                "configuration": data,
            }
        )
