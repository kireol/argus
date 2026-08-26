# Argus examples

Nine self-contained example projects, one per supported Argus target, all
driving the same tiny demo application, **Argus Demo**. Each example is a
folder under `examples/<target>/` with its own `argus.yaml`, its own
`tests/` directory, and a README with Prerequisites / Build / Run the app /
Run the tests / What the tests show / Troubleshooting sections.
`tests/unit/test_examples.py` loads every example's configuration and test
suite (including ESP32's second config, `argus.wokwi.yaml`) with the real
Argus loaders as a regression check, so the examples don't rot silently.

**All commands below are run from the repository root**, e.g.:

```bash
argus run --config examples/backend/argus.yaml
```

not from inside the example's own directory.

## The examples

| Folder | Argus device type | What you need | Run command |
| --- | --- | --- | --- |
| `examples/backend/` | none (`type: http`, backend only) | Python 3 (standard library only) | `python examples/backend/server.py`, then `argus run --config examples/backend/argus.yaml` |
| `examples/web/` | `browser` (`platform: web`) | Playwright + a browser install (`uv pip install -e ".[browser,ocr]"`, `.venv/bin/playwright install chromium`), the demo backend running | `python examples/web/server.py`, then `argus run --config examples/web/argus.yaml` |
| `examples/desktop/` | `desktop` | A desktop session (macOS/Linux/Windows), `argus[desktop,ocr]`, Tesseract on `PATH`, the demo backend running; macOS needs Screen Recording + Accessibility permission granted to the terminal | `argus run --config examples/desktop/argus.yaml` (launches `app.py` itself) |
| `examples/android/` | `android` | Android SDK (`ANDROID_HOME`, `adb`), a running emulator or device (API 26+), `ANDROID_SERIAL`, JDK 17+ and `gradle` to build | `./gradlew assembleDebug` + `adb install`, then `argus run --config examples/android/argus.yaml` |
| `examples/ios/` | `ios` | macOS with full Xcode 15+ (not just Command Line Tools), an iOS 16+ simulator, WebDriverAgent running on `:8100`, `tesseract` | `xcodebuild ... build` + `xcrun simctl install/launch`, then `argus run --config examples/ios/argus.yaml` |
| `examples/tvos/` | `tvos_sim` | macOS with full Xcode 15+, a 1080p Apple TV simulator (not Apple TV 4K), Accessibility permission for the terminal, `tesseract` | `xcodebuild -sdk appletvsimulator ...`, then `argus run --config examples/tvos/argus.yaml` |
| `examples/roku/` | `roku` | A Roku device in developer mode, `ROKU_HOST` + `ROKU_DEV_PASSWORD`, `zip` on `PATH` | `make zip` (in `examples/roku/`), then `argus run --config examples/roku/argus.yaml` |
| `examples/esp32/` | `esp32` (serial or Wokwi) | PlatformIO (`pio`) to build; a real ESP32 + SSD1306 OLED and `ESP32_PORT`, **or** `wokwi-cli` + `WOKWI_CLI_TOKEN` for simulation | `pio run` (in `examples/esp32/`), then `argus run --config examples/esp32/argus.yaml` (hardware) or `examples/esp32/argus.wokwi.yaml` (Wokwi) |
| `examples/yocto/` | `yocto` | A Yocto/embedded Linux target reachable over SSH with `pygame` installed, `YOCTO_HOST` + `YOCTO_USER` + `YOCTO_KEY`, `argus[yocto]` (paramiko) on the host | `bitbake argus-demo` + boot the image, then `argus run --config examples/yocto/argus.yaml` |

Backend, web, and desktop are the three examples that talk to the shared
example backend (`examples/backend/server.py`, port 8765); the rest
(android, ios, tvos, roku, esp32, yocto) are self-contained apps with no
backend dependency, to keep each build minimal. See each example's own
README for exact commands, tap/key coordinates, and troubleshooting.

## The shared "Argus Demo" spec

Every example implements the same tiny application so its tests read the
same way across platforms:

**Home screen**
- Title text `Argus Demo` (large, high contrast, OCR-friendly). The ESP32
  firmware shows the shorter `ARGUS` instead, to fit its 128x64 display.
- A counter labelled `Count: N`, starting at 0, and a `+` control that
  increments it — a button on pointer platforms; `ENTER` / `DPAD_CENTER` /
  `BTN_OK` on TV/embedded platforms.
- A `Settings` control that navigates to the settings screen (a tap, or
  `DPAD_RIGHT` then `ENTER`, per platform).
- A colour swatch — a solid rectangle at a documented position — that is
  **green `#2ecc71`** (RGB 46, 204, 113) in light theme and **purple
  `#8e44ad`** (RGB 142, 68, 173) in dark theme, so `pixel_matches` can assert
  the theme without OCR.

**Settings screen**
- Title text `Settings`.
- A `Dark theme` toggle that switches the theme (background + swatch
  colour). Light background `#ffffff`, dark background `#1e1e2e`; text is
  black on light, white on dark.
- A `Back` control (or `BACK`/`MENU` key) returning to Home. The counter
  value is preserved across navigation.

**Observability**
- Each action logs one exact line: `App ready` on start, then `Counter: N`,
  `Screen: home`, `Screen: settings`, `Theme: light`, `Theme: dark` as the
  corresponding events happen — so `log_contains` works on every platform
  that exposes logs (logcat, browser console, Roku debug console, simulator
  `log stream`, serial).
- Instrumentation, where an HTTP listener is possible (backend, web,
  desktop, Android, iOS sim, tvOS sim, Yocto), on port **8085**:
  - `GET /test/status` → `{"application":"ArgusDemo","version":"1.0.0","ready":true,"screen":"home"|"settings","capabilities":["status","state"]}`
  - `GET /test/state` → `{"counter":N,"theme":"light"|"dark","screen":"home"|"settings"}`
  - `GET /test/health` → 200 `{"ok":true}`

  ESP32 serves the same contract over the serial agent
  (`instrumentation: {type: device}`) instead of HTTP. Roku and a physical
  Apple TV have no instrumentation at all.
- Backend-driven state (backend, web, desktop only): the app polls the
  example backend's `GET /api/state` every 500 ms and applies `counter` and
  `theme` from it, so `backend.set` visibly changes the UI; it also `POST`s
  its own local changes back to `/api/state` so backend and UI stay in
  sync. If the backend is unreachable, the app keeps running standalone.

**The example backend** (`examples/backend/server.py`, port **8765**):
`GET /health` → 200 `{"ok":true}`; `GET /api/state` → the current state;
`POST /api/state` with a JSON body merges the given keys into the state and
returns the full state; `POST /api/reset` → `{"counter":0,"theme":"light"}`,
which is also the state a fresh backend starts in.

**Test IDs and tags:** every example's tests are numbered `<PREFIX>-001`
through `<PREFIX>-010` (prefixes `BE`, `WEB`, `DSK`, `AND`, `IOS`, `TV`,
`ROKU`, `ESP`, `YOC`), grouped under the feature name `Demo`. `smoke` tags
the first and third test in each suite; `visual` tags OCR/pixel-comparison
tests; `ocr` further marks tests that specifically require OCR.

## Conventions every example follows

- `argus.yaml` sets `test_paths: [examples/<target>/tests]` and, only when
  the example has an `images/` folder of reference screenshots,
  `asset_paths: [examples/<target>/images]`.
- `results: {retain_on_success: false}` so a clean run leaves no artifacts
  behind for passing tests.
- `action: wait` is never used to "wait for the app to settle" — every
  synchronization point is a `wait_until` on a concrete condition, per
  `docs/test-authoring.md`. (`examples/yocto/README.md` documents the one
  deliberate exception, a fixed 2s wait right after a device reset, and
  why `wait_until` can't be used there.)
- Each example's own README has: Prerequisites, Build, Run the app, Run the
  tests (the exact command), What the tests show, and Troubleshooting.

## Verification status

These examples were verified at different levels, depending on what
hardware/toolchains were available in the environment they were built in:

- **backend** and **web**: run end-to-end on macOS (`argus run`), with a
  full HTML report — all tests pass for real.
- **desktop**: verified by hand rather than in an automated/unattended
  terminal — macOS's Screen Recording permission gate blocks
  `pyautogui.screenshot()` for terminals that aren't interactively granted
  access, which an unattended agent session cannot grant itself. The
  example's own README documents the exact pre-flight error this produces
  and how to fix it.
- **android, ios, tvos, roku, esp32, yocto**: no Android SDK/emulator,
  Xcode, Roku device, ESP32/Wokwi toolchain, or Yocto target was available
  in this environment, so these were validated with
  `argus --dry-run --config examples/<target>/argus.yaml` (configuration
  and test-suite loading, asset/ID checks) plus static/type checks
  (`ruff`, and for the compiled platforms, an `xcodebuild`/`gradle`/`pio`
  invocation was attempted where possible) — not a real device/simulator
  run. Each of those examples' READMEs says so explicitly and explains
  what to check if the real build/run doesn't behave as documented.
