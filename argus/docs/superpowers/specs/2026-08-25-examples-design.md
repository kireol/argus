# Example projects for every Argus target — design

Date: 2026-08-25

## Goal

Ship a top-level `examples/` folder with one small, complete, buildable sample
application per supported target type, plus the Argus configuration and a
suite of 5–10 YAML tests that exercise the running app. New users should be
able to open one folder, build/run the app with the documented commands, and
run `argus run --config examples/<target>/argus.yaml` to see real tests pass.

## Targets

| Folder | Argus device `type` | App toolkit | Build / run |
| --- | --- | --- | --- |
| `examples/backend/` | backend (`type: http`) | Python stdlib `http.server` | `python examples/backend/server.py` |
| `examples/web/` | `browser` (`platform: web`) | vanilla HTML/CSS/JS + Python stdlib server | `python examples/web/server.py` |
| `examples/desktop/` | `desktop` | Python `tkinter` | `python examples/desktop/app.py` |
| `examples/android/` | `android` | Kotlin, Android Views, Gradle (KTS) | `./gradlew installDebug` |
| `examples/ios/` | `ios` | SwiftUI, Xcode project | `xcodebuild` + WebDriverAgent |
| `examples/tvos/` | `tvos_sim` (+ `appletv` note) | SwiftUI for tvOS, Xcode project | `xcodebuild -sdk appletvsimulator` |
| `examples/roku/` | `roku` | BrightScript SceneGraph | `make zip` (zip script) |
| `examples/esp32/` | `esp32` (serial or wokwi) | Arduino / PlatformIO + `ArgusAgent.h` | `pio run`, Wokwi |
| `examples/yocto/` | `yocto` | Python + `pygame` fullscreen, systemd unit, BitBake recipe | `python app.py` on the target |

The existing `agents/esp32/examples/ssd1306_menu/` stays where it is (the
integration test and drift guard depend on it); `examples/esp32/` is a
separate project that `#include`s a copy of `ArgusAgent.h` and is documented
as the *example*, while the agents folder remains the *firmware helper*.

## One app, many ports: the "Argus Demo" spec

Every example implements the same behaviour so tests read in parallel:

**Home screen**
- Title text `Argus Demo` (large, high contrast, OCR-friendly).
- A counter labelled `Count: N`, starting at 0, and a `+` control that
  increments it. Pointer platforms: a button; TV/embedded platforms: `ENTER` /
  `DPAD_CENTER` / `BTN_OK` increments.
- A `Settings` control that navigates to the settings screen (pointer tap or
  `DPAD_RIGHT` then `ENTER`, per platform README).
- A colour swatch — a solid rectangle at a documented position — that is
  **green `#2ecc71`** in light theme and **purple `#8e44ad`** in dark theme, so
  `pixel_matches` can assert the theme without OCR.

**Settings screen**
- Title text `Settings`.
- A `Dark theme` toggle that switches the theme (background + swatch colour).
- A `Back` control (or `BACK`/`MENU` key) returning to Home. Counter value is
  preserved across navigation.

**Observability**
- Each action logs one line: `Counter: N`, `Screen: home|settings`,
  `Theme: light|dark`, plus `App ready` on start — so `log_contains` works on
  every platform that exposes logs (logcat, browser console, Roku console,
  simulator `log stream`, serial).
- Instrumentation (where an HTTP listener is possible — backend, web,
  desktop, Android, iOS sim, tvOS sim, Yocto): `GET /test/status` →
  `{application: "ArgusDemo", version: "1.0.0", ready, screen, capabilities:
  ["status","state"]}`; `GET /test/state` → `{counter, theme, screen}`;
  `GET /test/health` → 200. ESP32 serves the same via the serial agent
  (`instrumentation: {type: device}`). Roku and physical Apple TV have none.
- Backend-driven state (backend, web, desktop examples): the app polls the
  example backend's `GET /api/state` every 500 ms and applies `counter` and
  `theme` from it, so `backend.set` visibly changes the UI. Other platforms
  are self-contained (no backend) to keep the build minimal.

## Per-example layout

```
examples/<target>/
  README.md        # prerequisites, build, run, `argus run` command, expected output
  argus.yaml       # complete config for this example; secrets/paths via ${ENV}
  tests/demo.yaml  # 5–10 tests, feature `Demo`, feature-level setup/teardown
  images/          # reference PNGs used by image_present tests (only where used)
  <source>         # app/, src/, Sources/, or firmware files — buildable as-is
```

`argus.yaml` sets `test_paths: [examples/<target>/tests]` and
`asset_paths: [examples/<target>/images]` so it runs from the repo root with
only `--config`. Where a backend is used it points at
`http://127.0.0.1:8765` (the example backend's port). A top-level
`examples/README.md` indexes the examples, what each needs, and the shared
demo-app spec.

## Tests (each example picks the subset its platform can observe)

| # | Test | Conditions used |
| --- | --- | --- |
| 1 | App starts and title is visible | `text_present "Argus Demo"` (or `log_contains "App ready"` where no screenshots) |
| 2 | Instrumentation reports ready on home | `instrumentation_value ready == true`, `screen == home` |
| 3 | `+` increments the counter | tap/key → `text_present "Count: 1"` + `application_state counter == 1` |
| 4 | Counter increments three times | loop of taps → `Count: 3`, `log_contains "Counter: 3"` |
| 5 | Navigate to Settings | tap/keys → `text_present "Settings"`, `application_state screen == settings` |
| 6 | Back returns home and keeps the counter | → `text_present "Count: 1"` |
| 7 | Dark theme changes the swatch colour | toggle → `pixel_matches` purple; teardown restores light |
| 8 | Backend drives the counter | `backend.set {counter: 42}` → `text_present "Count: 42"` (backend-connected examples) |
| 9 | Reset returns the app to a clean state | `device.reset` → `Count: 0` |
| 10 | Screenshot artifact | `screenshot` step (documentation of the feature) |

Roku (no pointer, screenshots only with `dev_password`) uses keys + logs +
pixel checks. The tvOS example targets `tvos_sim`; a physical Apple TV exposes
neither screenshots nor logs and the demo has no media for `now_playing`, so
the README explains why `appletv` is documented but not exercised. ESP32 uses the agent's framebuffer with
`image_present`/`text_present` on a 128×64 mono display — its title is shorter
(`ARGUS`) and tests use `pixel_matches`/`image_present` with committed
reference crops instead of OCR.

Every suite uses `features: Demo: setup: [device.start / backend.set reset]`
and a teardown that stops the app, and each test's own `setup:` resets state
so order does not matter.

## Verification plan

- `argus validate --framework-only` and `argus --dry-run --config
  examples/<target>/argus.yaml` for every example (configs load, tests parse,
  IDs unique).
- End-to-end on this machine: backend, web (Playwright chromium), desktop
  (tkinter + pyautogui) — real runs with the report attached to the PR.
- Build checks where a toolchain exists: Gradle (Android), `xcodebuild`
  (iOS/tvOS simulators). Roku/ESP32/Yocto: static checks only; their READMEs
  state exactly what hardware/simulator is needed.
- A unit test `tests/unit/test_examples.py` loads every `examples/*/argus.yaml`
  with the real config loader and asserts test IDs are unique and every
  referenced image exists — keeps the examples from rotting.

## Non-goals

- No shared cross-platform UI framework (Flutter/RN); each port is idiomatic
  and minimal.
- No CI jobs that need devices/simulators; only the loader unit test runs in CI.
- No media playback (so no `now_playing` example) — kept out to avoid
  licensing/asset weight; `docs/tvos.md` already shows it.
