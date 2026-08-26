"""Recorder capabilities and target profiles.

Capabilities are explicit. A recorder adapter reports exactly what it can do;
the UI shows only supported actions/assertions, and every unsupported request
fails with :class:`UnsupportedCapabilityError` — never a silent no-op.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RecorderCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Input the target can *receive* (maps to Argus device actions)
    supports_tap: bool = False
    supports_swipe: bool = False
    supports_long_press: bool = False
    supports_drag: bool = False
    supports_pinch: bool = False
    supports_multi_touch: bool = False
    supports_keyboard: bool = False
    supports_mouse: bool = False
    supports_touch: bool = False
    #: Physical/navigation keys (BACK, HOME, volume...) can be *observed*.
    supports_hardware_keys: bool = False
    supports_app_lifecycle: bool = False
    # Observation the recorder can *produce*
    supports_screenshot: bool = False
    supports_live_screen: bool = False
    supports_ocr: bool = False
    supports_element_metadata: bool = False
    supports_logs: bool = False
    supports_instrumentation: bool = False
    supports_backend: bool = False
    supports_playback_state: bool = False
    # Whether user interactions can be *observed* (recorded) at all.
    supports_input_recording: bool = False
    #: Free-form notes shown to the user ("pinch is not observable via ADB").
    limitations: tuple[str, ...] = ()

    def enabled(self) -> list[str]:
        return sorted(
            name.removeprefix("supports_")
            for name, value in self.model_dump().items()
            if name.startswith("supports_") and value is True
        )

    def disabled(self) -> list[str]:
        return sorted(
            name.removeprefix("supports_")
            for name, value in self.model_dump().items()
            if name.startswith("supports_") and value is False
        )

    def has(self, name: str) -> bool:
        return bool(getattr(self, f"supports_{name}", False))


class TargetProfile(BaseModel):
    """A recordable target and how Argus should address the same thing.

    ``argus_device_type``/``argus_device_options`` become the ``devices:`` entry
    in the exported Argus configuration, so a recorded test can run unchanged.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    #: Recorder adapter kind ("fake", "browser", "desktop", "android", ...).
    adapter: str
    #: Argus platform label (used for ``platforms:`` in tests).
    platform: str
    argus_device_type: str
    argus_device_name: str = "device"
    argus_device_options: dict[str, Any] = Field(default_factory=dict)
    capabilities: RecorderCapabilities = Field(default_factory=RecorderCapabilities)
    #: Adapter-specific settings (URL, package, serial, demo scenario...).
    settings: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
