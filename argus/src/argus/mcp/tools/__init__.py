"""Tool registration.

To add a tool: create a module with ``register_<area>_tools(server, ctx)``,
define a typed function (inputs are the signature, output a Pydantic model
from :mod:`argus.mcp.schemas`), wrap it with ``@guarded``, call the
service — then append the registrar below. Order here is the order clients
see in ``tools/list``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from argus.mcp.context import ServerContext
from argus.mcp.tools.artifacts import register_artifact_tools
from argus.mcp.tools.devices import register_device_tools
from argus.mcp.tools.diagnostics import register_diagnostic_tools
from argus.mcp.tools.discovery import register_discovery_tools
from argus.mcp.tools.execution import register_execution_tools
from argus.mcp.tools.validation import register_validation_tools

REGISTRARS = (
    register_discovery_tools,
    register_validation_tools,
    register_execution_tools,
    register_device_tools,
    register_artifact_tools,
    register_diagnostic_tools,
)

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_tools(server: MCPServer, ctx: ServerContext) -> None:
    for register in REGISTRARS:
        register(server, ctx)


__all__ = ["REGISTRARS", "register_tools"]
