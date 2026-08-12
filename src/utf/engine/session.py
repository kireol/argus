"""RunSession — shared, lazily-initialized services for one test session.

Devices stay connected for the whole session; HTTP clients pool connections;
reference images are cached once. Nothing here is a global: the session is
created by the runner (or a future GUI service) and injected everywhere.
"""

from __future__ import annotations

from pathlib import Path

from utf.actions.base import ActionRegistry
from utf.adapters.backend import BackendAdapter
from utf.adapters.base import Device
from utf.adapters.registry import DeviceRegistry
from utf.conditions.base import ConditionFactory
from utf.conditions.builtin import register as register_conditions
from utf.config.models import AppConfig
from utf.engine.context import VerifierBundle
from utf.events.bus import EventBus
from utf.exceptions import ConfigurationError
from utf.instrumentation.client import HttpInstrumentationClient, InstrumentationClient
from utf.logging import get_logger
from utf.verifiers.assets import AssetStore


class RunSession:
    """Owns adapters and shared caches for the duration of one run."""

    def __init__(self, config: AppConfig, events: EventBus | None = None) -> None:
        self.config = config
        self.events = events or EventBus()
        self.log = get_logger("utf.session")

        self.asset_paths: list[Path] = [
            config.resolve_path(p) for p in config.asset_paths
        ]
        self.assets = AssetStore(self.asset_paths)
        self.verifiers = VerifierBundle(assets=self.assets, config=config)

        self.actions = ActionRegistry()
        self.conditions = ConditionFactory()
        register_conditions(self.conditions)

        self._device_registry = DeviceRegistry()
        self._devices: dict[str, Device] = {}
        self._instrumentation: dict[str, InstrumentationClient | None] = {}
        self._backend: BackendAdapter | None = None

    # -- backend -----------------------------------------------------------------

    @property
    def backend(self) -> BackendAdapter:
        if self._backend is None:
            if self.config.backend.type == "fake":
                from utf.adapters.fake import FakeBackend

                self._backend = FakeBackend(self.config.backend.initial_state)
            else:
                self._backend = BackendAdapter(self.config.backend)
        return self._backend

    @property
    def backend_available(self) -> bool:
        return self.config.backend.configured

    # -- devices ------------------------------------------------------------------

    def device(self, name: str) -> Device:
        """Get (and connect) a configured device; connections are reused."""
        if name in self._devices:
            return self._devices[name]
        device_config = self.config.devices.get(name)
        if device_config is None:
            raise ConfigurationError(
                f"Unknown device {name!r}.",
                remediation="Configured devices: "
                f"{', '.join(sorted(self.config.devices)) or '<none>'}.",
            )
        device = self._device_registry.create(name, device_config)
        device.connect()
        self._bind_fake_world(device)
        self._devices[name] = device
        return device

    def _bind_fake_world(self, device: Device) -> None:
        """Let a FakeDevice render the FakeBackend's state (demo/self-test mode)."""
        from utf.adapters.fake import FakeDevice

        if isinstance(device, FakeDevice) and self.config.backend.type == "fake":
            backend = self.backend
            device.bind_state_provider(lambda: backend.get_state())

    def devices_for_platform(self, platform: str) -> list[str]:
        return sorted(self.config.devices_for_platform(platform))

    def select_device_names(
        self, platforms: list[str] | None, required: list[str] | None = None
    ) -> list[str]:
        """Device names needed for a run (explicit requirements win)."""
        names: list[str] = []
        if required:
            names.extend(required)
        if platforms:
            for platform in platforms:
                names.extend(self.devices_for_platform(platform))
        seen: set[str] = set()
        unique = [n for n in names if not (n in seen or seen.add(n))]
        return unique

    # -- instrumentation ---------------------------------------------------------------

    def instrumentation(self, device_name: str) -> InstrumentationClient | None:
        if device_name in self._instrumentation:
            return self._instrumentation[device_name]
        device_config = self.config.devices.get(device_name)
        client: InstrumentationClient | None = None
        if device_config is not None and device_config.instrumentation is not None:
            instr = device_config.instrumentation
            if instr.type == "fake":
                from utf.adapters.fake import FakeInstrumentation

                client = FakeInstrumentation(
                    status=dict(instr.status) or None, state=dict(instr.state) or None
                )
            elif instr.configured:
                client = HttpInstrumentationClient(instr)
        self._instrumentation[device_name] = client
        return client

    # -- lifecycle ----------------------------------------------------------------------

    def close(self) -> None:
        for name, device in self._devices.items():
            try:
                device.disconnect()
            except Exception:  # noqa: BLE001 - never fail a run on cleanup
                self.log.warning("Error disconnecting device %s", name)
        self._devices.clear()
        for client in self._instrumentation.values():
            if client is not None:
                client.close()
        self._instrumentation.clear()
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    def __enter__(self) -> RunSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
