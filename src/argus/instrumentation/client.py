"""Application instrumentation protocol.

Applications may expose an HTTP endpoint reporting internal state
(current screen, readiness, rendering...). Instrumentation is *diagnostic
and complementary* — it never replaces black-box visual verification.

Capabilities are discoverable: applications advertise which fields/endpoints
they implement, and the framework only requires what a test actually uses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from argus.config.models import InstrumentationConfig
from argus.exceptions import InstrumentationError
from argus.models.common import HealthCheckResult


class InstrumentationStatus(BaseModel):
    """Standard (partially optional) instrumentation status payload."""

    model_config = ConfigDict(extra="allow")

    application: str | None = None
    version: str | None = None
    build: str | None = None
    ready: bool | None = None
    screen: str | None = None
    backend_connected: bool | None = None
    rendering: bool | None = None
    image_loaded: bool | None = None
    capabilities: list[str] = Field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        """Dotted-path access into the raw payload (e.g. ``state.movie_id``)."""
        data: Any = self.model_dump()
        for part in key.split("."):
            if isinstance(data, dict) and part in data:
                data = data[part]
            else:
                return default
        return data


class InstrumentationClient(ABC):
    """Abstract instrumentation transport."""

    @abstractmethod
    def status(self) -> InstrumentationStatus:
        ...

    @abstractmethod
    def state(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def health_check(self) -> HealthCheckResult:
        ...

    @abstractmethod
    def capabilities(self) -> list[str]:
        ...

    def close(self) -> None:  # noqa: B027 - optional hook
        pass


class HttpInstrumentationClient(InstrumentationClient):
    """HTTP-based instrumentation (the initial standard transport)."""

    def __init__(self, config: InstrumentationConfig) -> None:
        if not config.configured:
            raise InstrumentationError(
                "Instrumentation is not configured.",
                remediation="Set instrumentation.base_url for the device.",
            )
        self._config = config
        self._client = httpx.Client(
            base_url=config.base_url or "", timeout=config.timeout_seconds
        )

    def status(self) -> InstrumentationStatus:
        payload = self._get_json(self._config.status_endpoint)
        return InstrumentationStatus.model_validate(payload)

    def state(self) -> dict[str, Any]:
        payload = self._get_json(self._config.state_endpoint)
        if not isinstance(payload, dict):
            raise InstrumentationError(
                f"Instrumentation state endpoint returned non-object: {payload!r}"
            )
        return payload

    def capabilities(self) -> list[str]:
        return self.status().capabilities

    def health_check(self) -> HealthCheckResult:
        try:
            response = self._client.get(self._config.health_endpoint)
        except httpx.HTTPError as exc:
            return HealthCheckResult.failed(f"Instrumentation unreachable: {exc}")
        if response.is_success:
            return HealthCheckResult.ok("Instrumentation reachable")
        return HealthCheckResult.failed(
            f"Instrumentation health endpoint returned {response.status_code}"
        )

    def close(self) -> None:
        self._client.close()

    def _get_json(self, endpoint: str) -> Any:
        try:
            response = self._client.get(endpoint)
        except httpx.HTTPError as exc:
            raise InstrumentationError(
                f"Instrumentation request GET {endpoint} failed: {exc}",
                remediation="Check the application is running and instrumentation "
                "base_url is correct.",
            ) from exc
        if not response.is_success:
            raise InstrumentationError(
                f"Instrumentation GET {endpoint} returned {response.status_code}."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise InstrumentationError(
                f"Instrumentation GET {endpoint} returned invalid JSON."
            ) from exc
