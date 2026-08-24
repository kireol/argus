"""AppleTvAdapter unit tests with a fake pyatv interface (pyatv not required)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from argus.adapters.appletv import AppleTvAdapter
from argus.adapters.registry import DeviceRegistry
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError

_REMOTE_METHODS = {
    "up", "down", "left", "right", "select", "menu", "home", "play", "pause", "play_pause",
    "stop", "next", "previous", "volume_up", "volume_down",
}


class FakeRemote:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def __getattr__(self, name: str):
        if name not in _REMOTE_METHODS:
            raise AttributeError(name)

        async def press() -> None:
            self._calls.append(name)

        return press


class FakeApps:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def launch_app(self, bundle_id: str) -> None:
        self._calls.append(f"launch:{bundle_id}")


class FakeMetadata:
    def __init__(self) -> None:
        self.app = SimpleNamespace(identifier="com.example.tv", name="Example")
        self.now = SimpleNamespace(
            device_state=SimpleNamespace(name="Playing"),
            title="Big Buck Bunny",
            position=12,
            total_time=600,
        )

    async def playing(self):
        return self.now


class _RaisingAppMetadata:
    """metadata whose `app` property and `playing()` raise, like a pyatv failure."""

    @property
    def app(self):
        raise RuntimeError("boom")

    async def playing(self):
        raise RuntimeError("boom")


class _RaisingPower:
    """power whose `power_state` property raises, like a pyatv failure."""

    @property
    def power_state(self):
        raise RuntimeError("boom")


class FakeAtv:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.remote_control = FakeRemote(self.calls)
        self.apps = FakeApps(self.calls)
        self.metadata = FakeMetadata()
        self.power = SimpleNamespace(power_state=SimpleNamespace(name="On"))
        self.closed = False

    def close(self) -> set:
        self.closed = True
        return set()


@pytest.fixture
def atv() -> FakeAtv:
    return FakeAtv()


@pytest.fixture
def adapter(atv: FakeAtv) -> Iterator[AppleTvAdapter]:
    async def factory() -> FakeAtv:
        await asyncio.sleep(0)
        return atv

    device = AppleTvAdapter("atv", app_id="com.example.tv", host="10.0.0.9", atv_factory=factory)
    yield device
    device.disconnect()


class TestIdentity:
    def test_capabilities(self, adapter):
        caps = adapter.capabilities
        assert caps.supports_keyboard and caps.supports_app_lifecycle
        assert caps.supports_playback_state
        assert not caps.supports_screenshot and not caps.supports_logs
        assert adapter.platform == "appletv"

    def test_unsupported_operations(self, adapter):
        adapter.connect()
        with pytest.raises(DeviceCapabilityError):
            adapter.screenshot()
        with pytest.raises(DeviceCapabilityError):
            adapter.get_logs()
        with pytest.raises(DeviceCapabilityError):
            adapter.tap(1, 1)


class TestConnection:
    def test_connect_disconnect_lifecycle(self, adapter, atv):
        adapter.connect()
        assert adapter.health_check().healthy
        adapter.connect()  # idempotent
        adapter.disconnect()
        assert atv.closed
        assert not adapter.health_check().healthy
        adapter.disconnect()  # idempotent

    def test_operations_before_connect_raise(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter.press_key("ENTER")

    def test_factory_failure_wrapped(self):
        async def failing():
            raise RuntimeError("pairing required")

        device = AppleTvAdapter("atv", app_id="x", host="10.0.0.9", atv_factory=failing)
        with pytest.raises(DeviceConnectionError, match="pairing required"):
            device.connect()
        device.disconnect()

    def test_call_timeout(self, atv):
        async def factory():
            return atv

        async def slow_launch(bundle_id: str) -> None:
            await asyncio.sleep(5)

        atv.apps.launch_app = slow_launch  # type: ignore[method-assign]
        device = AppleTvAdapter(
            "atv", app_id="com.example.tv", host="10.0.0.9", timeout=0.2, atv_factory=factory
        )
        device.connect()
        try:
            with pytest.raises(DeviceConnectionError, match="timed out"):
                device.start_application()
        finally:
            device.disconnect()


class TestLifecycle:
    def test_start_stop_running(self, adapter, atv):
        adapter.connect()
        adapter.start_application()
        assert atv.calls == ["launch:com.example.tv"]
        assert adapter.is_application_running()
        atv.metadata.app = SimpleNamespace(identifier="com.apple.TVHome", name="Home")
        assert not adapter.is_application_running()
        adapter.stop_application()
        assert atv.calls[-1] == "home"
        adapter.reset_application()
        assert atv.calls[-2:] == ["home", "launch:com.example.tv"]


class TestPlayback:
    def test_playback_state_mapping(self, adapter, atv):
        adapter.connect()
        state = adapter.get_playback_state()
        assert state.state == "playing"
        assert state.title == "Big Buck Bunny"
        assert state.app_id == "com.example.tv"
        assert state.position == 12.0 and state.duration == 600.0

    def test_playback_state_idle(self, adapter, atv):
        atv.metadata.now = SimpleNamespace(
            device_state=SimpleNamespace(name="Idle"), title=None, position=None, total_time=None
        )
        atv.metadata.app = None
        adapter.connect()
        state = adapter.get_playback_state()
        assert state.state == "idle" and state.app_id is None


class TestErrorWrapping:
    """pyatv exceptions raised mid-operation must be wrapped as DeviceConnectionError."""

    def test_is_application_running_wraps_pyatv_exception(self, adapter, atv):
        adapter.connect()
        atv.metadata = _RaisingAppMetadata()
        with pytest.raises(DeviceConnectionError, match="boom"):
            adapter.is_application_running()

    def test_get_playback_state_wraps_pyatv_exception(self, adapter, atv):
        adapter.connect()
        atv.metadata = _RaisingAppMetadata()
        with pytest.raises(DeviceConnectionError, match="boom"):
            adapter.get_playback_state()

    def test_health_check_wraps_pyatv_exception(self, adapter, atv):
        adapter.connect()
        atv.power = _RaisingPower()
        result = adapter.health_check()
        assert result.healthy is False


class TestInput:
    @pytest.mark.parametrize(
        ("key", "method"),
        [
            ("KEYCODE_DPAD_LEFT", "left"),
            ("enter", "select"),
            ("BACK", "menu"),
            ("HOME", "home"),
            ("MEDIA_PLAY_PAUSE", "play_pause"),
            ("MEDIA_NEXT", "next"),
            ("VOLUME_UP", "volume_up"),
            ("play_pause", "play_pause"),
        ],
    )
    def test_press_key_mapping(self, adapter, atv, key, method):
        adapter.connect()
        adapter.press_key(key)
        assert atv.calls[-1] == method

    def test_unknown_key(self, adapter):
        adapter.connect()
        with pytest.raises(DeviceCapabilityError, match="F13"):
            adapter.press_key("F13")


class TestConfig:
    def test_from_config(self):
        config = DeviceConfig.model_validate(
            {
                "type": "appletv",
                "host": "10.0.0.9",
                "app_id": "com.example.tv",
                "credentials": {"companion": "c1", "airplay": "a1"},
                "timeout": 4,
            }
        )
        adapter = AppleTvAdapter.from_config("atv", config)
        assert adapter._host == "10.0.0.9"
        assert adapter._app_id == "com.example.tv"
        assert adapter._credentials == {"companion": "c1", "airplay": "a1"}
        assert adapter._timeout == 4.0

    def test_from_config_requires_host_or_identifier(self):
        with pytest.raises(ConfigurationError, match="host"):
            AppleTvAdapter.from_config(
                "atv", DeviceConfig.model_validate({"type": "appletv", "app_id": "x"})
            )

    def test_from_config_requires_app_id(self):
        with pytest.raises(ConfigurationError, match="app_id"):
            AppleTvAdapter.from_config(
                "atv", DeviceConfig.model_validate({"type": "appletv", "host": "10.0.0.9"})
            )

    def test_registered_as_appletv(self):
        registry = DeviceRegistry()
        assert "appletv" in registry.types()
        device = registry.create(
            "atv",
            DeviceConfig.model_validate({"type": "appletv", "host": "10.0.0.9", "app_id": "x"}),
        )
        assert isinstance(device, AppleTvAdapter)

    def test_missing_pyatv_gives_remediation(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("pyatv"):
                raise ImportError("no pyatv")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        device = AppleTvAdapter("atv", app_id="x", host="10.0.0.9")
        assert device.is_available() is False
        with pytest.raises(DeviceConnectionError, match=r'pip install "argus\[appletv\]"'):
            device.connect()
        device.disconnect()
