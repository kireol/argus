#!/usr/bin/env python3
"""Argus Demo backend -- a tiny REST API Argus drives with backend.set.

    python examples/backend/server.py            # http://127.0.0.1:8765
    python examples/backend/server.py --port 9000

GET  /health      -> {"ok": true}
GET  /api/state   -> {"counter": 0, "theme": "light"}
POST /api/state   -> merge JSON body into state, return the full state
POST /api/reset   -> back to {"counter": 0, "theme": "light"}
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INITIAL_STATE = {"counter": 0, "theme": "light"}
_state = dict(INITIAL_STATE)
_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
        elif self.path == "/api/state":
            with _lock:
                self._send(200, dict(_state))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if self.path == "/api/reset":
            with _lock:
                _state.clear()
                _state.update(INITIAL_STATE)
                self._send(200, dict(_state))
            return
        if self.path != "/api/state":
            self._send(404, {"error": "not found"})
            return
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "body must be an object"})
            return
        with _lock:
            _state.update(body)
            self._send(200, dict(_state))

    def log_message(self, fmt, *args):  # quieter console
        print(f"{self.command} {self.path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Argus Demo backend on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
