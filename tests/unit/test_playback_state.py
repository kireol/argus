"""PlaybackState model and the Device.get_playback_state hook."""

from __future__ import annotations

import pytest

from argus.adapters.base import Device, DeviceCapabilities
from argus.adapters.fake import FakeDevice
from argus.exceptions import DeviceCapabilityError
from argus.models.common import HealthCheckResult, PlaybackState


class _MinimalDevice(Device):
    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities()

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def is_available(self) -> bool:
        return True

    def health_check(self) -> HealthCheckResult:
        return HealthCheckResult.ok()


def test_playback_state_defaults():
    state = PlaybackState(state="playing")
    assert state.title is None and state.position is None
    assert state.model_dump()["state"] == "playing"


def test_playback_state_rejects_unknown_state():
    with pytest.raises(ValueError):
        PlaybackState(state="dancing")


def test_default_hook_is_unsupported():
    device = _MinimalDevice("min")
    assert device.capabilities.supports_playback_state is False
    with pytest.raises(DeviceCapabilityError, match="get_playback_state"):
        device.get_playback_state()


def test_fake_device_reports_playback_state():
    device = FakeDevice()
    assert device.capabilities.supports_playback_state
    assert device.get_playback_state() == PlaybackState(state="idle")
    device.playback_state = PlaybackState(state="playing", title="Trailer", position=3.5)
    assert device.get_playback_state().title == "Trailer"
