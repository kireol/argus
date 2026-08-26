from __future__ import annotations

from argus_test_creator.adapters.android.fake_adb import FakeAdbClient, FakeDevice
from argus_test_creator.cli.doctor import _android


def states(items):
    return {name: state for state, name, _detail in items}


def test_doctor_reports_full_android_chain():
    items = _android(FakeAdbClient([FakeDevice("A1")]))
    assert states(items) == {
        "ADB": "ok", "Devices": "ok", "Selected device": "ok", "Android version": "ok",
        "getevent": "ok", "Input devices": "ok", "Touchscreen": "ok", "Screenshot": "ok",
    }
    detail = dict((n, d) for _s, n, d in items)
    assert "/dev/input/event2" in detail["Touchscreen"]
    assert "1080x2400" in detail["Android version"]


def test_doctor_without_adb_or_devices_has_remediation():
    (item,) = _android(FakeAdbClient(adb_available=False))
    assert item[0] == "warn" and "Android" in item[2]
    items = _android(FakeAdbClient())
    assert states(items)["Devices"] == "warn" and "USB debugging" in items[-1][2]


def test_doctor_unauthorized_and_multiple_devices():
    items = _android(FakeAdbClient([FakeDevice("A1", state="unauthorized")]))
    assert states(items)["Devices"] == "fail" and "Allow USB debugging" in items[-1][2]
    items = _android(FakeAdbClient([FakeDevice("A1"), FakeDevice("B2")]))
    assert states(items)["Selected device"] == "warn"
    items = _android(FakeAdbClient([FakeDevice("A1"), FakeDevice("B2")]), serial="B2")
    assert states(items)["Selected device"] == "ok" and states(items)["Screenshot"] == "ok"


def test_doctor_getevent_and_screenshot_failures():
    device = FakeDevice("A1")
    device.getevent_ok = False
    device.screenshot_ok = False
    items = _android(FakeAdbClient([device]))
    assert states(items)["getevent"] == "fail" and "Touchscreen" not in states(items)
    assert states(items)["Screenshot"] == "fail"
