"""Device, backend, and screenshot adapters."""

from argus.adapters.base import Device, DeviceCapabilities, ScreenshotProvider
from argus.adapters.registry import DeviceRegistry, create_device

__all__ = [
    "Device",
    "DeviceCapabilities",
    "DeviceRegistry",
    "ScreenshotProvider",
    "create_device",
]
