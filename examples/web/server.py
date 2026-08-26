#!/usr/bin/env python3
"""Argus Demo web example -- static file server + instrumentation.

    python examples/web/server.py            # http://127.0.0.1:3000
    python examples/web/server.py --port 9000

Serves examples/web/static/ (index.html, app.js, style.css) and the Argus
instrumentation contract on the *same* port:

    GET  /test/status  -> {"application": "ArgusDemo", "version": "1.0.0",
                            "ready": bool, "screen": "home"|"settings",
                            "capabilities": ["status", "state"]}
    GET  /test/state   -> {"counter": N, "theme": "light"|"dark",
                            "screen": "home"|"settings"}
    GET  /test/health  -> {"ok": true}
    POST /test/state   -> the page reports its current state here after
                           every local change (counter/theme/screen); the
                           body is merged into the server's copy and marks
                           the app "ready".

Sole dependency: the Python standard library.
"""
from __future__ import annotations

import argparse
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = (Path(__file__).resolve().parent / "static").resolve()

APP_NAME = "ArgusDemo"
APP_VERSION = "1.0.0"

_state: dict = {"counter": 0, "theme": "light", "screen": "home"}
_ready = False
_lock = threading.Lock()

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


def _resolve_static(rel_path: str) -> Path | None:
    """Resolve a request path under STATIC_DIR, refusing to escape it."""
    candidate = (STATIC_DIR / rel_path.lstrip("/")).resolve()
    if candidate != STATIC_DIR and STATIC_DIR not in candidate.parents:
        return None
    return candidate


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_static(self, rel_path: str) -> None:
        path = _resolve_static(rel_path)
        if path is None or not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        data = path.read_bytes()
        content_type = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/test/health":
            self._send_json(200, {"ok": True})
        elif self.path == "/test/status":
            with _lock:
                self._send_json(
                    200,
                    {
                        "application": APP_NAME,
                        "version": APP_VERSION,
                        "ready": _ready,
                        "screen": _state["screen"],
                        "capabilities": ["status", "state"],
                    },
                )
        elif self.path == "/test/state":
            with _lock:
                self._send_json(200, dict(_state))
        elif self.path in ("/", ""):
            self._send_static("index.html")
        else:
            self._send_static(self.path)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/test/state":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return
        if not isinstance(body, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "body must be an object"})
            return
        global _ready
        with _lock:
            _state.update(body)
            _ready = True
            self._send_json(200, dict(_state))

    def log_message(self, fmt, *args):  # quieter console
        print("%s %s" % (self.command, self.path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Argus Demo web example on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
