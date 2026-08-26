"""Generic HTTP backend adapter (httpx).

Owns everything HTTP: base URL, auth, headers, timeouts, retries, TLS,
connection pooling. Test definitions never contain raw HTTP details.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from argus.config.models import BackendConfig
from argus.exceptions import BackendError
from argus.logging import get_logger
from argus.models.common import HealthCheckResult

_RETRYABLE_STATUS = {502, 503, 504}


class BackendAdapter:
    """Thin, typed client for the system-under-test's backend API."""

    def __init__(self, config: BackendConfig) -> None:
        if not config.configured:
            raise BackendError(
                "Backend is not configured.",
                remediation="Set backend.base_url in configuration "
                "(e.g. via the BACKEND_URL environment variable).",
            )
        self._config = config
        self._log = get_logger("argus.backend")
        headers = dict(config.headers)
        if config.token:
            headers[config.auth_header] = (
                f"{config.auth_scheme} {config.token}".strip()
                if config.auth_scheme
                else config.token
            )
        self._client = httpx.Client(
            base_url=config.base_url or "",
            headers=headers,
            timeout=config.timeout_seconds,
            verify=config.verify_tls,
        )

    # -- generic HTTP -------------------------------------------------------------

    def request(self, method: str, endpoint: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        attempts = self._config.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.request(method, endpoint, **kwargs)
                if response.status_code in _RETRYABLE_STATUS and attempt < attempts:
                    self._log.warning(
                        "Backend %s %s returned %s (attempt %d/%d), retrying",
                        method,
                        endpoint,
                        response.status_code,
                        attempt,
                        attempts,
                    )
                    time.sleep(min(0.5 * attempt, 2.0))
                    continue
                return response
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(min(0.5 * attempt, 2.0))
                    continue
                raise BackendError(
                    f"Backend request {method} {endpoint} timed out after "
                    f"{self._config.timeout_seconds}s ({attempt} attempts).",
                    remediation="Check backend availability and the backend.timeout setting.",
                ) from exc
            except httpx.HTTPError as exc:
                raise BackendError(
                    f"Backend request {method} {endpoint} failed: {exc}",
                    remediation="Check backend.base_url and network connectivity.",
                ) from exc
        raise BackendError(
            f"Backend request {method} {endpoint} failed: {last_error}"
        )

    def get(self, endpoint: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", endpoint, **kwargs)

    def put(self, endpoint: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", endpoint, **kwargs)

    def patch(self, endpoint: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", endpoint, **kwargs)

    def delete(self, endpoint: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", endpoint, **kwargs)

    # -- state helpers -------------------------------------------------------------

    def set_state(self, data: dict[str, Any], endpoint: str | None = None) -> Any:
        """Set backend application state (used to drive deterministic tests)."""
        target = endpoint or self._config.state_endpoint
        response = self.post(target, json=data)
        self._raise_for_status(response, "set_state")
        return _safe_json(response)

    def get_state(self, endpoint: str | None = None) -> Any:
        target = endpoint or self._config.state_endpoint
        response = self.get(target)
        self._raise_for_status(response, "get_state")
        return _safe_json(response)

    def health_check(self) -> HealthCheckResult:
        try:
            response = self.get(self._config.health_endpoint)
        except BackendError as exc:
            return HealthCheckResult.failed(str(exc))
        if response.is_success:
            return HealthCheckResult.ok(
                f"Backend reachable ({response.status_code})",
                status_code=response.status_code,
            )
        return HealthCheckResult.failed(
            f"Backend health endpoint returned {response.status_code}",
            status_code=response.status_code,
        )

    def close(self) -> None:
        self._client.close()

    def _raise_for_status(self, response: httpx.Response, operation: str) -> None:
        if not response.is_success:
            raise BackendError(
                f"Backend {operation} returned {response.status_code}: "
                f"{response.text[:500]}",
                remediation="Check the endpoint path and request payload.",
            )


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
