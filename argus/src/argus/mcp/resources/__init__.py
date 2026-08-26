"""Resource registration.

Resources expose stable, read-only views as JSON. To add one: create a
module with ``register_<area>_resources(server, ctx)`` and append it below.
URI scheme: ``argus://<collection>[/<id>[/<sub>]]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from argus.mcp.context import ServerContext
from argus.mcp.resources.configuration import register_configuration_resources
from argus.mcp.resources.devices import register_device_resources
from argus.mcp.resources.runs import register_run_resources
from argus.mcp.resources.tests import register_test_resources

JSON = "application/json"

REGISTRARS = (
    register_test_resources,
    register_run_resources,
    register_device_resources,
    register_configuration_resources,
)

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_resources(server: MCPServer, ctx: ServerContext) -> None:
    for register in REGISTRARS:
        register(server, ctx)


def dump(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


__all__ = ["JSON", "REGISTRARS", "dump", "register_resources"]
