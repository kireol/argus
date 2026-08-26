from __future__ import annotations

import time
from pathlib import Path

import pytest

from argus_test_creator.adapters.android import AndroidRecorder, FakeAdbClient, FakeDevice
from argus_test_creator.adapters.android.diagnostics import AndroidRecordingDiagnostics
from argus_test_creator.adapters.android.models import AndroidDevice
from argus_test_creator.app import CreatorApp
from argus_test_creator.observation import FakeOCRProvider
from argus_test_creator.recording import RecorderRegistry
from argus_test_creator.ui.main_window import MainWindow
from argus_test_creator.ui.widgets.android_panel import AndroidPanel

pytestmark = pytest.mark.ui
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "android"


def test_android_panel_device_selection(qtbot):
    panel = AndroidPanel()
    qtbot.addWidget(panel)
    chosen = []
    panel.device_selected.connect(chosen.append)
    panel.set_devices([AndroidDevice(serial="A1", state="device", model="Pixel_8"),
                       AndroidDevice(serial="B2", state="unauthorized")])
    assert panel.device_combo.itemText(0) == "Choose a device…"
    assert panel.selected_serial() == ""
    panel.device_combo.setCurrentIndex(1)
    assert chosen == ["A1"]
    assert panel.device_combo.itemText(2).endswith("[unauthorized]")
    panel.set_devices([AndroidDevice(serial="Z9", state="device")])
    assert panel.selected_serial() == "Z9"  # a single usable device is preselected
    panel.set_devices([])
    assert panel.selected_serial() == ""


def test_android_panel_states(qtbot):
    panel = AndroidPanel()
    qtbot.addWidget(panel)
    panel.show()
    diagnostics = AndroidRecordingDiagnostics()
    diagnostics.update(input_device="/dev/input/event2", input_device_name="touch",
                       touchscreen=True, screen_size=(1080, 2400))
    panel.show_connected(diagnostics.snapshot())
    assert "Connected" in panel.status_label.text()
    assert "touch (touchscreen)" in panel.input_label.text()
    assert panel.resolution_label.text() == "1080 × 2400"
    assert not panel.raw_label.isVisible()
    diagnostics.count_raw(1238)
    diagnostics.gesture("Swipe (1, 2) → (3, 4)")
    panel.show_recording(diagnostics.snapshot(), 14)
    assert panel.raw_label.text() == "1,238" and panel.actions_label.text() == "14"
    assert panel.current_label.text().startswith("Swipe")
    assert panel.raw_label.isVisible() and not panel.device_combo.isEnabled()
    panel.show_disconnected(lost=True)
    assert panel.reconnect_button.isVisible() and "paused" in panel.status_label.text()
    panel.show_disconnected()
    assert not panel.reconnect_button.isVisible()


@pytest.fixture
def android_window(qtbot, tmp_path):
    fake = FakeAdbClient([FakeDevice("ABC123", width=1080, height=2400)])
    for name in ("tap_event.txt", "key_event.txt"):
        fake.script_fixture("ABC123", FIXTURES / name)
    registry = RecorderRegistry()
    registry.register("android", lambda t, o: AndroidRecorder(t, o, adb=fake))
    registry.register("fake", __import__(
        "argus_test_creator.adapters.fake", fromlist=["FakeRecorder"]).FakeRecorder)
    app = CreatorApp(registry=registry, ocr=FakeOCRProvider())
    app.config.recording.settle_ms = 0
    app.config.recording.live_preview_fps = 30
    app.create_project(tmp_path / "proj", name="ui")
    app.new_test(test_id="UI-A", name="android ui", feature="UI")
    win = MainWindow(app)
    win.prompt_on_close = False
    qtbot.addWidget(win)
    win.show()
    yield win, fake
    win._preview_timer.stop()
    win._android_timer.stop()
    win._bridge.close()
    app.shutdown()


def test_main_window_android_panel_lifecycle(qtbot, android_window):
    win, fake = android_window
    assert not win.android_panel.isVisible()
    index = win.target_combo.findData("android")
    win.target_combo.setCurrentIndex(index)
    assert win.android_panel.isVisible()
    qtbot.waitUntil(lambda: win.android_panel.selected_serial() == "ABC123", timeout=5000)
    win._toggle_connect()
    qtbot.waitUntil(lambda: "Connected" in win.android_panel.status_label.text(), timeout=5000)
    assert win.android_panel.resolution_label.text() == "1080 × 2400"
    assert not win.remote.isHidden()  # controlled input still available
    win._record()
    qtbot.waitUntil(lambda: win.android_panel.actions_label.text() == "3", timeout=5000)
    assert win._android_timer.isActive()
    assert "Recording" in win.android_panel.status_label.text()
    assert int(win.android_panel.raw_label.text().replace(",", "")) > 10
    win._android_diagnostics()
    assert win._android_dialog is not None
    text = win._android_dialog.text.toPlainText()
    assert "Touchscreen: ✓" in text and "Raw events:" in text and "EV_ABS" not in text
    fake.disconnect("ABC123")
    qtbot.waitUntil(lambda: win.android_panel.reconnect_button.isVisible(), timeout=5000)
    assert win.app.session is not None and win.app.session.state.value == "paused"
    fake.reconnect("ABC123")
    win._android_reconnect()
    qtbot.waitUntil(lambda: win.app.session.state.value == "recording", timeout=5000)
    time.sleep(0.1)
    win._stop()
    qtbot.waitUntil(lambda: not win._android_timer.isActive(), timeout=5000)
    actions = [s.action for s in win.app.authoring.document.steps]
    assert actions == ["device.tap", "device.key", "device.key"]
    yaml_text = win.yaml.toPlainText()
    assert "device.tap" in yaml_text and "getevent" not in yaml_text
