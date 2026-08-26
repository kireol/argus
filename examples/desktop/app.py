#!/usr/bin/env python3
"""Argus Demo desktop app (tkinter).

Run standalone:

    python examples/desktop/app.py
    python examples/desktop/app.py --no-backend   # skip polling the demo backend

A tkinter window titled "Argus Demo", geometry 800x600+0+0 (top-left of the
primary screen so coordinates are predictable). See examples/README.md for
the shared "Argus Demo" spec every example port implements.

Instrumentation (an in-process ThreadingHTTPServer, daemon thread) listens
on http://127.0.0.1:8085:

    GET /test/status  -> {"application": "ArgusDemo", "version": "1.0.0",
                           "ready": true, "screen": "home"|"settings",
                           "capabilities": ["status", "state"]}
    GET /test/state   -> {"counter": N, "theme": "light"|"dark",
                           "screen": "home"|"settings"}
    GET /test/health  -> 200 {"ok": true}

Unless --no-backend is given, the app polls the example backend
(examples/backend/server.py, http://127.0.0.1:8765) every 500ms via
urllib in a tkinter `after()` callback and applies counter/theme from it;
it also POSTs its own local changes back so backend and UI stay in sync.
If the backend is unreachable the app keeps running standalone.

Ctrl+Q quits. Logs exact lines to stdout: "App ready", "Counter: N",
"Screen: home", "Screen: settings", "Theme: light", "Theme: dark".
"""
from __future__ import annotations

import argparse
import json
import threading
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tkinter import font as tkfont
from urllib import error as urlerror
from urllib import request as urlrequest

APP_NAME = "ArgusDemo"
VERSION = "1.0.0"
INSTRUMENTATION_HOST = "127.0.0.1"
INSTRUMENTATION_PORT = 8085
BACKEND_BASE_URL = "http://127.0.0.1:8765"
POLL_INTERVAL_MS = 500
BACKEND_TIMEOUT_S = 0.3

LIGHT_BG = "#ffffff"
DARK_BG = "#1e1e2e"
LIGHT_FG = "#000000"
DARK_FG = "#ffffff"
LIGHT_SWATCH = "#2ecc71"
DARK_SWATCH = "#8e44ad"


class AppState:
    """Shared, lock-protected state read by both tkinter and the HTTP thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counter = 0
        self.theme = "light"
        self.screen = "home"

    def snapshot(self) -> dict:
        with self._lock:
            return {"counter": self.counter, "theme": self.theme, "screen": self.screen}


def make_instrumentation_handler(state: AppState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path == "/test/status":
                snap = state.snapshot()
                self._send_json(
                    200,
                    {
                        "application": APP_NAME,
                        "version": VERSION,
                        "ready": True,
                        "screen": snap["screen"],
                        "capabilities": ["status", "state"],
                    },
                )
            elif self.path == "/test/state":
                self._send_json(200, state.snapshot())
            elif self.path == "/test/health":
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"error": "not found"})

        def log_message(self, fmt: str, *args: object) -> None:  # quieter console
            pass

    return Handler


class DemoApp:
    def __init__(self, root: tk.Tk, state: AppState, *, use_backend: bool = True) -> None:
        self.root = root
        self.state = state
        self.use_backend = use_backend

        root.title("Argus Demo")
        root.geometry("800x600+0+0")
        root.resizable(False, False)

        title_font = tkfont.Font(size=24)
        label_font = tkfont.Font(size=16)

        self.home_frame = tk.Frame(root, width=800, height=600)
        self.settings_frame = tk.Frame(root, width=800, height=600)
        for frame in (self.home_frame, self.settings_frame):
            frame.place(x=0, y=0, width=800, height=600)

        # -- Home screen --------------------------------------------------------------
        self.home_title = tk.Label(self.home_frame, text="Argus Demo", font=title_font)
        self.home_title.place(x=20, y=20, anchor="nw")

        self.count_label = tk.Label(self.home_frame, text="Count: 0", font=label_font)
        self.count_label.place(x=400, y=180, anchor="center")

        self.plus_button = tk.Button(
            self.home_frame, text="+", font=label_font, width=3, command=self.increment
        )
        self.plus_button.place(x=400, y=300, anchor="center")

        self.settings_button = tk.Button(
            self.home_frame, text="Settings", command=self.show_settings
        )
        self.settings_button.place(x=400, y=400, anchor="center")

        self.swatch = tk.Canvas(
            self.home_frame, width=160, height=80, highlightthickness=0, bd=0
        )
        self.swatch.place(x=600, y=60, anchor="nw")

        # -- Settings screen ------------------------------------------------------------
        self.settings_title = tk.Label(self.settings_frame, text="Settings", font=title_font)
        self.settings_title.place(x=20, y=20, anchor="nw")

        self.dark_var = tk.BooleanVar(value=False)
        self.dark_toggle = tk.Checkbutton(
            self.settings_frame,
            text="Dark theme",
            variable=self.dark_var,
            command=self.toggle_theme,
        )
        self.dark_toggle.place(x=400, y=250, anchor="center")

        self.back_button = tk.Button(self.settings_frame, text="Back", command=self.show_home)
        self.back_button.place(x=400, y=400, anchor="center")

        self._labels = (
            self.home_title,
            self.count_label,
            self.settings_title,
            self.dark_toggle,
        )
        self._frames = (self.home_frame, self.settings_frame)
        self._buttons = (self.plus_button, self.settings_button, self.back_button)

        root.bind("<Control-q>", lambda _event: self.quit())
        root.protocol("WM_DELETE_WINDOW", self.quit)

        self._apply_theme(log=False)
        self._render_counter()
        self.home_frame.tkraise()

        print("App ready", flush=True)
        print("Screen: home", flush=True)

        if self.use_backend:
            self.root.after(POLL_INTERVAL_MS, self._poll_backend)

    # -- rendering ------------------------------------------------------------------------

    def _render_counter(self) -> None:
        self.count_label.configure(text=f"Count: {self.state.counter}")

    def _apply_theme(self, *, log: bool = True) -> None:
        theme = self.state.theme
        bg = DARK_BG if theme == "dark" else LIGHT_BG
        fg = DARK_FG if theme == "dark" else LIGHT_FG
        swatch_color = DARK_SWATCH if theme == "dark" else LIGHT_SWATCH

        self.root.configure(bg=bg)
        for frame in self._frames:
            frame.configure(bg=bg)
        for label in self._labels:
            label.configure(bg=bg, fg=fg)
        for button in self._buttons:
            button.configure(bg=bg, fg=fg)
        self.dark_toggle.configure(selectcolor=bg)
        self.swatch.configure(bg=swatch_color)
        self.dark_var.set(theme == "dark")

        if log:
            print(f"Theme: {theme}", flush=True)

    # -- navigation -----------------------------------------------------------------------

    def show_home(self) -> None:
        if self.state.screen != "home":
            self.state.screen = "home"
            print("Screen: home", flush=True)
        self.home_frame.tkraise()

    def show_settings(self) -> None:
        if self.state.screen != "settings":
            self.state.screen = "settings"
            print("Screen: settings", flush=True)
        self.settings_frame.tkraise()

    # -- actions --------------------------------------------------------------------------

    def increment(self) -> None:
        self.state.counter += 1
        self._render_counter()
        print(f"Counter: {self.state.counter}", flush=True)
        self._post_backend({"counter": self.state.counter})

    def toggle_theme(self) -> None:
        self.state.theme = "dark" if self.dark_var.get() else "light"
        self._apply_theme()
        self._post_backend({"theme": self.state.theme})

    def quit(self) -> None:
        self.root.destroy()

    # -- backend sync ----------------------------------------------------------------------

    def _post_backend(self, data: dict) -> None:
        if not self.use_backend:
            return
        try:
            body = json.dumps(data).encode()
            req = urlrequest.Request(
                f"{BACKEND_BASE_URL}/api/state",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urlrequest.urlopen(req, timeout=BACKEND_TIMEOUT_S).close()
        except (urlerror.URLError, OSError, ValueError):
            pass  # backend unreachable; keep running standalone

    def _poll_backend(self) -> None:
        try:
            with urlrequest.urlopen(
                f"{BACKEND_BASE_URL}/api/state", timeout=BACKEND_TIMEOUT_S
            ) as resp:
                data = json.loads(resp.read().decode())
            self._apply_backend_state(data)
        except (urlerror.URLError, OSError, ValueError):
            pass  # backend unreachable; keep running standalone
        finally:
            self.root.after(POLL_INTERVAL_MS, self._poll_backend)

    def _apply_backend_state(self, data: dict) -> None:
        counter = data.get("counter")
        if isinstance(counter, int) and counter != self.state.counter:
            self.state.counter = counter
            self._render_counter()
            print(f"Counter: {self.state.counter}", flush=True)

        theme = data.get("theme")
        if theme in ("light", "dark") and theme != self.state.theme:
            self.state.theme = theme
            self._apply_theme()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-backend", action="store_true", help="do not poll the example backend"
    )
    args = parser.parse_args()

    state = AppState()
    handler_cls = make_instrumentation_handler(state)
    server = ThreadingHTTPServer((INSTRUMENTATION_HOST, INSTRUMENTATION_PORT), handler_cls)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    root = tk.Tk()
    DemoApp(root, state, use_backend=not args.no_backend)

    try:
        root.mainloop()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
