"""Serve the Movies web demo (stdlib only) for browser recording and Argus runs.

    python -m argus_test_creator.demo.web_server [--port 3210]

Also serves the tiny Argus instrumentation contract (``/test/status``,
``/test/state``, ``/test/health``) so instrumentation conditions can be
demonstrated.
"""

from __future__ import annotations

import argparse
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "web"
_state: dict[str, object] = {"screen": "home"}
_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict[str, object]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/test/status":
            with _lock:
                self._json(200, {"application": "ArgusMoviesDemo", "version": "1.0.0",
                                 "ready": True, "screen": _state["screen"],
                                 "capabilities": ["status", "state"]})
            return
        if self.path == "/test/state":
            with _lock:
                self._json(200, dict(_state))
            return
        if self.path == "/test/health":
            self._json(200, {"ok": True})
            return
        if self.path in ("/", "/index.html"):
            data = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/test/state":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        with _lock:
            if isinstance(body, dict):
                _state.update(body)
            self._json(200, dict(_state))

    def log_message(self, *_args: object) -> None:  # quiet
        return


def serve(port: int = 3210, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="demo-web", daemon=True)
    thread.start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3210)
    args = parser.parse_args()
    server = serve(args.port)
    print(f"Argus Movies demo: http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
