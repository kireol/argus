"""Device, backend, and screenshot adapters."""

from utf.adapters.base import Device, DeviceCapabilities, ScreenshotProvider
from utf.adapters.registry import DeviceRegistry, create_device

__all__ = [
    "Device",
    "DeviceCapabilities",
    "DeviceRegistry",
    "ScreenshotProvider",
    "create_device",
]
