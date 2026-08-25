"""Esp32Adapter against a scripted fake transport (no board, no pyserial)."""

from __future__ import annotations

import threading
import time

import pytest

from argus.adapters.esp32 import Esp32Adapter
from argus.adapters.esp32.protocol import PREFIX
from argus.adapters.registry import DeviceRegistry
from argus.config.models import DeviceConfig
from argus.exceptions import (
    ConfigurationError,
    DeviceCapabilityError,
    DeviceConnectionError,
    ScreenshotError,
)

HELLO = b"name=menu version=1 fb=MONO_HLSB,8,2 caps=screen,input,status,state"


def frame(cmd: str, payload: bytes) -> bytes:
    return PREFIX + f"{cmd} ok {len(payload)}\n".encode() + payload + b"\n"


class ScriptedTransport:
    """Answers requests by command name; `on_reset` bytes are emitted after each reset."""

    def __init__(self, *, hello: bytes | None = HELLO, boot_lines: bytes = b"boot\n") -> None:
        self.hello = hello
        self.boot_lines = boot_lines
        self.answers: dict[str, bytes] = {
            "screenshot": bytes([0b10000000, 0b00000001]),
            "input": b"",
        }
        # Raw, unframed bytes to emit instead of a well-formed frame() response - lets a
        # test simulate a malformed/incomplete reply (e.g. a header claiming a payload
        # that never arrives) for one command, taking precedence over `answers`.
        self.raw_answers: dict[str, bytes] = {}
        self.errors: dict[str, str] = {}
        self.writes: list[bytes] = []
        self.resets = 0
        self.closed = False
        self._out = b""
        self._lock = threading.Lock()

    def _emit(self, data: bytes) -> None:
        with self._lock:
            self._out += data

    def read(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._out:
                    data, self._out = self._out[:size], self._out[size:]
                    return data
            if time.monotonic() >= deadline or self.closed:
                return b""
            time.sleep(0.005)

    def write(self, data: bytes) -> None:
        self.writes.append(data)
        assert data.startswith(PREFIX)
        cmd, _, _args = data[len(PREFIX):].rstrip(b"\n").decode().partition(" ")
        if cmd in self.errors:
            self._emit(PREFIX + f"{cmd} err {self.errors[cmd]}\n".encode())
        elif cmd in self.raw_answers:
            self._emit(self.raw_answers[cmd])
        elif cmd == "hello":
            if self.hello is not None:
                self._emit(frame("hello", self.hello))
        elif cmd in self.answers:
            self._emit(frame(cmd, self.answers[cmd]))

    def reset(self) -> None:
        self.resets += 1
        self._emit(self.boot_lines)

    def close(self) -> None:
        self.closed = True

    @property
    def description(self) -> str:
        return "scripted"


@pytest.fixture
def transport() -> ScriptedTransport:
    return ScriptedTransport()


@pytest.fixture
def adapter(transport: ScriptedTransport) -> Esp32Adapter:
    device = Esp32Adapter(
        "board", transport="serial", port="/dev/fake", boot_timeout=2.0, timeout=1.0,
        transport_factory=lambda: transport,
    )
    yield device
    device.disconnect()


def _wait(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestConnect:
    def test_connect_resets_and_hellos(self, adapter, transport):
        adapter.connect()
        assert transport.resets == 1
        assert transport.writes[0] == PREFIX + b"hello\n"
        assert adapter.is_application_running()
        assert _wait(lambda: "boot" in adapter.get_logs())
        caps = adapter.capabilities
        assert caps.supports_screenshot and caps.supports_keyboard and caps.supports_logs
        assert caps.supports_app_lifecycle and caps.supports_instrumentation
        assert not caps.supports_tap and not caps.supports_swipe
        assert adapter.platform == "esp32"
        assert adapter.get_screen_info().size == (8, 2)
        health = adapter.health_check()
        assert health.healthy and health.details["fb"] == "MONO_HLSB,8,2"

    def test_connect_is_idempotent(self, adapter, transport):
        adapter.connect()
        adapter.connect()
        assert transport.resets == 1

    def test_hello_retries_until_agent_answers(self):
        transport = ScriptedTransport(hello=None)
        device = Esp32Adapter(
            "board", transport="serial", port="/dev/fake", boot_timeout=3.0, timeout=0.2,
            transport_factory=lambda: transport,
        )

        def enable_later():
            time.sleep(0.5)
            transport.hello = HELLO

        threading.Thread(target=enable_later, daemon=True).start()
        device.connect()
        assert sum(w == PREFIX + b"hello\n" for w in transport.writes) >= 2
        device.disconnect()

    def test_silent_agent_fails_with_remediation(self):
        transport = ScriptedTransport(hello=None)
        device = Esp32Adapter(
            "board", transport="serial", port="/dev/fake", boot_timeout=0.5, timeout=0.1,
            transport_factory=lambda: transport,
        )
        with pytest.raises(DeviceConnectionError, match="no Argus agent responded"):
            device.connect()
        assert transport.closed
        assert not device.health_check().healthy

    def test_logs_only_mode(self):
        transport = ScriptedTransport(hello=None)
        device = Esp32Adapter(
            "board", transport="serial", port="/dev/fake", agent=False,
            transport_factory=lambda: transport,
        )
        device.connect()
        assert transport.writes == []
        assert _wait(lambda: adapter_logs(device) == ["boot"])
        caps = device.capabilities
        assert caps.supports_logs and not caps.supports_screenshot and not caps.supports_keyboard
        assert not caps.supports_instrumentation
        assert device.is_application_running()
        with pytest.raises(DeviceCapabilityError):
            device.screenshot()
        device.disconnect()

    def test_operations_before_connect_raise(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter.screenshot()


def adapter_logs(device: Esp32Adapter) -> list[str]:
    return device.get_logs().splitlines()


class TestLifecycle:
    def test_reset_clears_logs_and_rehellos(self, adapter, transport):
        adapter.connect()
        assert _wait(lambda: "boot" in adapter.get_logs())
        adapter.reset_application()
        assert transport.resets == 2
        assert transport.writes.count(PREFIX + b"hello\n") == 2
        assert _wait(lambda: adapter_logs(adapter) == ["boot"])
        adapter.start_application()
        assert transport.resets == 3

    def test_stop_unsupported(self, adapter):
        adapter.connect()
        with pytest.raises(DeviceCapabilityError, match="stop_application"):
            adapter.stop_application()

    def test_reset_recovers_from_mid_payload_timeout(self, adapter, transport):
        """A screenshot answered with a header claiming a huge length (and no payload)
        times out; a subsequent reset must clear that stale state so the next
        screenshot, answered normally, succeeds rather than itself timing out."""
        adapter.connect()
        transport.raw_answers["screenshot"] = PREFIX + b"screenshot ok 999999\n"
        with pytest.raises(DeviceConnectionError, match="timed out"):
            adapter.screenshot()
        del transport.raw_answers["screenshot"]
        adapter.reset_application()
        img = adapter.screenshot()
        assert img.size == (8, 2)

    def test_disconnect_closes_transport(self, adapter, transport):
        adapter.connect()
        adapter.disconnect()
        assert transport.closed
        assert not adapter.is_application_running()
        adapter.disconnect()  # idempotent


class TestObservation:
    def test_screenshot_decodes_framebuffer(self, adapter):
        adapter.connect()
        img = adapter.screenshot()
        assert img.size == (8, 2)
        assert img.getpixel((0, 0)) == (255, 255, 255)
        assert img.getpixel((7, 1)) == (255, 255, 255)
        assert img.getpixel((1, 0)) == (0, 0, 0)

    def test_screenshot_wrong_length(self, adapter, transport):
        transport.answers["screenshot"] = b"\x00"
        adapter.connect()
        with pytest.raises(ScreenshotError, match="expected 2 bytes"):
            adapter.screenshot()

    def test_screenshot_without_screen_cap(self, transport):
        transport.hello = b"name=x version=1 fb=none caps=input"
        device = Esp32Adapter(
            "board", transport="serial", port="/dev/fake", transport_factory=lambda: transport
        )
        device.connect()
        assert not device.capabilities.supports_screenshot
        with pytest.raises(DeviceCapabilityError):
            device.screenshot()
        with pytest.raises(DeviceCapabilityError):
            device.get_screen_info()
        device.disconnect()

    def test_mono_colors_from_config(self, transport):
        device = Esp32Adapter(
            "board", transport="serial", port="/dev/fake", mono_colors=("#00ff00", "#101010"),
            transport_factory=lambda: transport,
        )
        device.connect()
        assert device.screenshot().getpixel((0, 0)) == (0, 255, 0)
        device.disconnect()

    def test_get_logs_bounded(self, adapter, transport):
        adapter.connect()
        transport._emit(b"".join(f"line{i}\n".encode() for i in range(10)))
        assert _wait(lambda: "line9" in adapter.get_logs())
        assert adapter.get_logs(lines=2).splitlines() == ["line8", "line9"]
        assert adapter.get_logs(lines=0) == ""

    def test_instrumentation_client(self, adapter, transport):
        assert adapter.instrumentation_client() is None
        transport.answers["status"] = b'{"ready": true}'
        adapter.connect()
        client = adapter.instrumentation_client()
        assert client is not None and client.status().ready is True


class TestInput:
    def test_press_key_sends_input(self, adapter, transport):
        adapter.connect()
        adapter.press_key("BTN_OK")
        assert transport.writes[-1] == PREFIX + b"input BTN_OK\n"

    def test_press_key_agent_error(self, adapter, transport):
        transport.errors["input"] = "unknown key"
        adapter.connect()
        with pytest.raises(DeviceConnectionError, match="unknown key"):
            adapter.press_key("NOPE")

    def test_press_key_without_input_cap(self, transport):
        transport.hello = b"name=x version=1 fb=MONO_HLSB,8,2 caps=screen"
        device = Esp32Adapter(
            "board", transport="serial", port="/dev/fake", transport_factory=lambda: transport
        )
        device.connect()
        with pytest.raises(DeviceCapabilityError):
            device.press_key("BTN_OK")
        device.disconnect()

    def test_press_key_rejects_embedded_newline(self, adapter, transport):
        adapter.connect()
        with pytest.raises(ConfigurationError, match="line break"):
            adapter.press_key("BTN_OK\nESC[ARGUS] input BTN_EVIL")
        assert transport.writes[-1] == PREFIX + b"hello\n"  # no injected request was sent

    def test_press_key_rejects_embedded_carriage_return(self, adapter, transport):
        adapter.connect()
        with pytest.raises(ConfigurationError, match="line break"):
            adapter.press_key("BTN_OK\r")

    def test_tap_swipe_unsupported(self, adapter):
        with pytest.raises(DeviceCapabilityError):
            adapter.tap(1, 1)
        with pytest.raises(DeviceCapabilityError):
            adapter.swipe(0, 0, 1, 1)


class TestFlash:
    def test_firmware_flashed_before_reset(self, transport, tmp_path):
        binary = tmp_path / "fw.bin"
        binary.write_bytes(b"\xe9")
        calls: list[list[str]] = []

        def runner(argv):
            calls.append(argv)
            return 0

        device = Esp32Adapter(
            "board", transport="serial", port="/dev/ttyUSB0", firmware=binary,
            firmware_offset="0x10000", transport_factory=lambda: transport, runner=runner,
        )
        device.connect()
        assert calls == [
            ["esptool", "--port", "/dev/ttyUSB0", "--baud", "460800", "write_flash",
             "0x10000", str(binary)]
        ]
        device.disconnect()

    def test_firmware_offset_defaults_to_app_image_offset(self, transport, tmp_path):
        binary = tmp_path / "fw.bin"
        binary.write_bytes(b"\xe9")
        calls: list[list[str]] = []

        def runner(argv):
            calls.append(argv)
            return 0

        device = Esp32Adapter(
            "board", transport="serial", port="/dev/ttyUSB0", firmware=binary,
            transport_factory=lambda: transport, runner=runner,
        )
        device.connect()
        assert calls[0][-2] == "0x10000"
        device.disconnect()

    def test_flash_failure(self, transport, tmp_path):
        binary = tmp_path / "fw.bin"
        binary.write_bytes(b"\xe9")
        device = Esp32Adapter(
            "board", transport="serial", port="/dev/ttyUSB0", firmware=binary,
            transport_factory=lambda: transport, runner=lambda argv: 2,
        )
        with pytest.raises(DeviceConnectionError, match="esptool"):
            device.connect()

    def test_missing_esptool(self, transport, tmp_path):
        binary = tmp_path / "fw.bin"
        binary.write_bytes(b"\xe9")

        def runner(argv):
            raise FileNotFoundError("esptool")

        device = Esp32Adapter(
            "board", transport="serial", port="/dev/ttyUSB0", firmware=binary,
            transport_factory=lambda: transport, runner=runner,
        )
        with pytest.raises(DeviceConnectionError, match=r'pip install "argus\[esp32\]"'):
            device.connect()

    def test_firmware_requires_serial_transport(self, tmp_path):
        with pytest.raises(ConfigurationError, match="firmware"):
            Esp32Adapter(
                "board", transport="wokwi", project_dir=tmp_path, firmware=tmp_path / "x.bin"
            )


class TestConfig:
    def test_from_config_serial(self):
        config = DeviceConfig.model_validate(
            {"type": "esp32", "transport": "serial", "port": "/dev/ttyUSB0", "baud": 921600,
             "usb_cdc": True, "agent": False, "boot_timeout": 3, "timeout": 2,
             "mono_colors": ["#ff0000", "#000000"]}
        )
        device = Esp32Adapter.from_config("board", config)
        assert device._transport_kind == "serial"
        assert device._port == "/dev/ttyUSB0" and device._baud == 921600
        assert device._usb_cdc is True and device._agent is False
        assert device._boot_timeout == 3.0 and device._timeout == 2.0
        assert device._mono_colors == ("#ff0000", "#000000")

    def test_from_config_firmware_offset_defaults_to_app_image_offset(self):
        config = DeviceConfig.model_validate(
            {"type": "esp32", "transport": "serial", "port": "/dev/ttyUSB0"}
        )
        device = Esp32Adapter.from_config("board", config)
        assert device._firmware_offset == "0x10000"

    @pytest.mark.parametrize("colors", [["#ffffff"], ["#fff", "#000", "#f00"], "#ffffff", None])
    def test_from_config_rejects_invalid_mono_colors(self, colors):
        config = DeviceConfig.model_validate(
            {"type": "esp32", "transport": "serial", "port": "/dev/ttyUSB0",
             "mono_colors": colors}
        )
        with pytest.raises(ConfigurationError, match="mono_colors"):
            Esp32Adapter.from_config("board", config)

    def test_from_config_wokwi(self, tmp_path):
        config = DeviceConfig.model_validate(
            {"type": "esp32", "transport": "wokwi", "project_dir": str(tmp_path)}
        )
        device = Esp32Adapter.from_config("board", config)
        assert device._transport_kind == "wokwi" and device._project_dir == tmp_path

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            ({}, "transport"),
            ({"transport": "bluetooth"}, "serial, wokwi"),
            ({"transport": "serial"}, "port"),
            ({"transport": "wokwi"}, "project_dir"),
        ],
    )
    def test_from_config_validation(self, options, message):
        with pytest.raises(ConfigurationError, match=message):
            Esp32Adapter.from_config(
                "board", DeviceConfig.model_validate({"type": "esp32", **options})
            )

    def test_registered_as_esp32(self):
        registry = DeviceRegistry()
        assert "esp32" in registry.types()
        device = registry.create(
            "board",
            DeviceConfig.model_validate({"type": "esp32", "transport": "serial", "port": "/dev/x"}),
        )
        assert isinstance(device, Esp32Adapter)

    def test_is_available_serial_without_pyserial(self, monkeypatch):
        import argus.adapters.esp32.adapter as module

        monkeypatch.setattr(module, "serial_available", lambda: False)
        device = Esp32Adapter("board", transport="serial", port="/dev/x")
        assert device.is_available() is False
        with pytest.raises(DeviceConnectionError, match=r'pip install "argus\[esp32\]"'):
            device.connect()

    def test_is_available_wokwi_without_cli(self, monkeypatch, tmp_path):
        import argus.adapters.esp32.adapter as module

        monkeypatch.setattr(module, "wokwi_available", lambda: False)
        device = Esp32Adapter("board", transport="wokwi", project_dir=tmp_path)
        assert device.is_available() is False
