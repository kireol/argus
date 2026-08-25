"""Prompt registration. To add a prompt, append a registrar here."""

from __future__ import annotations

from typing import TYPE_CHECKING

from argus.mcp.context import ServerContext
from argus.mcp.prompts.testing import register_testing_prompts

REGISTRARS = (register_testing_prompts,)

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_prompts(server: MCPServer, ctx: ServerContext) -> None:
    for register in REGISTRARS:
        register(server, ctx)


__all__ = ["REGISTRARS", "register_prompts"]
