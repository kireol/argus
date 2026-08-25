"""Authentication for the Streamable HTTP transport.

Two options, both wired by :func:`argus.mcp.server.build_http_app`:

* **Static bearer tokens** (``mcp.auth.tokens`` — reference them as
  ``${ENV_VAR}``): a small ASGI middleware compares the ``Authorization``
  header in constant time. Suitable for CI and shared lab servers.
* **OAuth / JWT**: pass a ``TokenVerifier`` (and ``AuthSettings``) to
  :func:`argus.mcp.server.create_server`; the SDK then handles protected
  resource metadata and token validation.

Localhost is *not* treated as trusted: a server bound to a non-loopback
address refuses to start without one of the above.
"""

from __future__ import annotations

import hmac
from collections.abc import Sequence
from typing import Any

from argus.logging import get_logger

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def is_loopback(host: str) -> bool:
    return host in _LOOPBACK


class BearerTokenMiddleware:
    """ASGI middleware rejecting requests without a configured bearer token."""

    def __init__(self, app: Any, tokens: Sequence[str]) -> None:
        if not tokens:
            raise ValueError("BearerTokenMiddleware needs at least one token")
        self._app = app
        self._tokens = [t.encode("utf-8") for t in tokens]
        self._log = get_logger("argus.mcp.auth")

    def _authorized(self, headers: list[tuple[bytes, bytes]]) -> bool:
        presented = next((v for k, v in headers if k.lower() == b"authorization"), None)
        if presented is None:
            return False
        scheme, _, token = presented.strip().partition(b" ")
        if scheme.lower() != b"bearer" or not token:
            return False
        token = token.strip()
        # Compare against every token so timing does not reveal which one matched.
        return any(hmac.compare_digest(token, expected) for expected in self._tokens)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if self._authorized(scope.get("headers", [])):
            await self._app(scope, receive, send)
            return
        self._log.warning(
            "Rejected unauthenticated request %s %s",
            scope.get("method"),
            scope.get("path"),
            extra={"operation": "mcp.auth"},
        )
        body = b'{"error":"unauthorized","message":"A valid bearer token is required."}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Bearer realm="argus-mcp"'),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
