# Argus examples

Nine self-contained example projects, one per supported target, all driving
the same tiny demo application ("Argus Demo"). Each example is a folder
under `examples/<target>/` with its own `argus.yaml`, its own `tests/`
directory, and a README with build/run instructions. `tests/unit/test_examples.py`
loads every example's configuration and test suite with the real Argus
loaders as a regression check.

**All commands below are run from the repository root**, e.g.:

```bash
argus run --config examples/backend/argus.yaml
```

not from inside the example's own directory.

## The examples

| Folder | Target | What you need | Run command |
| --- | --- | --- | --- |
| `examples/backend/` | Backend REST API only, no device (`BE-*`) | Python 3 (standard library only) | `python examples/backend/server.py` then `argus run --config examples/backend/argus.yaml` |
| `examples/web/` | Web browser via Playwright (`WEB-*`) | Playwright + a browser install, the demo backend running | `argus run --config examples/web/argus.yaml` |
| `examples/desktop/` | Desktop app via pyautogui (`DSK-*`) | Windows/Linux/macOS desktop session, the demo backend running | `argus run --config examples/desktop/argus.yaml` |
| `examples/android/` | Android via ADB (`AND-*`) | An Android emulator or device, `ANDROID_SERIAL`, the demo backend running | `argus run --config examples/android/argus.yaml` |
| `examples/ios/` | iOS via WebDriverAgent (`IOS-*`) | An iOS simulator/device with WDA running, the demo backend running | `argus run --config examples/ios/argus.yaml` |
| `examples/tv/` | Apple TV via tvOS Simulator / pyatv (`TV-*`) | A tvOS Simulator or real Apple TV, the demo backend running | `argus run --config examples/tv/argus.yaml` |
| `examples/roku/` | Roku in developer mode (`ROKU-*`) | A Roku device with developer mode enabled, `ROKU_HOST`, `ROKU_DEV_PASSWORD` | `argus run --config examples/roku/argus.yaml` |
| `examples/esp32/` | ESP32 serial agent / Wokwi (`ESP-*`) | An ESP32 board (or Wokwi simulation), `ESP32_PORT` | `argus run --config examples/esp32/argus.yaml` |
| `examples/yocto/` | Yocto / embedded Linux over SSH (`YOC-*`) | A Yocto target reachable over SSH, `YOCTO_HOST`, `YOCTO_USER`, `YOCTO_KEY` | `argus run --config examples/yocto/argus.yaml` |

Only `examples/backend/` is implemented so far (this is the first of nine
example projects to land); the rest are planned as separate follow-up tasks
and their folders do not exist yet. `tests/unit/test_examples.py`
automatically picks up each example as soon as its folder and `argus.yaml`
appear, by scanning `examples/*/argus.yaml`.

## The shared demo app

Every UI-bearing example (web, desktop, android, ios, tv, roku, esp32,
yocto) is a test target for the same small "Argus Demo" application, driven
through the shared backend:

- **On screen:** the app title is "Argus Demo" (the ESP32 firmware shows
  "ARGUS" instead, to fit its small display). It shows a counter as
  "Count: N" and has a Settings screen (title "Settings") with a "Dark
  theme" toggle and a "Back" button.
- **Themes:** the light theme uses swatch colour `#2ecc71` (RGB
  46, 204, 113) on a white (`#ffffff`) background with black text; the dark
  theme uses swatch colour `#8e44ad` (RGB 142, 68, 173) on a
  `#1e1e2e` background with white text.
- **Logging:** the app writes exact log lines `App ready`, `Counter: N`,
  `Screen: home`, `Screen: settings`, `Theme: light`, and `Theme: dark` as
  the corresponding events happen, so `log_contains` conditions can assert
  on them.
- **Instrumentation** (port **8085**): `GET /test/status` returns
  `{"application":"ArgusDemo","version":"1.0.0","ready":true,"screen":"home"|"settings","capabilities":["status","state"]}`;
  `GET /test/state` returns `{"counter":N,"theme":"light"|"dark","screen":"home"|"settings"}`;
  `GET /test/health` returns 200 `{"ok":true}`.
- **Backend** (port **8765**, see `examples/backend/`): `GET /health` returns
  200 `{"ok":true}`; `GET /api/state` returns the current state; `POST
  /api/state` merges a JSON object body into the state and returns the full
  state; `POST /api/reset` restores `{"counter":0,"theme":"light"}`, which is
  also the state a fresh backend starts in.
- **Backend/UI sync:** apps that poll the backend (web, desktop) poll `GET
  /api/state` every 500 ms and apply `counter`/`theme` locally, and `POST`
  their own local changes back to `/api/state` so the two stay in sync. If
  the backend becomes unreachable, they keep running standalone rather than
  failing.
- **Test IDs and tags:** every example's tests are numbered `<PREFIX>-001`
  through `<PREFIX>-010` (prefixes per the table above), grouped under the
  feature name `Demo`. `smoke` tags the first and third test in each
  example; `visual` tags OCR/pixel-comparison tests; `ocr` further marks
  tests that specifically require OCR.

## Conventions every example follows

- `argus.yaml` sets `test_paths: [examples/<target>/tests]` and, only when
  the example has an `images/` folder of reference screenshots,
  `asset_paths: [examples/<target>/images]`.
- `results: {retain_on_success: false}` so a clean run leaves no artifacts
  behind for passing tests.
- `action: wait` is never used to "wait for the app to settle" — every
  synchronization point is a `wait_until` on a concrete condition, per
  `docs/test-authoring.md`. (A README calls out any deliberate exception.)
- Each example's own README has: Prerequisites, Build, Run the app, Run the
  tests (the exact command), What the tests show, and Troubleshooting.
