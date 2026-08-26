"""MCP server assembly and transports.

``create_server`` builds an ``MCPServer`` whose tools, resources and prompts
close over one :class:`ServerContext`; ``run_server`` starts it over STDIO or
Streamable HTTP. Nothing here knows how tests run — that is
:class:`argus.service.ArgusService`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from argus import __version__
from argus.config.models import AppConfig, MCPConfig
from argus.exceptions import ConfigurationError
from argus.logging import get_logger
from argus.mcp import MCP_API_VERSION
from argus.mcp.auth import BearerTokenMiddleware, is_loopback
from argus.mcp.context import ServerContext
from argus.mcp.errors import require_sdk
from argus.service import ArgusService

if TYPE_CHECKING:
    from mcp.server import MCPServer

INSTRUCTIONS = """Argus runs functional and visual tests against real devices and apps.
Typical workflows:
- discover: argus_list_tests → argus_get_test
- run: argus_preflight → argus_run_test / argus_run_tests → argus_get_run
- debug: argus_diagnose_run → argus_list_artifacts → argus_get_artifact (images)
- environment: argus_list_devices → argus_validate → argus_get_device(probe=true)
Running tests and capturing screenshots interact with real devices and may modify
the configured backend; read-only tools are marked as such. Responses are bounded:
use cursors (`next_cursor`) and `after` to page."""


def create_server(
    config: AppConfig,
    *,
    service: ArgusService | None = None,
    token_verifier: Any | None = None,
    auth: Any | None = None,
) -> MCPServer:
    """Build the Argus MCP server (does not start a transport).

    ``token_verifier``/``auth`` are the SDK's OAuth resource-server hooks for
    deployments that need more than static bearer tokens.
    """
    require_sdk()
    from mcp.server import MCPServer

    from argus.mcp.prompts import register_prompts
    from argus.mcp.resources import register_resources
    from argus.mcp.tools import register_tools

    context = ServerContext(config=config, service=service or ArgusService(config))
    server: MCPServer = MCPServer(
        name="argus",
        title="Argus test framework",
        version=__version__,
        instructions=INSTRUCTIONS,
        token_verifier=token_verifier,
        auth=auth,
        log_level=config.logging.level.upper()  # type: ignore[arg-type]
        if config.logging.level.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        else "INFO",
    )
    register_tools(server, context)
    register_resources(server, context)
    register_prompts(server, context)
    return server


def build_http_app(server: MCPServer, mcp_config: MCPConfig) -> Any:
    """Starlette app for Streamable HTTP, with authentication applied."""
    from mcp.server.transport_security import TransportSecuritySettings

    tokens = mcp_config.auth.configured_tokens
    sdk_auth = server.settings.auth is not None
    if not tokens and not sdk_auth and not is_loopback(mcp_config.host):
        raise ConfigurationError(
            f"Refusing to serve MCP over HTTP on {mcp_config.host!r} without authentication.",
            remediation="Set mcp.auth.tokens (e.g. ['${ARGUS_MCP_TOKEN}']) or bind to "
            "127.0.0.1.",
        )
    security = None
    if mcp_config.allowed_hosts or mcp_config.allowed_origins:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(mcp_config.allowed_hosts),
            allowed_origins=list(mcp_config.allowed_origins),
        )
    app: Any = server.streamable_http_app(
        streamable_http_path=mcp_config.path,
        json_response=mcp_config.json_response,
        stateless_http=mcp_config.stateless_http,
        transport_security=security,
        host=mcp_config.host,
    )
    if tokens:
        app = BearerTokenMiddleware(app, tokens)
    return app


def run_server(
    config: AppConfig,
    *,
    transport: str | None = None,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
) -> None:
    """Start the server; CLI overrides take precedence over ``config.mcp``."""
    mcp_config = config.mcp.model_copy(
        update={
            k: v
            for k, v in (
                ("transport", transport),
                ("host", host),
                ("port", port),
                ("path", path),
            )
            if v is not None
        }
    )
    try:
        mcp_config = MCPConfig.model_validate(mcp_config.model_dump())
    except ValidationError as exc:
        raise ConfigurationError(
            f"Invalid MCP settings:\n{exc}",
            remediation="Use --transport stdio|streamable-http, a port in 1-65535, "
            "and a path starting with '/'.",
        ) from exc
    log = get_logger("argus.mcp")
    server = create_server(config)
    log.info(
        "Argus MCP server %s (API v%s) starting over %s",
        __version__,
        MCP_API_VERSION,
        mcp_config.transport,
        extra={"operation": "mcp.start"},
    )
    if mcp_config.transport == "stdio":
        # stdout is the protocol channel; Argus logging already targets stderr.
        server.run("stdio")
        return

    import uvicorn

    app = build_http_app(server, mcp_config)
    log.info(
        "Listening on http://%s:%d%s (auth: %s)",
        mcp_config.host,
        mcp_config.port,
        mcp_config.path,
        "bearer token" if mcp_config.auth.configured_tokens else "none (loopback only)",
        extra={"operation": "mcp.start"},
    )
    uvicorn.run(
        app,
        host=mcp_config.host,
        port=mcp_config.port,
        log_config=None,
        log_level=config.logging.level.lower(),
    )
