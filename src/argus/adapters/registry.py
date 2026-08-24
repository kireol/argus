"""Device adapter registry.

Adapters register under a ``type`` name used in configuration
(``type: android``). Plugins can add adapters via the ``argus.devices``
entry-point group without touching core code.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import metadata

from argus.adapters.base import Device
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError

DeviceFactory = Callable[[str, DeviceConfig], Device]


class DeviceRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, DeviceFactory] = {}
        self._entry_points_loaded = False

    def register(self, type_name: str, factory: DeviceFactory) -> None:
        self._factories[type_name] = factory

    def create(self, name: str, config: DeviceConfig) -> Device:
        self._load_entry_points()
        factory = self._factories.get(config.type)
        if factory is None:
            raise ConfigurationError(
                f"Unknown device type {config.type!r} for device {name!r}.",
                remediation=f"Available types: {', '.join(sorted(self._factories))}.",
            )
        return factory(name, config)

    def types(self) -> list[str]:
        self._load_entry_points()
        return sorted(self._factories)

    def _load_entry_points(self) -> None:
        if self._entry_points_loaded:
            return
        self._entry_points_loaded = True
        register_builtin_devices(self)
        for entry_point in metadata.entry_points(group="argus.devices"):
            if entry_point.name == "builtin":
                continue  # already registered directly
            loader = entry_point.load()
            loader(self)


def register_builtin_devices(registry: DeviceRegistry) -> None:
    """Register the adapters that ship with the framework (lazy imports)."""
    from argus.adapters.android import AndroidAdapter
    from argus.adapters.browser import BrowserAdapter
    from argus.adapters.fake import FakeDevice
    from argus.adapters.roku import RokuAdapter
    from argus.adapters.yocto import YoctoAdapter

    registry.register("android", AndroidAdapter.from_config)
    registry.register("browser", BrowserAdapter.from_config)
    registry.register("roku", RokuAdapter.from_config)
    registry.register("yocto", YoctoAdapter.from_config)
    registry.register("fake", FakeDevice.from_config)


_default_registry = DeviceRegistry()


def create_device(name: str, config: DeviceConfig) -> Device:
    """Create a device from configuration using the default registry."""
    return _default_registry.create(name, config)
