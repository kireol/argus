"""Instrumentation client that reads the agent's status/state over the serial link."""

from __future__ import annotations

import json
from typing import Any, Protocol

from argus.adapters.esp32.protocol import AgentInfo
from argus.exceptions import DeviceConnectionError, InstrumentationError
from argus.instrumentation.client import InstrumentationClient, InstrumentationStatus
from argus.models.common import HealthCheckResult


class _LinkLike(Protocol):
    def request(self, cmd: str, *args: str) -> bytes: ...
    def hello(self) -> AgentInfo: ...


class SerialInstrumentationClient(InstrumentationClient):
    """``status``/``state`` JSON documents served by the firmware agent."""

    def __init__(self, link: _LinkLike, info: AgentInfo) -> None:
        self._link = link
        self._info = info

    def _fetch(self, cmd: str) -> Any:
        if cmd not in self._info.caps:
            raise InstrumentationError(
                f"Firmware agent {self._info.name!r} does not provide {cmd!r}.",
                remediation=f"Call argus.set{cmd.capitalize()}(...) in the firmware to enable it.",
            )
        try:
            raw = self._link.request(cmd)
        except DeviceConnectionError as exc:
            raise InstrumentationError(
                f"Instrumentation {cmd!r} request failed: {exc}",
                remediation=exc.remediation or "Check the serial connection.",
            ) from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstrumentationError(
                f"Instrumentation {cmd!r} returned invalid JSON: {raw[:80]!r}",
                remediation="The firmware agent must serialise status/state as a JSON object.",
            ) from exc

    def status(self) -> InstrumentationStatus:
        return InstrumentationStatus.model_validate(self._fetch("status"))

    def state(self) -> dict[str, Any]:
        data = self._fetch("state")
        if not isinstance(data, dict):
            raise InstrumentationError(
                f"Instrumentation 'state' returned non-object JSON: {data!r}",
                remediation="The firmware agent's state must be a JSON object.",
            )
        return data

    def capabilities(self) -> list[str]:
        return sorted(self._info.caps & {"status", "state"})

    def health_check(self) -> HealthCheckResult:
        try:
            info = self._link.hello()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(f"agent unreachable: {exc}")
        return HealthCheckResult.ok("serial instrumentation", agent=info.name)
