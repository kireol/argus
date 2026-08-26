# Backend example (`BE-*`)

The simplest possible Argus example: no device at all, just the framework
driving and asserting against a REST API with `backend.*` actions and the
`backend_value` condition. It's the reference implementation of the
"Argus Demo" backend contract that every other example (web, desktop,
android, ...) polls and pushes state to; see `examples/README.md` for the
shared demo-app spec.

All commands below are run **from the repository root**.

## Prerequisites

- The repo's `.venv` set up per the top-level `README.md`
  (`./install.sh` / `.\install.ps1`, or `uv sync`).
- Nothing else — `examples/backend/server.py` only uses the Python standard
  library (`http.server`), so there is nothing to install or build.

## Build

There is no build step; `server.py` is plain Python.

## Run the app

Start the demo backend in one terminal (it stays in the foreground):

```bash
python examples/backend/server.py
# Argus Demo backend on http://127.0.0.1:8765
```

Use `--port` to run on a different port (also update `backend.base_url` in
`argus.yaml` if you do):

```bash
python examples/backend/server.py --port 9000
```

Verify it's up:

```bash
curl http://127.0.0.1:8765/health
# {"ok": true}
```

## Run the tests

With the server still running, in another terminal:

```bash
.venv/bin/argus --dry-run --config examples/backend/argus.yaml   # validates config + tests, touches nothing
.venv/bin/argus run --config examples/backend/argus.yaml
```

Expected: `Executed: 8`, `Passed: 8`, `Failed: 0`.

## What the tests show

`examples/backend/tests/demo.yaml` defines `BE-001`..`BE-008` under the
`Demo` feature:

- **BE-001** — `GET /health` returns `200`. Tagged `smoke`.
- **BE-002** — `POST /api/reset` brings the counter back to `0`
  (`backend_value` condition).
- **BE-003** — `backend.set {counter: 5}` is readable back via
  `backend_value`. Tagged `smoke`.
- **BE-004** — `backend.set {theme: dark}` is readable back via
  `backend_value`; teardown resets the backend.
- **BE-005** — setting `theme` after `counter` doesn't clobber `counter`:
  `POST /api/state` **merges** the JSON body into existing state rather than
  replacing it.
- **BE-006** — `POST /api/state` with a non-object JSON body (a list) is
  rejected with `400`. (`backend.post`'s `data` is passed straight through
  as the JSON payload — it isn't restricted to objects — so this exercises
  the server's own validation, not Argus's.)
- **BE-007** — an unrouted path returns `404`.
- **BE-008** — after `backend.set {counter: 9}`, a `wait_until` polls
  `backend_value` until the counter reads `9`, demonstrating the framework's
  polling/synchronization mechanism even though the state here is already
  set by the time the poll starts.

The suite also declares a top-level `features: Demo:` block with `setup`/
`teardown` steps that reset the backend once before the first selected test
and once after the last (see "Feature-level setup and teardown" in
`docs/test-authoring.md`). Individual tests that mutate state (`BE-003`
through `BE-005`, `BE-008`) additionally reset in their own `setup`/
`teardown` so each passes independently of run order or `--test`/`--tag`
filtering, per the "Isolation" guidance in the same doc.

### `devices: {}` — no device needed

`examples/backend/argus.yaml` sets `devices: {}` and none of the tests
declare a `platforms:` list. This is deliberate and confirmed working: per
`docs/test-authoring.md` and the engine (`TestRunner._platforms_for` in
`src/argus/engine/runner.py`), "a test without a `platforms:` list runs
once with no device bound." The backend adapter is created independently of
any device (`RunSession.backend` in `src/argus/engine/session.py`), so a
backend-only test suite genuinely needs no `devices:` entries at all — no
`fake` placeholder device required. Both `.venv/bin/argus --dry-run --config
examples/backend/argus.yaml` and `.venv/bin/argus run --config
examples/backend/argus.yaml` were run against this config to confirm.

## Troubleshooting

- **Pre-flight fails on "Backend API"** — the server isn't running, or
  `backend.base_url` in `argus.yaml` doesn't match the port you started it
  on.
- **`Backend request ... failed: ...` / connection refused** — start
  `examples/backend/server.py` first; it must be running for the whole test
  run.
- **`Address already in use`** — another process is already bound to 8765;
  either stop it or run the server with `--port` and update
  `backend.base_url` in `argus.yaml` to match.
- **BE-006 fails with a different status** — the demo server intentionally
  treats "valid JSON that isn't an object" (e.g. a list) as a `400`, since
  `POST /api/state` only knows how to merge object bodies. If you changed
  `server.py`'s validation, update this test to match.
