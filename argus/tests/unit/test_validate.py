"""argus validate: device instrumentation errors surface as FAIL rows, not tracebacks."""

from __future__ import annotations

from argus.cli.validate import CheckState, validate_environment
from argus.config.models import AppConfig, DeviceConfig


def test_device_type_instrumentation_without_client_is_fail_row(base_config: AppConfig):
    base_config.devices = {
        "board": DeviceConfig.model_validate(
            {"type": "fake", "instrumentation": {"type": "device"}}
        )
    }
    report = validate_environment(base_config)
    device_section = next(s for s in report.sections if s.title.startswith("Device: board"))
    instrumentation_items = [
        item for item in device_section.items if item.name == "Instrumentation"
    ]
    assert len(instrumentation_items) == 1
    item = instrumentation_items[0]
    assert item.state == CheckState.FAIL
    assert "does not provide instrumentation" in item.detail
