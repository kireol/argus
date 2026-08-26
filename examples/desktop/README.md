# Desktop example (`DSK-*`)

A tkinter port of the "Argus Demo" application (see `examples/README.md`
for the shared spec), driven through Argus's `desktop` adapter
(`docs/desktop.md`), which screenshots the display with `pyautogui` and
drives it with real mouse clicks and keyboard input.

All commands below are run **from the repository root**.

## Prerequisites

- The repo's `.venv` set up per the top-level `README.md` (`./install.sh` /
  `.\install.ps1`, or `uv sync`).
- Desktop + OCR extras: `uv pip install -e ".[desktop,ocr]"`.
- [Tesseract](https://github.com/tesseract-ocr/tesseract) on `PATH` (used by
  the `text_present`/`text_not_present` conditions):
  `brew install tesseract` (macOS), `apt install tesseract-ocr` (Linux), or
  the [Windows installer](https://github.com/UB-Mannheim/tesseract/wiki).
- The example backend running (`examples/backend/server.py`, port 8765) —
  the desktop app polls it and the tests drive it with `backend.*` actions.
- Know your display's pixel scale (1x/standard-DPI vs 2x/Retina/HiDPI) —
  see "Measure your display" below — and pick `argus.yaml` or
  `argus.retina.yaml` accordingly. **The committed defaults in `argus.yaml`
  assume a 1x display and were verified on one; `argus.retina.yaml`'s
  doubled values were derived by calculation, not verified on real Retina
  hardware.**
- **macOS only:** grant your terminal app **Screen Recording** and
  **Accessibility** permission (System Settings → Privacy & Security), then
  restart the terminal. Without Screen Recording, `pyautogui.screenshot()`
  comes back black or fails outright; without Accessibility, clicks and key
  presses are silently dropped. See "Troubleshooting" below — this
  permission gate is the most common reason the run fails on a fresh
  machine.
- **Linux:** an X11 session with `DISPLAY` set, plus `scrot` and
  `python3-tk` (`sudo apt install scrot python3-tk python3-dev`). Under
  Wayland, run under XWayland or `xvfb-run`.
- **Windows:** nothing extra; run the terminal at the same integrity level
  as the app.

## Build

There is no build step; `app.py` is plain Python (`tkinter` + the standard
library `http.server`).

## Run the app

Start the demo backend in one terminal:

```bash
python examples/backend/server.py
# Argus Demo backend on http://127.0.0.1:8765
```

In another terminal, run the desktop app:

```bash
python examples/desktop/app.py
# (add --no-backend to run standalone, without polling the backend)
```

A window titled "Argus Demo" opens at the top-left of the primary screen
(geometry `800x600+0+0`, so its content lands at predictable pixel
coordinates for the tests). Verify instrumentation is up:

```bash
curl http://127.0.0.1:8085/test/status
# {"application": "ArgusDemo", "version": "1.0.0", "ready": true, "screen": "home", "capabilities": ["status", "state"]}
```

`ready` is a real readiness flag, not a constant: the instrumentation
server starts before the tkinter window exists, so `ready` is `false` for
a brief instant right after launch and flips to `true` once the window has
been built and the event loop has completed an idle pass (see
`DemoApp._mark_ready` in `app.py`) — by the time `startup_wait` (2s in
`argus.yaml`) elapses it is always `true`.

`Ctrl+Q` quits the app (so does closing the window).

## Measure your display

The `desktop` adapter's `region` and every `device.tap`/`pixel_matches`
coordinate are **screenshot pixels** — the raw pixels pyautogui's
`screenshot()` captures — not tkinter's logical points (see
`docs/desktop.md` and `src/argus/adapters/desktop.py`, `_to_logical()`).
On a 1x/standard-DPI display those are the same numbers as the app's own
800×600 logical geometry; on a 2x/Retina display (common on Apple Silicon
Macs) the raw screenshot is twice as many pixels in each dimension, so the
1x coordinates land at half the intended point and a `region` sized for
800×600 only captures the window's top-left quadrant.

Find out which you have before choosing a config, with the same
interpreters/libraries the adapter itself uses:

```bash
.venv/bin/python - <<'PY'
import pyautogui
logical = pyautogui.size()
raw = pyautogui.screenshot().size
print(f"logical={logical} raw={raw} ratio={raw[0] / logical[0]}")
PY
```

- `ratio` ≈ `1.0` → use `examples/desktop/argus.yaml` (the default).
- `ratio` ≈ `2.0` → use `examples/desktop/argus.retina.yaml`.

Both files declare the same coordinates once, in a `variables:` block, so
switching displays is a matter of pointing `--config` at the other file —
no per-test editing. **The 1x values in `argus.yaml` were verified on a
real 1x display (see "Run the tests" below); the 2x values in
`argus.retina.yaml` were derived by doubling them and have not been
verified against real Retina hardware** — re-run the measurement above
after a run to confirm, and adjust `argus.retina.yaml`'s `variables:`/
`region` if your Retina display's actual ratio differs from exactly 2.0.

## Run the tests

`argus.yaml` (or `argus.retina.yaml`, per "Measure your display" above)
launches `app.py` itself as the device under test (via
`devices.desktop_app.command`), so you do not need to start it by hand —
only the backend needs to already be running. The app command defaults to
`python3`; point it at this repo's own interpreter (which has
`argus[desktop,ocr]` installed) instead:

```bash
export ARGUS_PYTHON=$(pwd)/.venv/bin/python
```

(both files use `${ARGUS_PYTHON:-python3}`, so this is optional if your
default `python3` already resolves to an interpreter that can run
`app.py` — it doesn't need Argus itself installed, only `tkinter` (bundled
with CPython) since the app has no other dependencies.)

With the backend running and `ARGUS_PYTHON` exported:

```bash
.venv/bin/argus --dry-run --config examples/desktop/argus.yaml   # validates config + tests, touches nothing
.venv/bin/argus run --config examples/desktop/argus.yaml
# on a 2x/Retina display, use --config examples/desktop/argus.retina.yaml instead
```

Expected: `Executed: 9`, `Passed: 9`, `Failed: 0`. (On a machine that has
already granted Screen Recording/Accessibility permission — see
"Troubleshooting" below if pre-flight fails instead.)

## What the tests show

`examples/desktop/tests/demo.yaml` defines `DSK-001`..`DSK-009` under the
`Demo` feature, driving the app with `device.tap` at `${var}` coordinates
resolved from whichever config's `variables:` block is active (see
"Measure your display" above) — no coordinate is hardcoded in the test
file itself. The table below gives the widget layout in 1x screenshot
pixels (`argus.yaml`'s values; `argus.retina.yaml` doubles all of them):

| Widget | Position (1x) | Variable(s) | Screen |
| --- | --- | --- | --- |
| Title "Argus Demo" / "Settings" (24pt) | top-left, `(20, 20)` | — (not tapped) | both |
| `Count: N` label | centred at `(400, 180)` | — (not tapped) | home |
| `+` button | centred at `(400, 300)` | `tap_plus_x`/`tap_plus_y` | home |
| `Settings` button | centred at `(400, 400)` | `tap_settings_x`/`tap_settings_y` | home |
| Colour swatch (160×80 canvas) | top-left corner `(600, 60)` | `swatch_pixel_x`/`swatch_pixel_y` reads a point inside it, `(680, 100)` | home |
| `Dark theme` checkbutton | centred at `(400, 250)` | `tap_dark_toggle_x`/`tap_dark_toggle_y` | settings |
| `Back` button | centred at `(400, 400)` | `tap_back_x`/`tap_back_y` | settings |

Tests:

- **DSK-001** — the window launches and `text_present "Argus Demo"` finds
  the title. Tagged `smoke`, `visual`, `ocr`.
- **DSK-002** — `/test/status` reports `ready: true` and `screen: home` at
  startup (instrumentation only, no OCR/pixel check, so no `visual`/`ocr`
  tag).
- **DSK-003** — tapping `+` at `(400, 300)` shows `Count: 1` and
  `application_state.counter == 1`. Tagged `smoke`, `visual`, `ocr`.
- **DSK-004** — tapping `+` three times shows `Count: 3`. Tagged `visual`,
  `ocr`.
- **DSK-005** — tapping `Settings` at `(400, 400)` shows the `Settings`
  title and `application_state.screen == settings`. Tagged `visual`, `ocr`.
- **DSK-006** — incrementing, navigating to Settings, then tapping `Back`
  (also at `(400, 400)`, now on the settings screen) returns to `Count: 1`
  — the counter survives navigation. Tagged `visual`, `ocr`.
- **DSK-007** — toggling `Dark theme` at `(400, 250)` turns the swatch
  purple (`#8e44ad`); the swatch canvas only exists on the Home screen (see
  `app.py`), so the test taps `Back` first, then checks `pixel_matches` at
  `(680, 100)` (inside the swatch) once Home is showing again — pixel-only,
  so tagged `visual` without `ocr`. Teardown issues `device.reset` to
  restore the light theme.
- **DSK-008** — `backend.set {counter: 42}` is picked up by the app's
  500ms backend poll and shown as `Count: 42`. Tagged `visual`, `ocr`.
- **DSK-009** — after incrementing to `1`, resetting the backend and
  issuing `device.reset` relaunches the app fresh at `Count: 0`. Tagged
  `visual`, `ocr`.

Nearly every test ends up tagged `visual`/`ocr` because `text_present` is
this suite's only way to confirm the UI actually reflects a state change
(there is no `log_contains` here — see below); `--tag ocr` is still useful
to exclude this whole suite on a machine without Tesseract installed, and
`--tag smoke` picks the two fast checks (`DSK-001`, `DSK-003`).

Unlike the web example, this suite does not use `log_contains`: per the
task brief for this example, `device.tap` and screenshot/instrumentation
conditions drive and verify everything instead, even though the desktop
adapter does capture the launched process's stdout/stderr as logs (see
`docs/desktop.md`) and `app.py` does print the exact log lines from the
shared spec (`App ready`, `Counter: N`, `Screen: home`, `Screen: settings`,
`Theme: light`, `Theme: dark`) for anyone who wants to watch them by hand.

Because the `desktop` adapter only probes the display on `connect()` and
never auto-launches the application, the suite's `features: Demo:` setup
explicitly issues `device.start`; teardown issues `device.stop` and resets
the backend. Individual tests that mutate counter/theme/screen state
additionally issue `device.reset` in their own `setup:` (which relaunches
the app fresh, since no `reset_command` is configured) so each test passes
independently of run order or `--test`/`--tag` filtering, per "Isolation"
in `docs/test-authoring.md`.

### `region` and the macOS menu bar / title bar

`argus.yaml` sets `region: [0, 0, 800, 600]` (doubled in
`argus.retina.yaml`), matching the window's own `800x600+0+0` geometry. On
macOS, the window's actual content area does not necessarily start at
screen pixel `(0, 0)` even when its geometry says `+0+0` — the global menu
bar and the window's own title bar both take up space above it, and how
much varies by macOS version and window manager, so do not hardcode a
specific offset. If tests fail with plausible-looking screenshots that
seem shifted vertically or horizontally, measure the real offset instead
of guessing: run any test (e.g. `argus run --config examples/desktop/argus.yaml --test DSK-001`),
open a saved screenshot from `results/<run>/DSK-001_desktop/` (or
temporarily set `results: {retain_on_success: false}` to `true` in the
config to keep one from a passing run), and compare where the title/window
content actually falls against `region`'s `[x, y, width, height]`; adjust
`region` (and, if the window is offset rather than just cropped, every
`variables:` coordinate by the same amount) to match what you measured —
see `docs/desktop.md` for how `region` crops the screenshot and offsets
tap coordinates.

## Troubleshooting

- **Pre-flight fails on "Backend API"** — start
  `examples/backend/server.py` first; it must keep running for the whole
  test run.
- **Pre-flight fails on "Device: desktop_app" / "Screenshot: desktop_app"
  with `screencapture ... returned non-zero exit status 1` (macOS)** —
  this is the Screen Recording permission gate. This is the exact error
  observed while verifying this example on a real macOS host without the
  permission granted:

  ```
  Desktop device 'desktop_app': screenshot probe failed (Desktop screenshot
  failed: Command ['screencapture', '-x', '/var/.../tmpXXXX.png'] returned
  non-zero exit status 1.).
  Remediation: Grant your terminal Screen Recording and Accessibility
  permission (System Settings > Privacy & Security), then re-run.
  ```

  Fix: System Settings → Privacy & Security → Screen Recording → enable
  your terminal app (Terminal, iTerm2, VS Code, ...) → **restart the
  terminal** (a re-launch, not just re-running the command — macOS only
  re-checks the grant for a fresh process). Also enable the same app under
  Accessibility, or clicks/keys are silently dropped even once screenshots
  work.
- **Pre-flight fails on "Instrumentation: desktop_app" with `Connection
  refused`** — this is a symptom of the app never having started (usually
  because the Screen Recording/Accessibility gate above stopped it before
  it could launch); fix that first. If instrumentation still fails once
  the app is confirmed running, check nothing else on the machine is bound
  to port 8085.
- **`Application executable not found`** — `ARGUS_PYTHON` isn't exported
  (or points at a missing interpreter) and the default `python3` is not on
  `PATH`; `export ARGUS_PYTHON=$(pwd)/.venv/bin/python` before running.
- **`region ... exceeds the screenshot`** — either the display's real
  resolution is smaller than the active config's `region` implies (e.g. a
  scaled-down VM/CI display; use a screen at least as large as `region`,
  or lower its values), or you used `argus.yaml` (1x, `[0, 0, 800, 600]`)
  on a 2x/Retina display where the raw screenshot is `1600x1200`+ — run
  "Measure your display" above and switch to `argus.retina.yaml` if so.
- **Taps land in the wrong place / pixel_matches never finds the swatch
  colour, but the window and screenshots otherwise look right** — this is
  the classic symptom of using the 1x config (`argus.yaml`) on a 2x/Retina
  display (or vice versa): every coordinate is off by the display's pixel
  ratio. Run "Measure your display" above and use the matching config.
- **OCR-based tests (`text_present`) fail even though the window looks
  right** — check `tesseract --version` runs; without the `ocr` extra and
  the `tesseract` binary installed, `text_present`/`text_not_present`
  conditions error out rather than silently passing.
- **`Address already in use` (backend, port 8765)** — another process is
  already bound to it; stop it, or run the backend with `--port` and
  update `backend.base_url` in `argus.yaml` to match.
