"""Model Context Protocol support (optional: ``pip install "argus[mcp]"``).

Importing this package never imports the ``mcp`` SDK; ``create_server`` and
``run_server`` raise a remediated ``ConfigurationError`` when it is missing.
"""

from __future__ import annotations

#: Version of the MCP tool/resource contract (independent of the Argus version).
#: Bump the major part only for breaking changes to tool names, argument
#: semantics, output models, or resource URIs; see docs/mcp.md "Versioning".
MCP_API_VERSION = "1.0"

__all__ = ["MCP_API_VERSION"]
