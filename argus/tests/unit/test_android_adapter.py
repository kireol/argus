"""Android adapter input behaviour, verified through the adb argv it produces."""

from __future__ import annotations

import pytest

from argus.adapters.android import AndroidAdapter
from argus.exceptions import DeviceCapabilityError, DeviceConnectionError

_GETEVENT_P = """add device 1: /dev/input/event3
  name:     "gpio-keys"
  events:
    KEY (0001): 0072  0073  0074
add device 2: /dev/input/event2
  name:     "sdk_gphone_touchscreen"
  events:
    ABS (0003): 0035  : value 0, min 0, max 2159, fuzz 0, flat 0, resolution 0
                0036  : value 0, min 0, max 3839, fuzz 0, flat 0, resolution 0
                0039  : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
"""


class _Shell:
    """Stand-in for ``AndroidAdapter._shell`` recording every command."""

    def __init__(self, *, sdk: int = 33, getevent: str = _GETEVENT_P) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.sdk = sdk
        self.getevent = getevent
        self.fail_sendevent = False

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[:3] == ("getprop", "ro.build.version.sdk", ""):
            return f"{self.sdk}\n"
        if args[:2] == ("getprop", "ro.build.version.sdk"):
            return f"{self.sdk}\n"
        if args[:2] == ("getevent", "-p"):
            return self.getevent
        if args[:2] == ("wm", "size"):
            return "Physical size: 1080x1920\n"
        if args[:2] == ("wm", "density"):
            return "Physical density: 420\n"
        if args[:2] == ("sh", "-c") and self.fail_sendevent:
            raise DeviceConnectionError(
                "adb shell sh -c failed (1): sendevent: /dev/input/event2: Permission denied"
            )
        return ""

    def scripts(self) -> list[str]:
        return [c[2] for c in self.calls if c[:2] == ("sh", "-c")]


@pytest.fixture
def shell(monkeypatch) -> _Shell:
    sh = _Shell()
    monkeypatch.setattr(AndroidAdapter, "_shell", lambda self, *a: sh(*a))
    return sh


@pytest.fixture
def adapter() -> AndroidAdapter:
    return AndroidAdapter("phone", serial="emulator-5554")


def test_capabilities_include_gestures(adapter):
    caps = adapter.capabilities
    assert caps.supports_long_press and caps.supports_drag and caps.supports_multi_touch


def test_long_press_is_zero_length_swipe(adapter, shell):
    adapter.long_press(10, 20, duration_ms=1500)
    assert shell.calls == [("input", "swipe", "10", "20", "10", "20", "1500")]


def test_drag_uses_draganddrop_on_api_30_plus(adapter, shell):
    adapter.drag(1, 2, 3, 4, hold_ms=100, duration_ms=700)
    assert ("input", "draganddrop", "1", "2", "3", "4", "700") in shell.calls
    assert shell.scripts() == []


def test_drag_falls_back_to_sendevent_before_api_30(adapter, shell):
    shell.sdk = 28
    adapter.drag(1, 2, 3, 4, hold_ms=100, duration_ms=40)
    assert not any(c[:2] == ("input", "draganddrop") for c in shell.calls)
    (script,) = shell.scripts()
    lines = script.splitlines()
    # press, hold (sleep), move, release
    down = lines.index("sendevent /dev/input/event2 3 57 0")
    assert lines[down + 1] == "sendevent /dev/input/event2 1 330 1"
    assert "sleep 0.1" in lines[down:]
    assert "sendevent /dev/input/event2 3 57 -1" in lines
    assert lines[-1] == "sendevent /dev/input/event2 0 0 0"


def test_multi_touch_emits_protocol_b_events_scaled_to_touchscreen(adapter, shell):
    # Screen 1080x1920, touchscreen 0..2159 x 0..3839 -> scale x2 (ish).
    adapter.multi_touch([[(0, 0), (540, 0)], [(1079, 1919), (1079, 1919)]], duration_ms=40)
    (script,) = shell.scripts()
    lines = script.splitlines()
    assert lines[0] == "set -e"
    head = lines[1:11]
    assert head == [
        "sendevent /dev/input/event2 3 47 0",  # ABS_MT_SLOT 0
        "sendevent /dev/input/event2 3 57 0",  # ABS_MT_TRACKING_ID 0
        "sendevent /dev/input/event2 1 330 1",  # BTN_TOUCH down
        "sendevent /dev/input/event2 3 53 0",  # ABS_MT_POSITION_X
        "sendevent /dev/input/event2 3 54 0",  # ABS_MT_POSITION_Y
        "sendevent /dev/input/event2 3 47 1",
        "sendevent /dev/input/event2 3 57 1",
        "sendevent /dev/input/event2 3 53 2159",
        "sendevent /dev/input/event2 3 54 3839",
        "sendevent /dev/input/event2 0 0 0",  # SYN_REPORT
    ]
    # Two move frames over 40ms (20ms apart): midpoint then endpoint of finger 0.
    assert "sendevent /dev/input/event2 3 53 540" in lines  # 270px -> 540 units
    assert "sendevent /dev/input/event2 3 53 1081" in lines  # 540px -> 1081 units
    assert lines.count("sleep 0.02") == 2
    tail = lines[-6:]
    assert tail == [
        "sendevent /dev/input/event2 3 47 0",
        "sendevent /dev/input/event2 3 57 -1",
        "sendevent /dev/input/event2 3 47 1",
        "sendevent /dev/input/event2 3 57 -1",
        "sendevent /dev/input/event2 1 330 0",
        "sendevent /dev/input/event2 0 0 0",
    ]


def test_touchscreen_discovery_is_cached(adapter, shell):
    adapter.multi_touch([[(0, 0), (1, 1)]], duration_ms=20)
    adapter.multi_touch([[(0, 0), (1, 1)]], duration_ms=20)
    assert sum(1 for c in shell.calls if c[:2] == ("getevent", "-p")) == 1


def test_input_device_override_skips_discovery(monkeypatch, shell):
    adapter = AndroidAdapter("phone", input_device="/dev/input/event7")
    adapter.multi_touch([[(10, 10), (20, 20)]], duration_ms=20)
    assert not any(c[:2] == ("getevent", "-p") for c in shell.calls)
    (script,) = shell.scripts()
    # No axis info -> screen pixels are sent verbatim.
    assert "sendevent /dev/input/event7 3 53 10" in script
    assert "sendevent /dev/input/event7 3 54 10" in script


def test_no_touchscreen_found_is_capability_error(adapter, shell):
    shell.getevent = "add device 1: /dev/input/event3\n  name: \"gpio-keys\"\n"
    with pytest.raises(DeviceCapabilityError, match="touchscreen"):
        adapter.multi_touch([[(0, 0), (1, 1)]])


def test_unwritable_input_device_is_capability_error(adapter, shell):
    shell.fail_sendevent = True
    with pytest.raises(DeviceCapabilityError, match="Permission denied"):
        adapter.multi_touch([[(0, 0), (1, 1)]])


def test_from_config_reads_input_device():
    from argus.config.models import DeviceConfig

    cfg = DeviceConfig(type="android", input_device="/dev/input/event9")
    adapter = AndroidAdapter.from_config("phone", cfg)
    assert adapter._input_device == "/dev/input/event9"


# -- device selection on connect ------------------------------------------------------


def _patch_devices(monkeypatch, devices: list[str]) -> None:
    monkeypatch.setattr(
        AndroidAdapter, "list_devices", staticmethod(lambda adb_path="adb", timeout=10.0: devices)
    )
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)


def test_connect_uses_single_device(monkeypatch) -> None:
    _patch_devices(monkeypatch, ["emulator-5554"])
    adapter = AndroidAdapter("phone")
    adapter.connect()
    assert adapter.serial == "emulator-5554"


def test_connect_honours_android_serial_env(monkeypatch) -> None:
    _patch_devices(monkeypatch, ["emulator-5554", "emulator-5556"])
    monkeypatch.setenv("ANDROID_SERIAL", "emulator-5556")
    adapter = AndroidAdapter("phone")
    adapter.connect()
    assert adapter.serial == "emulator-5556"


def test_connect_prompts_when_multiple_devices_interactive(monkeypatch, capsys) -> None:
    _patch_devices(monkeypatch, ["emulator-5554", "emulator-5556"])
    monkeypatch.setattr("argus.adapters.android._interactive", lambda: True)
    answers = iter(["x", "9", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    adapter = AndroidAdapter("phone")
    adapter.connect()
    assert adapter.serial == "emulator-5556"
    err = capsys.readouterr().err
    assert "1. emulator-5554" in err
    assert "2. emulator-5556" in err
    assert "ANDROID_SERIAL=emulator-5554" in err


def test_connect_multiple_devices_non_interactive_raises(monkeypatch) -> None:
    from argus.exceptions import ConfigurationError

    _patch_devices(monkeypatch, ["emulator-5554", "emulator-5556"])
    monkeypatch.setattr("argus.adapters.android._interactive", lambda: False)
    adapter = AndroidAdapter("phone")
    with pytest.raises(ConfigurationError, match="ANDROID_SERIAL"):
        adapter.connect()


# -- adb argv / pidof ------------------------------------------------------------------


class _Completed:
    def __init__(self, returncode: int = 0, stdout: bytes = b"") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, b""


def _patch_adb(monkeypatch, responder):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return responder(command)

    monkeypatch.setattr("argus.adapters.android.shutil.which", lambda _: "/usr/bin/adb")
    monkeypatch.setattr("argus.adapters.android.subprocess.run", fake_run)
    return calls


def test_adb_targets_serial_even_before_connect(monkeypatch) -> None:
    _patch_devices(monkeypatch, ["emulator-5554", "emulator-5556"])
    monkeypatch.setenv("ANDROID_SERIAL", "emulator-5556")
    calls = _patch_adb(monkeypatch, lambda cmd: _Completed(0, b"1234\n"))
    adapter = AndroidAdapter("phone", app_package="com.example.app")
    assert adapter.is_application_running() is True
    assert calls == [["adb", "-s", "emulator-5556", "shell", "pidof", "com.example.app"]]


def test_is_application_running_false_when_pidof_exits_nonzero(monkeypatch) -> None:
    _patch_adb(monkeypatch, lambda cmd: _Completed(1, b""))
    adapter = AndroidAdapter("phone", serial="emulator-5554", app_package="com.example.app")
    adapter._connected = True
    assert adapter.is_application_running() is False


def test_is_application_running_true_with_pid(monkeypatch) -> None:
    calls = _patch_adb(monkeypatch, lambda cmd: _Completed(0, b"4321\n"))
    adapter = AndroidAdapter("phone", serial="emulator-5554", app_package="com.example.app")
    adapter._connected = True
    assert adapter.is_application_running() is True
    assert calls[0][:3] == ["adb", "-s", "emulator-5554"]
