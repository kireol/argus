"""Fake adapters for development and framework self-tests.

FakeDevice serves predefined screenshots so image/text/timeout/failure paths
can be exercised without hardware. FakeBackend and FakeInstrumentation keep
state in memory. All example tests run against these.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from PIL.Image import Image

from argus.adapters.backend import BackendAdapter
from argus.adapters.base import Device, DeviceCapabilities
from argus.config.models import BackendConfig, DeviceConfig
from argus.exceptions import DeviceConnectionError, ScreenshotError
from argus.instrumentation.client import InstrumentationClient, InstrumentationStatus
from argus.models.common import HealthCheckResult, ScreenInfo


class FakeDevice(Device):
    """In-memory device: serves queued screenshots and records inputs."""

    def __init__(
        self,
        name: str = "fake",
        *,
        screen_size: tuple[int, int] = (1280, 720),
        screenshots: list[Image | str | Path] | None = None,
        screenshot_dir: str | Path | None = None,
        fail_screenshot: bool = False,
        available: bool = True,
        platform: str = "fake",
        render: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name)
        self._screen_size = screen_size
        self._queue: list[Image | str | Path] = list(screenshots or [])
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self._render = render
        self._state_provider: Callable[[], dict[str, Any]] | None = None
        self.fail_screenshot = fail_screenshot
        self.available = available
        self._platform = platform
        self.connected = False
        self.app_running = False
        self.taps: list[tuple[int, int]] = []
        self.swipes: list[tuple[int, int, int, int]] = []
        self.keys: list[str] = []
        self.log_lines: list[str] = ["fake device log"]
        self.screenshot_count = 0

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> FakeDevice:
        options = config.options
        size = options.get("screen_size", [1280, 720])
        return cls(
            name,
            screen_size=(int(size[0]), int(size[1])),
            screenshot_dir=options.get("screenshot_dir"),
            screenshots=options.get("screenshots"),
            platform=config.effective_platform,
            render=options.get("render"),
        )

    def bind_state_provider(self, provider: Callable[[], dict[str, Any]]) -> None:
        """Attach a state source (e.g. FakeBackend.get_state) for rendering."""
        self._state_provider = provider

    # -- capabilities ------------------------------------------------------------

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
        return self._platform

    # -- connection ---------------------------------------------------------------

    def connect(self) -> None:
        if not self.available:
            raise DeviceConnectionError(f"Fake device {self.name!r} is unavailable.")
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def is_available(self) -> bool:
        return self.available

    def health_check(self) -> HealthCheckResult:
        if not self.available:
            return HealthCheckResult.failed("fake device marked unavailable")
        return HealthCheckResult.ok("fake device healthy", connected=self.connected)

    # -- application lifecycle -------------------------------------------------------

    def start_application(self) -> None:
        self.app_running = True

    def stop_application(self) -> None:
        self.app_running = False

    def is_application_running(self) -> bool:
        return self.app_running

    # -- observation --------------------------------------------------------------------

    def queue_screenshot(self, screenshot: Image | str | Path) -> None:
        self._queue.append(screenshot)

    def screenshot(self) -> Image:
        if self.fail_screenshot:
            raise ScreenshotError(f"Fake device {self.name!r}: screenshot failure injected.")
        self.screenshot_count += 1
        if self._render is not None and self._state_provider is not None:
            return self._render_state()
        source: Image | str | Path
        if self._queue:
            # Serve the queue in order, holding on the last frame.
            source = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        elif self._screenshot_dir is not None:
            frames = sorted(self._screenshot_dir.glob("*.png"))
            if not frames:
                raise ScreenshotError(
                    f"Fake device {self.name!r}: no PNGs in {self._screenshot_dir}."
                )
            source = frames[min(self.screenshot_count - 1, len(frames) - 1)]
        else:
            return PILImage.new("RGB", self._screen_size, color=(16, 16, 16))
        if isinstance(source, (str, Path)):
            with PILImage.open(source) as img:
                return img.convert("RGB")
        return source

    def _render_state(self) -> Image:
        """Render the bound backend state into a screenshot.

        This makes the fake ecosystem honest end-to-end: `backend.set` changes
        state, the "screen" changes, and OpenCV/OCR verification does real work.

        Render config (all keys optional)::

            render:
              background: "#101018"
              state_image:
                key: movieId                # state field to read
                template: "movie_{value}.png"
                search_dirs: [assets/images]
                position: [100, 100]
              state_text:
                key: movieId
                map: {"123": "Star Wars", "456": "The Matrix"}
                position: [600, 120]
                size: 48
        """
        assert self._render is not None and self._state_provider is not None
        state = self._state_provider()
        background = self._render.get("background", "#101018")
        screen = PILImage.new("RGB", self._screen_size, color=background)

        image_cfg = self._render.get("state_image")
        if image_cfg:
            value = state.get(image_cfg.get("key", "movieId"))
            if value is not None:
                filename = str(image_cfg.get("template", "{value}.png")).format(value=value)
                for base in image_cfg.get("search_dirs", ["assets/images"]):
                    candidate = Path(base) / filename
                    if candidate.is_file():
                        with PILImage.open(candidate) as artwork:
                            position = image_cfg.get("position", [100, 100])
                            screen.paste(
                                artwork.convert("RGB"),
                                (int(position[0]), int(position[1])),
                            )
                        break

        text_cfg = self._render.get("state_text")
        if text_cfg:
            value = state.get(text_cfg.get("key", "movieId"))
            text = (text_cfg.get("map") or {}).get(str(value))
            if text:
                draw = ImageDraw.Draw(screen)
                position = text_cfg.get("position", [600, 120])
                try:
                    font = ImageFont.load_default(size=int(text_cfg.get("size", 48)))
                except TypeError:  # Pillow < 10.1 fallback
                    font = ImageFont.load_default()
                draw.text(
                    (int(position[0]), int(position[1])),
                    str(text),
                    fill="#ffffff",
                    font=font,
                )
        return screen

    def get_screen_info(self) -> ScreenInfo:
        return ScreenInfo(width=self._screen_size[0], height=self._screen_size[1])

    def get_logs(self, lines: int = 200) -> str:
        return "\n".join(self.log_lines[-lines:])

    # -- input ------------------------------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.swipes.append((x1, y1, x2, y2))

    def press_key(self, key: str) -> None:
        self.keys.append(key)


class FakeBackend(BackendAdapter):
    """In-memory backend with the same surface as BackendAdapter."""

    def __init__(self, initial_state: dict[str, Any] | None = None) -> None:
        # Deliberately do NOT call super().__init__ — no HTTP client needed.
        self._config = BackendConfig(base_url="http://fake-backend")
        self.state: dict[str, Any] = dict(initial_state or {})
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.healthy = True

    def request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        self.requests.append((method, endpoint, kwargs))

        class _Response:
            status_code = 200
            is_success = True
            text = "{}"

            @staticmethod
            def json() -> dict[str, Any]:
                return {}

        return _Response()

    def set_state(self, data: dict[str, Any], endpoint: str | None = None) -> Any:
        self.requests.append(("POST", endpoint or "/api/state", {"json": data}))
        self.state.update(data)
        return dict(self.state)

    def get_state(self, endpoint: str | None = None) -> Any:
        return dict(self.state)

    def health_check(self) -> HealthCheckResult:
        if self.healthy:
            return HealthCheckResult.ok("fake backend healthy")
        return HealthCheckResult.failed("fake backend marked unhealthy")

    def close(self) -> None:
        pass


class FakeInstrumentation(InstrumentationClient):
    """In-memory instrumentation client."""

    def __init__(
        self,
        status: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        self._status = status or {
            "application": "FakeApp",
            "version": "1.0.0",
            "ready": True,
            "screen": "home",
            "rendering": True,
            "capabilities": ["status", "state", "screen", "rendering"],
        }
        self._state = state or {}
        self.healthy = True

    def set_status(self, **fields: Any) -> None:
        self._status.update(fields)

    def set_state(self, **fields: Any) -> None:
        self._state.update(fields)

    def status(self) -> InstrumentationStatus:
        return InstrumentationStatus.model_validate(self._status)

    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def capabilities(self) -> list[str]:
        return list(self._status.get("capabilities", []))

    def health_check(self) -> HealthCheckResult:
        if self.healthy:
            return HealthCheckResult.ok("fake instrumentation healthy")
        return HealthCheckResult.failed("fake instrumentation marked unhealthy")
