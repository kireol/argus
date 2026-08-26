"""BrowserAdapter unit tests against an in-memory fake page (no Playwright)."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

import pytest
from PIL import Image

from argus.adapters.browser import BrowserAdapter
from argus.adapters.registry import DeviceRegistry
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceConnectionError


class _FakeMouse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def click(self, x: float, y: float) -> None:
        self.calls.append(("click", (x, y)))

    def move(self, x: float, y: float, *, steps: int = 1) -> None:
        self.calls.append(("move", (x, y, steps)))

    def down(self) -> None:
        self.calls.append(("down", ()))

    def up(self) -> None:
        self.calls.append(("up", ()))


class _FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)


class _FakeConsoleMessage:
    def __init__(self, type_: str, text: str) -> None:
        self.type = type_
        self.text = text


class FakePage:
    def __init__(
        self,
        *,
        size: tuple[int, int] = (800, 600),
        fail_screenshot: bool = False,
        fail_goto: bool = False,
        fail_close: bool = False,
    ):
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self.viewport_size = {"width": size[0], "height": size[1]}
        self.url = "about:blank"
        self.visited: list[str] = []
        self.reloads = 0
        self.closed = False
        self.fail_screenshot = fail_screenshot
        self.fail_goto = fail_goto
        self.fail_close = fail_close
        self._handlers: dict[str, list[Callable[[Any], None]]] = {}

    def goto(self, url: str, *, timeout: float | None = None, wait_until: str = "load") -> None:
        if self.fail_goto:
            raise RuntimeError("goto boom")
        self.visited.append(url)
        self.url = url

    def reload(self, *, timeout: float | None = None) -> None:
        self.reloads += 1

    def screenshot(self, *, type: str = "png") -> bytes:  # noqa: A002 - mirrors Playwright
        if self.fail_screenshot:
            raise RuntimeError("boom")
        size = (self.viewport_size["width"], self.viewport_size["height"])
        img = Image.new("RGB", size, (1, 2, 3))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit_console(self, type_: str, text: str) -> None:
        for handler in self._handlers.get("console", []):
            handler(_FakeConsoleMessage(type_, text))

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        if self.fail_close:
            raise RuntimeError("close boom")
        self.closed = True


@pytest.fixture
def page() -> FakePage:
    return FakePage()


@pytest.fixture
def adapter(page: FakePage) -> BrowserAdapter:
    return BrowserAdapter("web", url="http://app.local/", page_factory=lambda: page)


class TestLifecycle:
    def test_capabilities(self, adapter):
        caps = adapter.capabilities
        assert caps.supports_screenshot and caps.supports_tap and caps.supports_swipe
        assert caps.supports_keyboard and caps.supports_app_lifecycle and caps.supports_logs
        assert adapter.platform == "web"

    def test_connect_opens_url_and_subscribes_console(self, adapter, page):
        adapter.connect()
        assert page.visited == ["http://app.local/"]
        assert adapter.is_application_running()
        page.emit_console("log", "hello")
        assert adapter.get_logs() == "log: hello"

    def test_operations_before_connect_raise(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter.screenshot()

    def test_health_check(self, adapter, page):
        assert not adapter.health_check().healthy
        adapter.connect()
        assert adapter.health_check().healthy
        page.closed = True
        assert not adapter.health_check().healthy

    def test_start_stop_restart(self, adapter, page):
        adapter.connect()
        adapter.stop_application()
        assert page.visited[-1] == "about:blank"
        assert not adapter.is_application_running()
        adapter.start_application()
        assert page.visited[-1] == "http://app.local/"
        adapter.reset_application()
        assert page.visited.count("http://app.local/") == 3

    def test_disconnect_closes_page(self, adapter, page):
        adapter.connect()
        adapter.disconnect()
        assert page.closed
        assert not adapter.is_application_running()

    def test_connect_failure_during_start_application_closes_page_and_raises(self):
        page = FakePage(fail_goto=True)
        adapter = BrowserAdapter("web", url="http://app.local/", page_factory=lambda: page)
        with pytest.raises(DeviceConnectionError, match="Unable to open"):
            adapter.connect()
        assert adapter._page is None
        assert page.closed is True

    def test_disconnect_survives_page_close_failure(self):
        page = FakePage(fail_close=True)
        adapter = BrowserAdapter("web", url="http://app.local/", page_factory=lambda: page)
        adapter.connect()
        adapter.disconnect()
        assert adapter._page is None


class TestObservation:
    def test_screenshot_returns_rgb_image(self, adapter):
        adapter.connect()
        img = adapter.screenshot()
        assert img.mode == "RGB"
        assert img.size == (800, 600)

    def test_screenshot_failure_wrapped(self):
        page = FakePage(fail_screenshot=True)
        adapter = BrowserAdapter("web", url="http://x/", page_factory=lambda: page)
        adapter.connect()
        from argus.exceptions import ScreenshotError

        with pytest.raises(ScreenshotError):
            adapter.screenshot()

    def test_screen_info_from_viewport(self, adapter):
        adapter.connect()
        info = adapter.get_screen_info()
        assert info.size == (800, 600)

    def test_logs_are_bounded_and_ordered(self, adapter, page):
        adapter.connect()
        for i in range(5):
            page.emit_console("error" if i % 2 else "log", f"msg{i}")
        assert adapter.get_logs(lines=2).splitlines() == ["error: msg3", "log: msg4"]

    def test_logs_cleared_on_start(self, adapter, page):
        adapter.connect()
        page.emit_console("log", "old")
        adapter.start_application()
        assert adapter.get_logs() == ""


class TestInput:
    def test_tap(self, adapter, page):
        adapter.connect()
        adapter.tap(10, 20)
        assert page.mouse.calls == [("click", (10, 20))]

    def test_swipe_is_drag(self, adapter, page):
        adapter.connect()
        adapter.swipe(0, 0, 100, 50, duration_ms=300)
        names = [c[0] for c in page.mouse.calls]
        assert names == ["move", "down", "move", "up"]
        assert page.mouse.calls[2][1][:2] == (100, 50)

    def test_press_key_maps_android_style_names(self, adapter, page):
        adapter.connect()
        adapter.press_key("enter")
        adapter.press_key("KEYCODE_DPAD_LEFT")
        adapter.press_key("F5")
        assert page.keyboard.pressed == ["Enter", "ArrowLeft", "F5"]


class TestConfig:
    def test_from_config(self):
        config = DeviceConfig.model_validate(
            {
                "type": "browser",
                "url": "http://a/",
                "browser": "firefox",
                "headless": False,
                "viewport": [1024, 768],
                "timeout": 5,
            }
        )
        adapter = BrowserAdapter.from_config("web", config)
        assert adapter._url == "http://a/"
        assert adapter._browser_name == "firefox"
        assert adapter._headless is False
        assert adapter._viewport == (1024, 768)
        assert adapter._timeout == 5.0

    def test_from_config_requires_url(self):
        with pytest.raises(ConfigurationError, match="url"):
            BrowserAdapter.from_config("web", DeviceConfig.model_validate({"type": "browser"}))

    def test_init_rejects_unknown_browser(self):
        with pytest.raises(ConfigurationError, match="chromium, firefox, webkit"):
            BrowserAdapter("web", url="http://a/", browser="ie")

    def test_from_config_rejects_unknown_browser(self):
        with pytest.raises(ConfigurationError, match="chromium, firefox, webkit"):
            BrowserAdapter.from_config(
                "web",
                DeviceConfig.model_validate(
                    {"type": "browser", "url": "http://a/", "browser": "ie"}
                ),
            )

    def test_registered_as_browser(self):
        registry = DeviceRegistry()
        assert "browser" in registry.types()
        device = registry.create(
            "web", DeviceConfig.model_validate({"type": "browser", "url": "http://a/"})
        )
        assert isinstance(device, BrowserAdapter)

    def test_missing_playwright_gives_remediation(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("playwright"):
                raise ImportError("no playwright")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        adapter = BrowserAdapter("web", url="http://a/")
        assert adapter.is_available() is False
        with pytest.raises(DeviceConnectionError, match=r'pip install "argus\[browser\]"'):
            adapter.connect()

    def test_open_page_failure_after_launch_tears_down_playwright(self, monkeypatch):
        """A failure after the browser process is launched must not leak it."""
        import builtins
        import types

        class _FakeBrowser:
            def __init__(self) -> None:
                self.closed = False

            def new_context(self, **kwargs: Any) -> Any:
                raise RuntimeError("new_context boom")

            def close(self) -> None:
                self.closed = True

        class _FakeDriver:
            def __init__(self, browser: _FakeBrowser) -> None:
                self.chromium = types.SimpleNamespace(launch=lambda **kwargs: browser)
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        fake_browser = _FakeBrowser()
        fake_driver = _FakeDriver(fake_browser)
        fake_sync_api = types.SimpleNamespace(
            sync_playwright=lambda: types.SimpleNamespace(start=lambda: fake_driver)
        )

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "playwright.sync_api":
                return fake_sync_api
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        adapter = BrowserAdapter("web", url="http://a/")
        with pytest.raises(DeviceConnectionError, match="Unable to open"):
            adapter.connect()

        assert fake_browser.closed is True
        assert fake_driver.stopped is True
        assert adapter._browser is None
        assert adapter._playwright is None


class _FakeCDPSession:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sent.append((method, params or {}))
        return {}


class _FakeContext:
    def __init__(self) -> None:
        self.cdp = _FakeCDPSession()

    def new_cdp_session(self, page: Any) -> _FakeCDPSession:
        return self.cdp


class TestGestures:
    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        self.sleeps: list[float] = []
        monkeypatch.setattr("argus.adapters.browser.time.sleep", self.sleeps.append)

    def test_capabilities(self, adapter):
        caps = adapter.capabilities
        assert caps.supports_long_press and caps.supports_drag and caps.supports_multi_touch

    def test_long_press_holds_mouse_down(self, adapter, page):
        adapter.connect()
        adapter.long_press(10, 20, duration_ms=1500)
        assert page.mouse.calls == [("move", (10, 20, 1)), ("down", ()), ("up", ())]
        assert self.sleeps == [1.5]

    def test_drag_holds_before_moving(self, adapter, page):
        adapter.connect()
        adapter.drag(0, 0, 100, 50, hold_ms=250, duration_ms=320)
        names = [c[0] for c in page.mouse.calls]
        assert names == ["move", "down", "move", "up"]
        assert page.mouse.calls[2][1] == (100, 50, 20)
        assert self.sleeps == [0.25]

    def test_multi_touch_dispatches_cdp_touch_events(self, adapter, page):
        page.context = _FakeContext()
        adapter.connect()
        adapter.multi_touch([[(0, 0), (10, 10)], [(100, 100), (80, 80)]], duration_ms=32)
        sent = page.context.cdp.sent
        assert [m for m, _ in sent] == [
            "Input.dispatchTouchEvent",
            "Input.dispatchTouchEvent",
            "Input.dispatchTouchEvent",
            "Input.dispatchTouchEvent",
        ]
        assert sent[0][1] == {
            "type": "touchStart",
            "touchPoints": [{"x": 0, "y": 0, "id": 0}, {"x": 100, "y": 100, "id": 1}],
        }
        assert sent[1][1] == {
            "type": "touchMove",
            "touchPoints": [{"x": 5, "y": 5, "id": 0}, {"x": 90, "y": 90, "id": 1}],
        }
        assert sent[2][1] == {
            "type": "touchMove",
            "touchPoints": [{"x": 10, "y": 10, "id": 0}, {"x": 80, "y": 80, "id": 1}],
        }
        assert sent[3][1] == {"type": "touchEnd", "touchPoints": []}

    def test_pinch_uses_multi_touch(self, adapter, page):
        page.context = _FakeContext()
        adapter.connect()
        adapter.pinch(200, 200, start_distance=100, end_distance=50, duration_ms=16)
        sent = page.context.cdp.sent
        assert sent[0][1]["touchPoints"] == [
            {"x": 150, "y": 200, "id": 0},
            {"x": 250, "y": 200, "id": 1},
        ]
        assert sent[-2][1]["touchPoints"] == [
            {"x": 175, "y": 200, "id": 0},
            {"x": 225, "y": 200, "id": 1},
        ]

    def test_multi_touch_requires_chromium(self, page):
        from argus.exceptions import DeviceCapabilityError

        page.context = _FakeContext()
        adapter = BrowserAdapter(
            "web", url="http://app.local/", browser="firefox", page_factory=lambda: page
        )
        adapter.connect()
        with pytest.raises(DeviceCapabilityError, match="chromium"):
            adapter.multi_touch([[(0, 0), (1, 1)]])
        assert page.context.cdp.sent == []
