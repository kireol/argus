# Argus Demo — Web example

A minimal vanilla HTML/CSS/JS single-page app implementing the shared
"Argus Demo" spec: a home screen with a counter and a theme-sensitive
colour swatch, and a settings screen with a dark-theme toggle. It is served
(and instrumented) by `examples/web/server.py`, a Python-stdlib
`http.server` with no third-party dependencies. Argus drives it through
[Playwright](../../docs/browser.md) — the browser adapter treats the page
like any other screen: taps are mouse clicks, verification is visual/OCR,
and the browser console is the device log.

This example is **backend-connected**: it polls
`examples/backend/server.py`'s `GET /api/state` every 500ms and applies
`counter`/`theme` from it, and pushes local changes back — so
`backend.set` visibly changes the running page.

## Prerequisites

- The repo's `.venv` set up per the top-level `README.md`
  (`./install.sh` / `.\install.ps1`, or `uv sync`), with the browser and
  OCR extras installed:

  ```bash
  uv pip install -e ".[browser,ocr]"
  .venv/bin/playwright install chromium
  ```

- OCR needs the `tesseract` binary on `PATH` (see `docs/ocr.md`); on macOS,
  `brew install tesseract`.
- `examples/backend/server.py` running — see `examples/backend/README.md`.
  This example's app runs standalone (with defaults) if the backend is
  unreachable, but `WEB-008` specifically exercises the backend link.

`examples/web/server.py` itself has **no dependencies beyond the Python
standard library** — nothing to `npm install`.

## Build

There is no build step; `server.py` and `static/*` are plain
Python/HTML/CSS/JS, served as-is.

## Run the app

Start the example backend in one terminal:

```bash
python examples/backend/server.py
# Argus Demo backend on http://127.0.0.1:8765
```

Start the web app's server in another terminal (it stays in the
foreground):

```bash
python examples/web/server.py
# Argus Demo web example on http://127.0.0.1:3000
```

Use `--port` to run either server on a different port (also update the
matching URL in `examples/web/argus.yaml` if you do). Open
<http://127.0.0.1:3000/> in a browser to see it directly.

### Controls (1280x720 fixed layout)

| Control | Screen | Centre coordinates |
| --- | --- | --- |
| `+` (increment counter) | Home | (640, 400) |
| `Settings` | Home | (640, 500) |
| Colour swatch | Home | (1100, 150) |
| `Dark theme` checkbox | Settings | (640, 300) |
| `Back` | Settings | (640, 500) |

The layout uses fixed pixel positioning (no responsive units) so these
coordinates are stable; the Playwright viewport is configured to
1280x720 in `argus.yaml` to match.

## Run the tests

With both servers still running, from the repository root:

```bash
.venv/bin/argus --dry-run --config examples/web/argus.yaml   # validates config + tests, touches nothing
.venv/bin/argus run --config examples/web/argus.yaml
```

Expected: `Executed: 10`, `Passed: 10`, `Failed: 0`.

## What the tests show

`examples/web/tests/demo.yaml` defines `WEB-001`..`WEB-010` under the
`Demo` feature:

| ID | Shows |
| --- | --- |
| WEB-001 | App loads; the "Argus Demo" title is readable via OCR |
| WEB-002 | Instrumentation reports `ready: true`, `screen: home` on load |
| WEB-003 | Tapping `+` once increments the counter (UI + `application_state`) |
| WEB-004 | Three taps show `Count: 3` and log `Counter: 3` (browser console) |
| WEB-005 | Tapping `Settings` opens the settings screen |
| WEB-006 | Tapping `Back` returns to Home with the counter preserved |
| WEB-007 | The `Dark theme` toggle turns the swatch purple (`pixel_matches`) |
| WEB-008 | `backend.set {counter: 42}` is picked up by the page's poll and shown as `Count: 42` |
| WEB-009 | Resetting the backend, then `device.reset`, shows `Count: 0` again |
| WEB-010 | A documentation screenshot of the home screen |

The feature-level `setup`/`teardown` reset the backend and start/stop the
browser once per run (see "Feature-level setup and teardown" in
`docs/test-authoring.md`). Because this example polls the backend, most
individual tests additionally reset the backend in their own `setup`
(and some in `teardown`) so each passes independently of run order or
`--test`/`--tag` filtering — `device.reset` alone only reloads the page,
which then re-reads whatever the backend currently holds (see "Isolation"
in `docs/test-authoring.md`).

### Instrumentation and logs

`server.py` answers `GET /test/status`, `/test/state`, `/test/health` on
the same port as the static files; `app.js` reports its state with
`POST /test/state` after every local change (tap, navigation, theme
toggle) so those endpoints reflect what's on screen. Every user action also
`console.log`s one of `App ready`, `Counter: N`, `Screen: home`,
`Screen: settings`, `Theme: light`, `Theme: dark`, which Argus reads as the
browser console log (`log_contains`).

## Troubleshooting

- **Pre-flight fails on the browser device / `Unable to open
  http://127.0.0.1:3000/`** — `examples/web/server.py` isn't running, or
  the port doesn't match `devices.web.url` in `argus.yaml`.
- **`WEB-008` fails / counter never reaches 42** — confirm
  `examples/backend/server.py` is running on port 8765 (`curl
  http://127.0.0.1:8765/health`); the page polls it every 500ms and gives
  up silently (by design) if it's unreachable.
- **`text_present` assertions fail (`WEB-001`, etc.)** — OCR must be
  installed and configured for Argus; see `docs/ocr.md` (`tesseract` on
  `PATH`).
- **`WEB-002` instrumentation checks time out** — `devices.web.instrumentation.base_url`
  must point at the same port as `examples/web/server.py` (3000 by
  default); the page has to complete at least one `POST /test/state` (on
  load) before `ready` becomes `true`.
- **`Address already in use`** — another process is bound to 3000 (or
  8765 for the backend); stop it or run the server with `--port` and
  update `argus.yaml` to match.
- **Playwright errors about a missing browser** — run
  `.venv/bin/playwright install chromium`.
