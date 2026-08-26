"""Fault injection — an extensible adapter interface.

The core engine only knows :class:`FaultInjector`: ``apply``/``clear`` a
:class:`Fault` and report ``capabilities``. Environment-specific mechanisms
(a proxy, tc/netem, a service mesh, an app debug endpoint) plug in through
the ``argus.stress.faults`` entry-point group or ``FaultRegistry.register``.

Two implementations ship:

* :class:`BackendFaultInjector` — wraps the Argus backend adapter's own HTTP
  client so *Argus's* requests (mutations, state reads) see latency, timeouts
  and error responses. It cannot degrade the application's network path —
  it advertises exactly that through ``capabilities``.
* :class:`FakeFaultInjector` — records faults for tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib import metadata
from typing import Any

from argus.adapters.backend import BackendAdapter
from argus.exceptions import BackendError, UTFError
from argus.stress.models import Fault

FAULT_TYPES = ("latency", "timeout", "disconnect", "http_error", "malformed_response",
               "empty_response", "duplicate_response")


class FaultInjector(ABC):
    name: str = "injector"
    #: Human-readable scope of what the injector can degrade.
    scope: str = ""

    @abstractmethod
    def capabilities(self) -> frozenset[str]:
        """Fault types this injector can apply."""

    @abstractmethod
    def apply(self, fault: Fault) -> None: ...

    @abstractmethod
    def clear(self, fault: Fault | None = None) -> None:
        """Clear one fault (or every active fault when ``None``)."""

    def supports(self, fault_type: str) -> bool:
        return fault_type in self.capabilities()

    def active(self) -> list[Fault]:
        return []

    def close(self) -> None:
        self.clear()


class FakeFaultInjector(FaultInjector):
    name = "fake"
    scope = "in-memory (tests)"

    def __init__(self, capabilities: frozenset[str] | None = None) -> None:
        self._caps = capabilities if capabilities is not None else frozenset(FAULT_TYPES)
        self._active: list[Fault] = []
        self.applied: list[Fault] = []
        self.cleared: list[Fault | None] = []

    def capabilities(self) -> frozenset[str]:
        return self._caps

    def apply(self, fault: Fault) -> None:
        if not self.supports(fault.fault_type):
            raise UTFError(f"fake injector cannot apply {fault.fault_type}")
        self._active.append(fault)
        self.applied.append(fault)

    def clear(self, fault: Fault | None = None) -> None:
        self.cleared.append(fault)
        if fault is None:
            self._active.clear()
        else:
            self._active = [f for f in self._active if f != fault]

    def active(self) -> list[Fault]:
        return list(self._active)


class _FaultyResponse:
    """A synthetic ``httpx.Response``-like object for injected HTTP faults."""

    def __init__(self, status_code: int, text: str, *, json_value: Any = None,
                 malformed: bool = False) -> None:
        self.status_code = status_code
        self.text = text
        self.is_success = 200 <= status_code < 300
        self._json = json_value
        self._malformed = malformed
        self.headers: dict[str, str] = {"content-type": "application/json"}

    def json(self) -> Any:
        if self._malformed:
            raise ValueError("malformed JSON (injected fault)")
        return self._json


class BackendFaultInjector(FaultInjector):
    """Degrade the Argus→backend path by wrapping ``BackendAdapter.request``."""

    name = "backend"
    scope = "Argus's own backend requests (not the application's network)"

    def __init__(self, backend: BackendAdapter, *, sleep: Any = None) -> None:
        self._backend = backend
        self._original = backend.request
        self._active: list[Fault] = []
        self._sleep = sleep
        self._patched = False
        self._duplicate_cache: Any = None

    def capabilities(self) -> frozenset[str]:
        return frozenset(FAULT_TYPES)

    def apply(self, fault: Fault) -> None:
        if not self.supports(fault.fault_type):
            raise UTFError(f"backend injector cannot apply {fault.fault_type}")
        self._active.append(fault)
        if not self._patched:
            self._backend.request = self._request  # type: ignore[method-assign]
            self._patched = True

    def clear(self, fault: Fault | None = None) -> None:
        if fault is None:
            self._active.clear()
        else:
            self._active = [f for f in self._active if f != fault]
        if not self._active and self._patched:
            self._backend.request = self._original  # type: ignore[method-assign]
            self._patched = False

    def active(self) -> list[Fault]:
        return list(self._active)

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        for fault in list(self._active):
            kind = fault.fault_type
            params = fault.parameters
            if kind == "latency":
                delay = float(params.get("seconds", 1.0))
                (self._sleep or _real_sleep)(delay)
            elif kind == "timeout":
                raise BackendError(f"Backend request {method} {endpoint} timed out (injected).",
                                   remediation="Injected fault; expected during chaos runs.")
            elif kind == "disconnect":
                raise BackendError(f"Backend request {method} {endpoint} failed: connection "
                                   "refused (injected).")
            elif kind == "http_error":
                status = int(params.get("status", 500))
                return _FaultyResponse(status, f'{{"error": "injected {status}"}}',
                                       json_value={"error": f"injected {status}"})
            elif kind == "malformed_response":
                return _FaultyResponse(200, "<html>not json</html>", malformed=True)
            elif kind == "empty_response":
                return _FaultyResponse(200, "", json_value=None)
            elif kind == "duplicate_response":
                if self._duplicate_cache is not None:
                    return self._duplicate_cache
        response = self._original(method, endpoint, **kwargs)
        if any(f.fault_type == "duplicate_response" for f in self._active):
            self._duplicate_cache = response
        return response


class FaultRegistry:
    ENTRY_POINT_GROUP = "argus.stress.faults"

    def __init__(self) -> None:
        self._factories: dict[str, Any] = {}
        self._load_entry_points()

    def register(self, name: str, factory: Any) -> None:
        """``factory(backend: BackendAdapter | None) -> FaultInjector``."""
        self._factories[name] = factory

    def create(self, name: str, backend: BackendAdapter | None) -> FaultInjector | None:
        if name == "backend":
            return BackendFaultInjector(backend) if backend is not None else None
        if name == "fake":
            return FakeFaultInjector()
        factory = self._factories.get(name)
        if factory is None:
            raise UTFError(f"Unknown fault injector {name!r}.",
                           remediation=f"Available: backend, fake, {', '.join(sorted(self._factories))}")  # noqa: E501
        return factory(backend)

    def names(self) -> list[str]:
        return sorted({"backend", "fake", *self._factories})

    def _load_entry_points(self) -> None:
        try:
            entry_points = list(metadata.entry_points(group=self.ENTRY_POINT_GROUP))
        except Exception:  # noqa: BLE001
            return
        for entry_point in entry_points:
            try:
                self._factories[entry_point.name] = entry_point.load()
            except Exception:  # noqa: BLE001
                continue


def _real_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


__all__ = ["FAULT_TYPES", "BackendFaultInjector", "FakeFaultInjector", "FaultInjector",
           "FaultRegistry"]
