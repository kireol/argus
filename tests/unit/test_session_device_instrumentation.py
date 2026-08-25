"""InstrumentationConfig type: device -> Device.instrumentation_client()."""

from __future__ import annotations

import pytest

from argus.adapters.fake import FakeDevice, FakeInstrumentation
from argus.config.models import AppConfig, DeviceConfig
from argus.engine.session import RunSession
from argus.exceptions import ConfigurationError


class DeviceWithInstrumentation(FakeDevice):
    def instrumentation_client(self):
        return FakeInstrumentation(status={"screen": "serial", "capabilities": []})


def _session(device_type: str, base_config: AppConfig) -> RunSession:
    base_config.devices = {
        "board": DeviceConfig.model_validate(
            {"type": device_type, "instrumentation": {"type": "device"}}
        )
    }
    session = RunSession(base_config)
    session._device_registry.register(
        "with_instr", lambda name, cfg: DeviceWithInstrumentation(name)
    )
    return session


def test_device_type_uses_device_client(base_config):
    session = _session("with_instr", base_config)
    client = session.instrumentation("board")
    assert client is not None and client.status().screen == "serial"
    assert session.instrumentation("board") is client  # cached
    session.close()


def test_device_type_without_client_is_configuration_error(base_config):
    session = _session("fake", base_config)
    with pytest.raises(ConfigurationError, match="does not provide instrumentation"):
        session.instrumentation("board")
    session.close()


def test_base_device_default_is_none():
    assert FakeDevice().instrumentation_client() is None
