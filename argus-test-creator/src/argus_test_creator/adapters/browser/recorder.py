"""BrowserRecorder — records a web application through Playwright.

Design: a headed chromium page is opened for the user; an injected script
reports pointer/keyboard/scroll events (with viewport coordinates and light
DOM evidence) through ``page.expose_binding``. Coordinates are viewport
pixels — exactly what Argus's browser adapter uses for ``device.tap``.

DOM metadata (tag, id, accessible name, bounding box) is stored as event
metadata only; it never becomes an Argus action.

Playwright's sync API is not thread-safe across threads, so all Playwright
calls run on one dedicated thread (``_PlaywrightThread``) and other threads
submit work to it.
"""

from __future__ import annotations

import io
import queue
import threading
from collections.abc import Callable
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus_test_creator.core.errors import RecordingError, ScreenshotError, TargetConnectionError
from argus_test_creator.models.capabilities import RecorderCapabilities, TargetProfile
from argus_test_creator.models.common import Point
from argus_test_creator.models.recording import RecordingEvent, RecordingEventType
from argus_test_creator.recording.adapter import EventSink, RecorderRegistry
from argus_test_creator.targets.catalog import PLATFORM_CAPABILITIES

INSTALL = "pip install 'argus-test-creator[browser]' && playwright install chromium"

# Reported keys are mapped to Argus/Android-style names where Argus's browser
# adapter has an explicit mapping; printable characters pass through.
_KEY_MAP = {
    "Enter": "ENTER", "Escape": "BACK", "Tab": "TAB", " ": "SPACE", "Backspace": "BACKSPACE",
    "ArrowUp": "DPAD_UP", "ArrowDown": "DPAD_DOWN", "ArrowLeft": "DPAD_LEFT",
    "ArrowRight": "DPAD_RIGHT", "Home": "HOME", "End": "END", "PageUp": "PAGE_UP",
    "PageDown": "PAGE_DOWN",
}

_INIT_SCRIPT = r"""
(() => {
  if (window.__argusCreatorInstalled) return;
  window.__argusCreatorInstalled = true;
  const describe = (el) => {
    if (!el || !el.getBoundingClientRect) return {};
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName ? el.tagName.toLowerCase() : null,
      id: el.id || null,
      name: el.getAttribute ? (el.getAttribute('aria-label') || el.getAttribute('name') ||
             (el.innerText || '').trim().slice(0, 80) || null) : null,
      box: {x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width),
            height: Math.round(r.height)},
    };
  };
  const send = (payload) => { try { window.__argusCreatorEvent(payload); } catch (e) {} };
  window.addEventListener('pointerdown', (e) => send({type: 'pointer_down', x: e.clientX,
    y: e.clientY, button: e.button, target: describe(e.target)}), true);
  window.addEventListener('pointerup', (e) => send({type: 'pointer_up', x: e.clientX,
    y: e.clientY, button: e.button, target: describe(e.target)}), true);
  window.addEventListener('pointermove', (e) => { if (e.buttons) send({type: 'pointer_move',
    x: e.clientX, y: e.clientY}); }, true);
  window.addEventListener('keydown', (e) => send({type: 'key_press', key: e.key,
    modifiers: [e.ctrlKey && 'ctrl', e.altKey && 'alt', e.shiftKey && 'shift',
                e.metaKey && 'meta'].filter(Boolean)}), true);
  window.addEventListener('wheel', (e) => send({type: 'scroll', x: e.clientX, y: e.clientY,
    dx: e.deltaX, dy: e.deltaY}), {capture: true, passive: true});
})();
"""


class _PlaywrightThread(threading.Thread):
    """Owns the Playwright objects; runs callables submitted from other threads."""

    def __init__(self) -> None:
        super().__init__(name="playwright", daemon=True)
        self._tasks: queue.Queue[tuple[Callable[[], Any], queue.Queue[tuple[bool, Any]]] | None]
        self._tasks = queue.Queue()

    def run(self) -> None:
        while True:
            item = self._tasks.get()
            if item is None:
                return
            fn, reply = item
            try:
                reply.put((True, fn()))
            except BaseException as exc:  # noqa: BLE001 - propagate to caller
                reply.put((False, exc))

    def call(self, fn: Callable[[], Any], timeout: float = 60.0) -> Any:
        reply: queue.Queue[tuple[bool, Any]] = queue.Queue()
        self._tasks.put((fn, reply))
        try:
            ok, value = reply.get(timeout=timeout)
        except queue.Empty as exc:
            raise RecordingError(f"Browser did not respond within {timeout:.0f}s.") from exc
        if ok:
            return value
        raise value

    def stop(self) -> None:
        self._tasks.put(None)


class BrowserRecorder:
    def __init__(self, target: TargetProfile, options: dict[str, Any] | None = None) -> None:
        self.target = target
        settings = {**target.settings, **(options or {})}
        self._url = str(settings.get("url") or "")
        self._browser_name = str(settings.get("browser", "chromium"))
        viewport = settings.get("viewport", [1280, 720])
        self._viewport = (int(viewport[0]), int(viewport[1]))
        self._headless = bool(settings.get("headless", False))
        self._capabilities = PLATFORM_CAPABILITIES["web"]
        self._thread: _PlaywrightThread | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._sink: EventSink | None = None
        self._recording = False
        self._connected = False
        self._last_url: str | None = None
        self._lock = threading.Lock()

    @property
    def capabilities(self) -> RecorderCapabilities:
        return self._capabilities

    @property
    def connected(self) -> bool:
        return self._connected

    def describe_limitations(self) -> list[str]:
        return list(self._capabilities.limitations)

    # -- connection ------------------------------------------------------------------

    def connect(self) -> None:
        if not self._url:
            raise TargetConnectionError(
                "The browser target needs a URL.",
                remediation="Open Target → Settings and enter the application URL.",
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise TargetConnectionError(
                "Playwright is not installed.", remediation=INSTALL,
            ) from exc
        self._thread = _PlaywrightThread()
        self._thread.start()

        def start() -> None:
            self._playwright = sync_playwright().start()
            launcher = getattr(self._playwright, self._browser_name)
            self._browser = launcher.launch(headless=self._headless)
            context = self._browser.new_context(
                viewport={"width": self._viewport[0], "height": self._viewport[1]},
            )
            context.expose_binding("__argusCreatorEvent", self._on_page_event)
            context.add_init_script(_INIT_SCRIPT)
            self._page = context.new_page()
            self._page.on("framenavigated", self._on_navigated)
            self._page.goto(self._url, wait_until="load")
            self._last_url = self._page.url

        try:
            self._thread.call(start, timeout=90)
        except Exception as exc:  # noqa: BLE001
            self.disconnect()
            raise TargetConnectionError(
                f"Could not open {self._url} in {self._browser_name}: {exc}",
                remediation=f"Check the URL and that browsers are installed ({INSTALL}).",
                details=repr(exc),
            ) from exc
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._recording = False
        thread = self._thread
        if thread is None:
            return

        def stop() -> None:
            page, browser, playwright = self._page, self._browser, self._playwright
            self._browser = self._playwright = self._page = None
            for step in (
                lambda: page.context.close() if page is not None else None,
                lambda: browser.close() if browser is not None else None,
                lambda: playwright.stop() if playwright is not None else None,
            ):
                try:
                    step()
                except Exception:  # noqa: BLE001 - best effort on shutdown
                    pass

        try:
            thread.call(stop, timeout=30)
        except Exception:  # noqa: BLE001
            pass
        thread.stop()
        self._thread = None

    # -- observation ---------------------------------------------------------------------

    def screenshot(self) -> Image:
        thread, page = self._thread, self._page
        if thread is None or page is None:
            raise ScreenshotError("Browser is not connected.", remediation="Connect the target.")
        try:
            data = thread.call(lambda: page.screenshot(type="png"), timeout=30)
        except Exception as exc:  # noqa: BLE001
            raise ScreenshotError(
                f"Browser screenshot failed: {exc}",
                remediation="Make sure the browser window is still open, then retry.",
                details=repr(exc),
            ) from exc
        with PILImage.open(io.BytesIO(data)) as img:
            return img.convert("RGB")

    def screen_size(self) -> tuple[int, int]:
        return self._viewport

    def last_screen_metadata(self) -> dict[str, Any]:
        return {"url": self._last_url}

    def run_in_page(self, fn: Callable[[Any], Any], timeout: float = 60.0) -> Any:
        """Run ``fn(page)`` on the Playwright thread (scripted demos and tests)."""
        thread, page = self._thread, self._page
        if thread is None or page is None:
            raise RecordingError("Browser is not connected.")
        return thread.call(lambda: fn(page), timeout=timeout)

    # -- recording ------------------------------------------------------------------------

    def start_recording(self, sink: EventSink) -> None:
        if not self._connected:
            raise RecordingError("Connect the browser target before recording.")
        self._sink = sink
        self._recording = True

    def stop_recording(self) -> None:
        self._recording = False
        self._sink = None

    # -- callbacks (run on the Playwright thread) -----------------------------------------------

    def _on_navigated(self, frame: Any) -> None:
        try:
            if frame != self._page.main_frame:
                return
            url = frame.url
        except Exception:  # noqa: BLE001
            return
        self._last_url = url
        self._emit(RecordingEventType.NAVIGATION, text=url, metadata={"url": url})

    def _on_page_event(self, _source: Any, payload: dict[str, Any]) -> None:
        kind = payload.get("type")
        meta: dict[str, Any] = {"url": self._last_url}
        if payload.get("target"):
            meta["element"] = payload["target"]
        position = None
        if "x" in payload and "y" in payload:
            position = Point(x=int(payload["x"]), y=int(payload["y"]))
        match kind:
            case "pointer_down":
                self._emit(RecordingEventType.POINTER_DOWN, position=position,
                           button=_button(payload.get("button")), metadata=meta)
            case "pointer_up":
                self._emit(RecordingEventType.POINTER_UP, position=position,
                           button=_button(payload.get("button")), metadata=meta)
            case "pointer_move":
                self._emit(RecordingEventType.POINTER_MOVE, position=position, droppable=True)
            case "key_press":
                key = str(payload.get("key", ""))
                modifiers = tuple(payload.get("modifiers") or ())
                if key in ("Shift", "Control", "Alt", "Meta"):
                    return
                mapped = _KEY_MAP.get(key, key)
                if modifiers and len(key) == 1:
                    mapped = "+".join([*(m.title() for m in modifiers), key])
                self._emit(RecordingEventType.KEY_PRESS, key=mapped, modifiers=modifiers,
                           metadata={**meta, "modifiers": list(modifiers)})
            case "scroll":
                dx, dy = int(payload.get("dx", 0)), int(payload.get("dy", 0))
                end = Point(x=position.x - dx, y=position.y - dy) if position else None
                self._emit(RecordingEventType.SCROLL, position=position, position_end=end,
                           metadata={**meta, "dx": dx, "dy": dy})

    def _emit(self, event_type: RecordingEventType, *, droppable: bool = False, **fields: Any) -> None:  # noqa: E501
        sink = self._sink
        if sink is None or not self._recording:
            return
        sink.push(RecordingEvent(event_type=event_type, **fields), droppable=droppable)


def _button(value: Any) -> str:
    return {0: "left", 1: "middle", 2: "right"}.get(value, "left")


def register(registry: RecorderRegistry) -> None:
    registry.register("browser", BrowserRecorder)
