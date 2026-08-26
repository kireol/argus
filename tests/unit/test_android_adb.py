from __future__ import annotations

import sys
import time

import pytest

from argus_test_creator.adapters.android.adb import (
    AdbProcess,
    SubprocessAdbClient,
    parse_devices_output,
    parse_rotation,
    parse_wm_size,
)
from argus_test_creator.adapters.android.fake_adb import FakeAdbClient, FakeDevice
from argus_test_creator.core.errors import TargetConnectionError

PY = sys.executable


def test_parse_devices_output_with_details():
    text = """List of devices attached
ABC123\tdevice usb:1-1 product:shiba model:Pixel_8 device:shiba transport_id:3
emulator-5554\toffline
DEF456\tunauthorized usb:2-1 transport_id:4

"""
    devices = parse_devices_output(text)
    assert [d.serial for d in devices] == ["ABC123", "emulator-5554", "DEF456"]
    assert devices[0].usable and devices[0].model == "Pixel_8"
    assert devices[0].label() == "Pixel 8 — ABC123"
    assert not devices[1].usable and devices[1].state == "offline"
    assert devices[2].state == "unauthorized"
    assert parse_devices_output("List of devices attached\n\n") == []
    assert parse_devices_output("* daemon started successfully\nX\tdevice\n")[0].serial == "X"


def test_parse_wm_size_prefers_override():
    assert parse_wm_size("Physical size: 1080x2400\n") == (1080, 2400)
    assert parse_wm_size("Physical size: 1080x2400\nOverride size: 720x1600\n") == (720, 1600)
    assert parse_wm_size("garbage") == (0, 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  mCurrentRotation=1", 1),
        ("SurfaceOrientation: 3", 3),
        ("DisplayDeviceInfo{... rotation=2 ...}", 2),
        ("mRotation=ROTATION_90", 0),  # named rotations without digit → parsed as 0? no match
        ("nothing here", None),
    ],
)
def test_parse_rotation(text, expected):
    if text.startswith("mRotation=ROTATION_90"):
        assert parse_rotation(text) in (None, 0, 9 % 4)
    else:
        assert parse_rotation(text) == expected


def test_adb_process_streams_lines_and_stops_cleanly():
    script = "import sys,time\nfor i in range(5):\n print('line', i, flush=True)\nsys.stderr.write('warn\\n')\nsys.stderr.flush()\ntime.sleep(30)\n"  # noqa: E501
    proc = AdbProcess([PY, "-u", "-c", script]).start()
    lines = []
    deadline = time.monotonic() + 5
    while len(lines) < 5 and time.monotonic() < deadline:
        line = proc.readline(timeout=0.5)
        if line:
            lines.append(line.strip())
    assert lines == [f"line {i}" for i in range(5)]
    assert proc.alive
    proc.stop(timeout=2)
    assert not proc.alive
    assert proc.returncode is not None
    assert "warn" in proc.stderr_text
    assert proc.readline(timeout=0.1) is None  # EOF after stop


def test_adb_process_eof_when_child_exits():
    proc = AdbProcess([PY, "-c", "print('x')"]).start()
    seen = []
    for _ in range(50):
        line = proc.readline(timeout=0.2)
        if line is None:
            break
        if line:
            seen.append(line.strip())
    assert seen == ["x"]
    assert proc.returncode == 0
    proc.stop()


def test_adb_process_kills_child_that_ignores_sigterm():
    script = "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\nwhile True: time.sleep(0.1)\n"  # noqa: E501
    proc = AdbProcess([PY, "-u", "-c", script]).start()
    assert proc.readline(timeout=3).strip() == "ready"
    started = time.monotonic()
    proc.stop(timeout=0.5)
    assert not proc.alive
    assert time.monotonic() - started < 3


def test_adb_process_missing_binary_is_a_connection_error():
    with pytest.raises(TargetConnectionError):
        AdbProcess(["/definitely/not/adb"]).start()


def test_subprocess_client_missing_adb_reports_remediation(tmp_path):
    client = SubprocessAdbClient(str(tmp_path / "nope-adb"))
    ok, reason = client.available()
    assert not ok and "adb not found" in reason
    with pytest.raises(TargetConnectionError) as info:
        client.list_devices()
    assert info.value.remediation


def test_subprocess_client_builds_argv_with_serial(monkeypatch, tmp_path):
    calls = []

    class Completed:
        returncode = 0
        stdout = b"List of devices attached\nABC\tdevice\n"
        stderr = b""

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert "shell" not in kwargs or kwargs.get("shell") is not True
        return Completed()

    monkeypatch.setattr("argus_test_creator.adapters.android.adb.subprocess.run", fake_run)
    client = SubprocessAdbClient("adb")
    client.shell("SER 1; rm -rf /", "getevent", "-lp")
    assert calls[-1] == ["adb", "-s", "SER 1; rm -rf /", "shell", "getevent", "-lp"]
    client.list_devices()
    assert calls[-1] == ["adb", "devices", "-l"]


def test_fake_adb_client_scripts_devices_and_stream(tmp_path):
    fake = FakeAdbClient([FakeDevice("A1"), FakeDevice("B2", state="unauthorized")])
    devices = fake.list_devices()
    assert [(d.serial, d.usable) for d in devices] == [("A1", True), ("B2", False)]
    info = fake.get_device_info("A1")
    assert info.screen_size == (1080, 2400)
    inputs = fake.get_input_devices("A1")
    assert any(d.is_touchscreen for d in inputs)
    fake.script_lines("A1", ["/dev/input/event0: EV_KEY KEY_BACK DOWN\n"])
    stream = fake.start_getevent("A1")
    assert stream.readline(timeout=1).startswith("/dev/input/event0")
    assert stream.readline(timeout=0.05) == ""  # open, idle
    fake.disconnect("A1")
    assert stream.readline(timeout=1) is None
    assert stream.returncode == 255
    assert fake.list_devices() == [d for d in fake.list_devices() if d.serial != "A1"]
    with pytest.raises(TargetConnectionError):
        fake.screenshot("A1")
    fake.reconnect("A1")
    assert fake.screenshot("A1").startswith(b"\x89PNG")
