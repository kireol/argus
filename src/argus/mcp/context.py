"""Explicit dependencies for tool/resource/prompt registration.

Nothing in the MCP package is global: ``create_server`` builds one
``ServerContext`` and every ``register_*`` function closes over it.
"""

from __future__ import annotations

from dataclasses import dataclass

from argus.config.models import AppConfig, MCPLimitsConfig
from argus.service import ArgusService


@dataclass(frozen=True)
class ServerContext:
    config: AppConfig
    service: ArgusService

    @property
    def limits(self) -> MCPLimitsConfig:
        return self.config.mcp.limits

    def bounded_limit(self, requested: int | None) -> int:
        """Clamp a caller-supplied page size to the configured maximum."""
        maximum = self.limits.max_results
        if requested is None:
            return min(20, maximum)
        return max(1, min(requested, maximum))
