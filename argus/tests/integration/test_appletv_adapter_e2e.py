"""AppleTvAdapter against a real Apple TV. Skipped unless ARGUS_APPLETV_HOST is set."""

from __future__ import annotations

import json
import os
import time

import pytest

from argus.adapters.appletv import AppleTvAdapter

pytestmark = pytest.mark.integration

pytest.importorskip("pyatv")

HOST = os.environ.get("ARGUS_APPLETV_HOST")
APP_ID = os.environ.get("ARGUS_APPLETV_APP_ID", "com.apple.TVWatchList")
CREDENTIALS = os.environ.get("ARGUS_APPLETV_CREDENTIALS")


@pytest.fixture
def atv():
    if not HOST or not CREDENTIALS:
        pytest.skip("ARGUS_APPLETV_HOST / ARGUS_APPLETV_CREDENTIALS not set")
    device = AppleTvAdapter(
        "atv", app_id=APP_ID, host=HOST, credentials=json.loads(CREDENTIALS)
    )
    device.connect()
    yield device
    device.disconnect()


def test_launch_keys_and_playback_state(atv: AppleTvAdapter):
    assert atv.health_check().healthy
    atv.start_application()
    time.sleep(3)
    assert atv.is_application_running()
    atv.press_key("DPAD_DOWN")
    atv.press_key("MENU")
    state = atv.get_playback_state()
    assert state.state in {"playing", "paused", "stopped", "idle", "loading", "seeking"}
    atv.stop_application()
