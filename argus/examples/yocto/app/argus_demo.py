#!/usr/bin/env python3
"""Argus Demo -- pygame port for the Yocto / embedded-Linux example.

    python3 argus_demo.py                     # fullscreen at the display's resolution
    python3 argus_demo.py --windowed          # windowed, 1280x720
    python3 argus_demo.py --windowed 1024x600 # windowed, custom size

The app draws the shared "Argus Demo" layout (Home / Settings, a counter, a
theme swatch) at a fixed logical resolution of 1280x720 -- the same
coordinates the Argus test suite (``examples/yocto/tests/demo.yaml``) and
``argus.yaml`` (``screen_size: [1280, 720]``) assume, regardless of the
physical display size the window is scaled to.

Instrumentation (``ThreadingHTTPServer`` on ``0.0.0.0:8085`` by default):

    GET  /test/status  -> {"application": "ArgusDemo", "version": "1.0.0",
                            "ready": bool, "screen": "home"|"settings",
                            "capabilities": ["status", "state"]}
    GET  /test/state   -> {"counter": N, "theme": "light"|"dark", "screen": ...}
    GET  /test/health  -> 200 {"ok": true}
    GET  /test/screen  -> current frame as PNG (pygame.image.save to BytesIO) --
                          usable as a Yocto screenshot provider with no
                          compositor at all: `screenshot.command: "curl -s -o
                          {path} http://127.0.0.1:8085/test/screen"`.
    POST /test/input   -> testing-only synthetic input, since the Argus Yocto
                          adapter has no tap()/press_key() of its own (see
                          examples/yocto/README.md "Why shell.run + curl" for
                          the full explanation). Body is JSON:
                              {"key": "RETURN"|"RIGHT"|"ESCAPE"|"BACKSPACE"}
                              {"click": [x, y]}   # in the 1280x720 logical space

Keys (real keyboard or synthetic via /test/input):
    RETURN  -- Home: increments the counter. Settings: toggles the theme.
    RIGHT   -- Home: opens Settings.
    ESCAPE / BACKSPACE -- Settings: returns to Home (counter is preserved).

Mouse clicks on the drawn controls do the same thing as the matching key.

Log lines (exact, printed to stdout -- captured by the systemd journal):
    App ready
    Counter: N
    Screen: home
    Screen: settings
    Theme: light
    Theme: dark
"""
from __future__ import annotations

import argparse
import io
import json
import queue
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pygame

WIDTH, HEIGHT = 1280, 720

LIGHT_BG = (255, 255, 255)
DARK_BG = (0x1E, 0x1E, 0x2E)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREEN = (46, 204, 113)   # light theme swatch
PURPLE = (142, 68, 173)  # dark theme swatch

# Swatch rectangle -- covers the pixel (1100, 150) used by YOC-007.
SWATCH_RECT = pygame.Rect(1050, 100, 150, 100)


class ArgusDemoApp:
    """Owns the shared state, the pygame surface, and the HTTP instrumentation."""

    def __init__(self, host: str, port: int, windowed_size: tuple[int, int] | None) -> None:
        self.host = host
        self.port = port
        self.windowed_size = windowed_size

        self.lock = threading.Lock()
        self.state: dict[str, Any] = {"counter": 0, "theme": "light", "screen": "home"}
        self.ready = False

        self.input_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.frame_lock = threading.Lock()
        self.latest_frame: bytes | None = None

        self._stop_event = threading.Event()
        self._window_size = windowed_size or (WIDTH, HEIGHT)

        # Layout rects (logical 1280x720 space).
        self.plus_rect = pygame.Rect(0, 0, 140, 90)
        self.plus_rect.center = (WIDTH // 2, 320)
        self.settings_rect = pygame.Rect(0, 0, 260, 70)
        self.settings_rect.center = (WIDTH // 2, 460)
        self.toggle_rect = pygame.Rect(0, 0, 60, 60)
        self.toggle_rect.center = (WIDTH // 2 + 160, 300)
        self.back_rect = pygame.Rect(0, 0, 200, 70)
        self.back_rect.center = (WIDTH // 2, 520)

    # -- state transitions (each logs exactly one line) ------------------------

    def _increment(self) -> None:
        with self.lock:
            self.state["counter"] += 1
            n = self.state["counter"]
        print(f"Counter: {n}", flush=True)

    def _goto_settings(self) -> None:
        with self.lock:
            self.state["screen"] = "settings"
        print("Screen: settings", flush=True)

    def _goto_home(self) -> None:
        with self.lock:
            self.state["screen"] = "home"
        print("Screen: home", flush=True)

    def _toggle_theme(self) -> None:
        with self.lock:
            self.state["theme"] = "dark" if self.state["theme"] == "light" else "light"
            theme = self.state["theme"]
        print(f"Theme: {theme}", flush=True)

    # -- input -------------------------------------------------------------------

    def handle_key(self, key: str) -> None:
        key = key.strip().upper()
        with self.lock:
            screen = self.state["screen"]
        if screen == "home":
            if key in ("RETURN", "ENTER", "DPAD_CENTER", "BTN_OK"):
                self._increment()
            elif key in ("RIGHT", "DPAD_RIGHT"):
                self._goto_settings()
        else:
            if key in ("RETURN", "ENTER", "DPAD_CENTER", "BTN_OK"):
                self._toggle_theme()
            elif key in ("ESCAPE", "BACKSPACE", "BACK", "MENU"):
                self._goto_home()

    def handle_click(self, pos: tuple[float, float]) -> None:
        with self.lock:
            screen = self.state["screen"]
        if screen == "home":
            if self.plus_rect.collidepoint(pos):
                self._increment()
            elif self.settings_rect.collidepoint(pos):
                self._goto_settings()
        else:
            if self.toggle_rect.collidepoint(pos):
                self._toggle_theme()
            elif self.back_rect.collidepoint(pos):
                self._goto_home()

    def _drain_input_queue(self) -> None:
        while True:
            try:
                item = self.input_queue.get_nowait()
            except queue.Empty:
                break
            if "key" in item:
                self.handle_key(str(item["key"]))
            elif "click" in item:
                click = item["click"]
                if isinstance(click, (list, tuple)) and len(click) == 2:
                    self.handle_click((float(click[0]), float(click[1])))

    def _window_pos_to_logical(self, pos: tuple[int, int]) -> tuple[float, float]:
        wx, wy = self._window_size
        return pos[0] * WIDTH / wx, pos[1] * HEIGHT / wy

    # -- rendering -----------------------------------------------------------------

    def _draw(self, surface: pygame.Surface) -> None:
        with self.lock:
            screen = self.state["screen"]
            counter = self.state["counter"]
            theme = self.state["theme"]
        bg = LIGHT_BG if theme == "light" else DARK_BG
        fg = BLACK if theme == "light" else WHITE
        swatch_color = GREEN if theme == "light" else PURPLE

        surface.fill(bg)
        if screen == "home":
            self._blit_text(surface, "Argus Demo", self.title_font, fg, (WIDTH // 2, 80))
            self._blit_text(surface, f"Count: {counter}", self.body_font, fg, (WIDTH // 2, 220))
            pygame.draw.rect(surface, fg, self.plus_rect, width=3)
            self._blit_text(surface, "+", self.title_font, fg, self.plus_rect.center)
            pygame.draw.rect(surface, fg, self.settings_rect, width=3)
            self._blit_text(surface, "Settings", self.body_font, fg, self.settings_rect.center)
        else:
            self._blit_text(surface, "Settings", self.title_font, fg, (WIDTH // 2, 80))
            self._blit_text(surface, "Dark theme", self.body_font, fg, (WIDTH // 2 - 140, 300))
            pygame.draw.rect(surface, fg, self.toggle_rect, width=3)
            if theme == "dark":
                pygame.draw.rect(surface, fg, self.toggle_rect.inflate(-16, -16))
            pygame.draw.rect(surface, fg, self.back_rect, width=3)
            self._blit_text(surface, "Back", self.body_font, fg, self.back_rect.center)

        pygame.draw.rect(surface, swatch_color, SWATCH_RECT)

    def _blit_text(
        self, surface: pygame.Surface, text: str, font: pygame.font.Font, color, center
    ) -> None:
        image = font.render(text, True, color)
        rect = image.get_rect(center=center)
        surface.blit(image, rect)

    # -- main loop ------------------------------------------------------------------

    def run(self) -> None:
        pygame.init()
        pygame.display.set_caption("Argus Demo")

        if self.windowed_size:
            display_flags = 0
            self._window_size = self.windowed_size
        else:
            display_flags = pygame.FULLSCREEN
            info = pygame.display.Info()
            self._window_size = (
                (info.current_w, info.current_h) if info.current_w else (WIDTH, HEIGHT)
            )

        display_surface = pygame.display.set_mode(self._window_size, display_flags)
        render_surface = pygame.Surface((WIDTH, HEIGHT))

        self.title_font = pygame.font.SysFont(None, 72)
        self.body_font = pygame.font.SysFont(None, 48)

        server = ThreadingHTTPServer((self.host, self.port), _make_handler(self))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        def _on_term(signum, frame):  # noqa: ANN001 - signal handler signature
            self._stop_event.set()

        signal.signal(signal.SIGTERM, _on_term)
        signal.signal(signal.SIGINT, _on_term)

        self.ready = True
        print("App ready", flush=True)

        clock = pygame.time.Clock()
        try:
            while not self._stop_event.is_set():
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self._stop_event.set()
                    elif event.type == pygame.KEYDOWN:
                        self._handle_pygame_key(event.key)
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        self.handle_click(self._window_pos_to_logical(event.pos))

                self._drain_input_queue()
                self._draw(render_surface)

                if self._window_size == (WIDTH, HEIGHT):
                    display_surface.blit(render_surface, (0, 0))
                else:
                    scaled = pygame.transform.smoothscale(render_surface, self._window_size)
                    display_surface.blit(scaled, (0, 0))
                pygame.display.flip()

                buf = io.BytesIO()
                pygame.image.save(render_surface, buf, "screenshot.png")
                with self.frame_lock:
                    self.latest_frame = buf.getvalue()

                clock.tick(20)
        finally:
            server.shutdown()
            pygame.quit()

    def _handle_pygame_key(self, key: int) -> None:
        mapping = {
            pygame.K_RETURN: "RETURN",
            pygame.K_KP_ENTER: "RETURN",
            pygame.K_RIGHT: "RIGHT",
            pygame.K_ESCAPE: "ESCAPE",
            pygame.K_BACKSPACE: "BACKSPACE",
        }
        name = mapping.get(key)
        if name:
            self.handle_key(name)


def _make_handler(app: ArgusDemoApp) -> type[BaseHTTPRequestHandler]:
    class InstrumentationHandler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path == "/test/status":
                with app.lock:
                    screen = app.state["screen"]
                self._send_json(
                    200,
                    {
                        "application": "ArgusDemo",
                        "version": "1.0.0",
                        "ready": app.ready,
                        "screen": screen,
                        "capabilities": ["status", "state"],
                    },
                )
            elif self.path == "/test/state":
                with app.lock:
                    body = dict(app.state)
                self._send_json(200, body)
            elif self.path == "/test/health":
                self._send_json(200, {"ok": True})
            elif self.path == "/test/screen":
                with app.frame_lock:
                    png = app.latest_frame
                if png is None:
                    self._send_json(503, {"error": "no frame yet"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if self.path != "/test/input":
                self._send_json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid json"})
                return
            if not isinstance(body, dict):
                self._send_json(400, {"error": "body must be an object"})
                return
            app.input_queue.put(body)
            self._send_json(200, {"ok": True})

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
            print(f"{self.command} {self.path}", flush=True)

    return InstrumentationHandler


def _parse_size(value: str) -> tuple[int, int]:
    try:
        w, h = value.lower().split("x", 1)
        return int(w), int(h)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected WxH, got {value!r}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Argus Demo pygame app")
    parser.add_argument(
        "--windowed",
        nargs="?",
        const="1280x720",
        default=None,
        metavar="WxH",
        help="Run in a window instead of fullscreen (default size 1280x720)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Instrumentation bind host")
    parser.add_argument("--port", type=int, default=8085, help="Instrumentation port")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    windowed_size = _parse_size(args.windowed) if args.windowed else None
    app = ArgusDemoApp(args.host, args.port, windowed_size)
    app.run()


if __name__ == "__main__":
    main()
