"""RokuAdapter against a real developer-mode Roku. Skipped unless ARGUS_ROKU_HOST is set."""

from __future__ import annotations

import os
import time

import pytest

from argus.adapters.roku import RokuAdapter

pytestmark = pytest.mark.integration

HOST = os.environ.get("ARGUS_ROKU_HOST")
DEV_PASSWORD = os.environ.get("ARGUS_ROKU_DEV_PASSWORD")


@pytest.fixture
def roku():
    if not HOST:
        pytest.skip("ARGUS_ROKU_HOST not set")
    device = RokuAdapter("tv", host=HOST, dev_password=DEV_PASSWORD)
    device.connect()
    yield device
    device.disconnect()


def test_device_info_and_keys(roku: RokuAdapter):
    health = roku.health_check()
    assert health.healthy
    assert roku.get_screen_info().width > 0
    roku.press_key("HOME")
    time.sleep(1)
    assert not roku.is_application_running()


def test_sideloaded_channel_screenshot(roku: RokuAdapter):
    if not DEV_PASSWORD:
        pytest.skip("ARGUS_ROKU_DEV_PASSWORD not set")
    roku.start_application()
    time.sleep(5)
    assert roku.is_application_running()
    img = roku.screenshot()
    assert img.size == roku.get_screen_info().size
    assert roku.get_logs() != ""
