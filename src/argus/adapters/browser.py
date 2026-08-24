"""Web browser adapter (Playwright-based).

Drives a web application the same way the Android adapter drives a phone:
screenshots go through the visual/OCR verifiers, taps become mouse clicks,
swipes become drags, and browser console output is exposed as device logs.
Playwright is an optional dependency (``pip install "argus[browser]"``).
"""

from __future__ import annotations

import contextlib
import io
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

from PIL import Image as PILImage
from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceConnectionError, ScreenshotError
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo

_SUPPORTED_BROWSERS = ("chromium", "firefox", "webkit")
_DEFAULT_TIMEOUT = 30.0
_MAX_LOG_LINES = 5000
_BLANK = "about:blank"

# Android-style key names -> Playwright key names. Anything not listed is passed
# through unchanged (Playwright accepts "Enter", "F5", "a", "Shift+Tab", ...).
_KEY_MAP = {
    "ENTER": "Enter",
    "DPAD_CENTER": "Enter",
    "BACK": "Escape",
    "ESCAPE": "Escape",
    "TAB": "Tab",
    "SPACE": "Space",
    "DEL": "Backspace",
    "BACKSPACE": "Backspace",
    "DPAD_UP": "ArrowUp",
    "DPAD_DOWN": "ArrowDown",
    "DPAD_LEFT": "ArrowLeft",
    "DPAD_RIGHT": "ArrowRight",
    "UP": "ArrowUp",
    "DOWN": "ArrowDown",
    "LEFT": "ArrowLeft",
    "RIGHT": "ArrowRight",
    "HOME": "Home",
    "END": "End",
    "PAGE_UP": "PageUp",
    "PAGE_DOWN": "PageDown",
}


class PageLike(Protocol):
    """The subset of Playwright's ``Page`` the adapter relies on (fakeable)."""

    mouse: Any
    keyboard: Any
    viewport_size: dict[str, int] | None
    url: str

    def goto(self, url: str, *, timeout: float | None = None, wait_until: str = "load") -> Any: ...
    def reload(self, *, timeout: float | None = None) -> Any: ...
    def screenshot(self, *, type: str = "png") -> bytes: ...  # noqa: A002
    def on(self, event: str, handler: Callable[[Any], None]) -> None: ...
    def is_closed(self) -> bool: ...
    def close(self) -> None: ...


class BrowserAdapter(Device):
    """Controls a web application through a Playwright browser page."""

    def __init__(
        self,
        name: str,
        *,
        url: str,
        browser: str = "chromium",
        headless: bool = True,
        viewport: tuple[int, int] = (1280, 720),
        timeout: float = _DEFAULT_TIMEOUT,
        page_factory: Callable[[], PageLike] | None = None,
    ) -> None:
        super().__init__(name)
        browser = browser.lower()
        if browser not in _SUPPORTED_BROWSERS:
            raise ConfigurationError(
                f"Browser device {name!r}: unknown browser {browser!r}.",
                remediation=f"Use one of: {', '.join(_SUPPORTED_BROWSERS)}.",
            )
        self._url = url
        self._browser_name = browser
        self._headless = headless
        self._viewport = viewport
        self._timeout = timeout
        self._page_factory = page_factory
        self._page: PageLike | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._app_running = False
        self._log = get_logger("argus.browser", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> BrowserAdapter:
        options: dict[str, Any] = config.options
        url = options.get("url")
        if not url:
            raise ConfigurationError(
                f"Browser device {name!r} requires a 'url' option.",
                remediation="Set devices.<name>.url to the application's address.",
            )
        browser = str(options.get("browser", "chromium"))
        viewport = options.get("viewport", [1280, 720])
        return cls(
            name,
            url=str(url),
            browser=browser,
            headless=bool(options.get("headless", True)),
            viewport=(int(viewport[0]), int(viewport[1])),
            timeout=float(options.get("timeout", _DEFAULT_TIMEOUT)),
        )

    # -- identity -----------------------------------------------------------------

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=True,
            supports_tap=True,
            supports_swipe=True,
            supports_keyboard=True,
            supports_app_lifecycle=True,
            supports_logs=True,
            supports_instrumentation=True,
        )

    @property
    def platform(self) -> str:
        return "web"

    # -- playwright plumbing --------------------------------------------------------

    def _open_page(self) -> PageLike:
        if self._page_factory is not None:
            return self._page_factory()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise DeviceConnectionError(
                "playwright is not installed (required for browser devices).",
                remediation='Install browser support: pip install "argus[browser]" '
                "&& playwright install chromium",
            ) from exc
        self._playwright = sync_playwright().start()
        launcher = getattr(self._playwright, self._browser_name)
        try:
            self._browser = launcher.launch(headless=self._headless)
        except Exception as exc:  # noqa: BLE001 - Playwright raises its own Error type
            self._teardown_playwright()
            raise DeviceConnectionError(
                f"Unable to launch {self._browser_name}: {exc}",
                remediation=f"Run: playwright install {self._browser_name}",
            ) from exc
        try:
            context = self._browser.new_context(
                viewport={"width": self._viewport[0], "height": self._viewport[1]}
            )
            page = context.new_page()
            page.set_default_timeout(self._timeout * 1000)
        except Exception as exc:  # noqa: BLE001 - Playwright raises its own Error type
            self._teardown_playwright()
            raise DeviceConnectionError(
                f"Unable to open a {self._browser_name} page: {exc}",
                remediation=f"Run: playwright install {self._browser_name}",
            ) from exc
        return page

    def _teardown_playwright(self) -> None:
        """Close any playwright browser/driver process created by ``_open_page``."""
        browser, self._browser = self._browser, None
        playwright, self._playwright = self._playwright, None
        try:
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.close()
        finally:
            if playwright is not None:
                playwright.stop()

    def _require_page(self) -> PageLike:
        if self._page is None:
            raise DeviceConnectionError(
                f"Browser device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        return self._page

    def _on_console(self, message: Any) -> None:
        self._logs.append(f"{message.type}: {message.text}")

    # -- connection -----------------------------------------------------------------

    def connect(self) -> None:
        if self._page is not None:
            return
        page = self._open_page()
        page.on("console", self._on_console)
        self._page = page
        try:
            self.start_application()
        except Exception as exc:  # noqa: BLE001 - Playwright raises its own Error type
            with contextlib.suppress(Exception):
                page.close()
            self._teardown_playwright()
            self._page = None
            self._app_running = False
            raise DeviceConnectionError(
                f"Unable to open {self._url}: {exc}",
                remediation="Check the application is running and devices.<name>.url is reachable.",
            ) from exc

    def disconnect(self) -> None:
        page, self._page = self._page, None
        self._app_running = False
        try:
            if page is not None:
                with contextlib.suppress(Exception):
                    if not page.is_closed():
                        page.close()
        finally:
            self._teardown_playwright()

    def is_available(self) -> bool:
        if self._page_factory is not None:
            return True
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        return True

    def health_check(self) -> HealthCheckResult:
        if self._page is None:
            if not self.is_available():
                return HealthCheckResult.failed("playwright not installed")
            return HealthCheckResult.failed("browser not connected")
        if self._page.is_closed():
            return HealthCheckResult.failed("browser page closed")
        return HealthCheckResult.ok("browser healthy", url=self._page.url)

    # -- application lifecycle --------------------------------------------------------

    def start_application(self) -> None:
        page = self._require_page()
        self._logs.clear()
        page.goto(self._url, timeout=self._timeout * 1000)
        self._app_running = True

    def stop_application(self) -> None:
        page = self._require_page()
        page.goto(_BLANK)
        self._app_running = False

    def reset_application(self) -> None:
        # A fresh navigation is the closest analogue to `pm clear` + start.
        self.stop_application()
        self.start_application()

    def is_application_running(self) -> bool:
        return self._page is not None and self._app_running

    # -- observation --------------------------------------------------------------------

    def screenshot(self) -> Image:
        page = self._require_page()
        try:
            data = page.screenshot(type="png")
            with PILImage.open(io.BytesIO(data)) as img:
                return img.convert("RGB")
        except Exception as exc:  # noqa: BLE001 - any Playwright/PIL failure
            raise ScreenshotError(
                f"Browser screenshot failed: {exc}",
                remediation="Check the page is still open and the browser is responsive.",
            ) from exc

    def get_screen_info(self) -> ScreenInfo:
        page = self._require_page()
        size = page.viewport_size or {"width": self._viewport[0], "height": self._viewport[1]}
        return ScreenInfo(width=int(size["width"]), height=int(size["height"]))

    def get_logs(self, lines: int = 200) -> str:
        entries = list(self._logs)[-lines:] if lines > 0 else []
        return "\n".join(entries)

    # -- input ----------------------------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        self._require_page().mouse.click(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        mouse = self._require_page().mouse
        steps = max(1, duration_ms // 16)  # ~60 Hz worth of intermediate events
        mouse.move(x1, y1)
        mouse.down()
        mouse.move(x2, y2, steps=steps)
        mouse.up()

    def press_key(self, key: str) -> None:
        name = key.removeprefix("KEYCODE_")
        mapped = _KEY_MAP.get(name.upper(), name)
        self._require_page().keyboard.press(mapped)
