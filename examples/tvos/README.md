# Argus Demo — tvOS (Apple TV Simulator)

A minimal SwiftUI app for tvOS plus a nine-test Argus suite that drives it
with remote keys only. It is the reference example for the `tvos_sim`
adapter (see [`docs/tvos.md`](../../docs/tvos.md)).

```
examples/tvos/
  README.md
  argus.yaml                     # device + test paths, run from the repo root
  tests/demo.yaml                # TV-001 … TV-009
  ArgusDemoTV.xcodeproj/         # single app target, shared scheme
  ArgusDemoTV/
    ArgusDemoTVApp.swift         # SwiftUI lifecycle; starts instrumentation (DEBUG)
    ContentView.swift            # focus-driven UI + absolutely-positioned swatch
    DemoModel.swift              # counter / theme / screen, log lines, palette
    InstrumentationServer.swift  # /test/status, /test/state, /test/health on 8085
```

## What the app does

**Home** shows the title `Argus Demo`, a `Count: N` label, and two focusable
controls: `+` (which has default focus) and `Settings` to its right.
`DPAD_RIGHT` moves focus, `ENTER` (Select) activates.

**Settings** shows the title `Settings`, a `Dark theme` toggle (default focus)
and a `Back` control below it. `ENTER` on the toggle switches the theme;
`MENU` (Escape in the Simulator) also returns home. The counter survives
navigation.

A **colour swatch** is drawn in absolute screen coordinates —
**x 1500…1700, y 100…200** on a 1920×1080 simulator — filled green
`#2ecc71` in light theme and purple `#8e44ad` in dark theme, so
`pixel_matches` can assert the theme without OCR. Backgrounds are `#ffffff`
(light) and `#1e1e2e` (dark); text is black on light, white on dark.

Every state change emits one exact line through `os.Logger` **and** `print`:

```
App ready
Counter: N
Screen: home
Screen: settings
Theme: light
Theme: dark
```

`Logger.notice` is used deliberately: `notice` is the *default* log level, and
the adapter streams with `log stream --style compact --predicate 'process ==
"ArgusDemoTV"'` without `--level info`, so `info`-level messages would never
reach `log_contains`.

`InstrumentationServer` is wrapped in `#if DEBUG`, so a release build ships
no listener. In debug builds it serves, on port **8085**:

| Endpoint | Body |
| --- | --- |
| `GET /test/status` | `{"application":"ArgusDemo","version":"1.0.0","ready":true,"screen":"home"\|"settings","capabilities":["status","state"]}` |
| `GET /test/state` | `{"counter":N,"theme":"light"\|"dark","screen":"home"\|"settings"}` |
| `GET /test/health` | `200 {"ok":true}` |

The simulator shares the Mac's network stack, so the host reaches it at
`http://127.0.0.1:8085`. This example has no backend — the app is
self-contained and state changes come from key presses only.

## Prerequisites

- macOS with **full Xcode** (not just the Command Line Tools) and a tvOS
  simulator runtime. Check with `xcrun simctl list devices` — if that fails
  with `unable to find utility "simctl"`, run
  `sudo xcode-select -s /Applications/Xcode.app`. The project targets
  **tvOS 17**, so it needs Xcode 15 or newer; lower
  `TVOS_DEPLOYMENT_TARGET` in the project if you are on an older SDK.
- A **1080p** tvOS simulator, i.e. the plain **Apple TV** device, not
  *Apple TV 4K*. The 4K simulator renders at 2× and `simctl io screenshot`
  returns 3840×2160 pixels, which would move the swatch to x 3000…3400 and
  break the pixel assertions. Create one if you have none:
  ```bash
  xcrun simctl create "Argus Apple TV" \
    com.apple.CoreSimulator.SimDeviceType.Apple-TV-1080p \
    "$(xcrun simctl list runtimes | awk '/tvOS/ {print $NF; exit}')"
  ```
- **Accessibility permission** for your terminal (System Settings → Privacy &
  Security → Accessibility). The adapter has no remote API: it sends keys to
  the frontmost Simulator window via `osascript`. Without this every
  `device.key` step fails with "osascript was denied Accessibility access".
- Argus with OCR support: the `tesseract` binary on `PATH`
  (`brew install tesseract`). Every test except TV-002 and TV-007 uses
  `text_present`.

## Build

From the repository root:

```bash
xcodebuild -project examples/tvos/ArgusDemoTV.xcodeproj \
  -scheme ArgusDemoTV \
  -sdk appletvsimulator \
  -destination 'generic/platform=tvOS Simulator' \
  -derivedDataPath examples/tvos/build \
  CODE_SIGNING_ALLOWED=NO \
  build
```

> **Heads-up:** `ArgusDemoTV.xcodeproj/project.pbxproj` was written by hand and
> has not yet been compiled against a real tvOS SDK — the machine this example
> was authored on had only the Command Line Tools installed. If the build
> fails, the first settings to check are `TVOS_DEPLOYMENT_TARGET` (17.0) and
> `objectVersion` (56); the Swift sources themselves type-check cleanly.

That produces
`examples/tvos/build/Build/Products/Debug-appletvsimulator/ArgusDemoTV.app`,
which is exactly the default `app_path` in `argus.yaml`. Build somewhere else
and point Argus at it with `ARGUS_TVOS_APP=/path/to/ArgusDemoTV.app`.

`build/` and `xcuserdata/` are git-ignored.

## Run the app

Argus installs and launches the app itself (`app_path` + `boot: true`), so you
normally do not need to. To drive it by hand:

```bash
xcrun simctl boot "Argus Apple TV"       # or any 1080p Apple TV simulator
open -a Simulator
xcrun simctl install booted examples/tvos/build/Build/Products/Debug-appletvsimulator/ArgusDemoTV.app
xcrun simctl launch --console booted com.argus.demo.tv
curl -s http://127.0.0.1:8085/test/state
```

Use the arrow keys and Return in the Simulator window; Escape is the Menu
button.

## Run the tests

From the repository root:

```bash
argus run --config examples/tvos/argus.yaml
```

Useful variants:

```bash
argus --dry-run --config examples/tvos/argus.yaml   # validate config + tests, run nothing
argus run --config examples/tvos/argus.yaml --tag smoke
argus run --config examples/tvos/argus.yaml --test TV-007
```

## What the tests show

| ID | Test | Demonstrates |
| --- | --- | --- |
| TV-001 | App starts and the title is visible | `log_contains "App ready"` + OCR `text_present` on a simulator screenshot |
| TV-002 | Instrumentation reports ready on the home screen | `instrumentation_value` against `/test/status` and `application_state` against `/test/state` |
| TV-003 | ENTER increments the counter | default focus on `+`; internal state and pixels asserted together |
| TV-004 | Counter increments three times | a key/`wait_until` loop and `log_contains "Counter: 3"` |
| TV-005 | DPAD_RIGHT then ENTER opens Settings | focus movement between siblings; `Screen: settings` in the log stream |
| TV-006 | MENU returns home and keeps the counter | `onExitCommand` handling the remote's Menu button; state survives navigation |
| TV-007 | Dark theme turns the swatch purple | `pixel_matches` at (1600, 150) — a theme assertion that needs no OCR |
| TV-008 | Reset returns the app to a clean state | `device.reset` reinstalls + relaunches via `simctl` |
| TV-009 | Screenshot artifact | the `screenshot` action, saved into the test's artifacts |

Every test resets the app in its own `setup:` and then waits for
`instrumentation_value ready == true`, so the suite is order-independent and
contains no fixed `wait` steps. The feature-level `setup:`/`teardown:` in
`tests/demo.yaml` launches the app once and terminates it at the end.

## Why the physical Apple TV (`appletv`) is not exercised here

Argus supports two Apple TV targets. This example only uses `tvos_sim`,
deliberately:

- A physical Apple TV exposes **no screenshot API and no log API** over pyatv,
  so `text_present`, `pixel_matches`, `image_present` and `log_contains` all
  raise a capability error there. Seven of these nine tests would be
  impossible.
- Its remaining verification surface is `now_playing` (playback state, title,
  position advancing), and this demo deliberately ships **no media** — no
  bundled video, no licensing or asset weight. `docs/tvos.md` already shows a
  worked `now_playing` example.
- A third-party app cannot be side-loaded onto a retail Apple TV without a
  provisioning profile and a paired developer device, so `device.reset` (the
  install/uninstall cycle every test here relies on) has no equivalent.

If you want to point this app at a real Apple TV, deploy it from Xcode, add an
`appletv` device to `argus.yaml`, and write tests that assert through
`now_playing` or a backend rather than through screenshots and logs.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `xcrun: error: unable to find utility "simctl"` | Only the Command Line Tools are active. `sudo xcode-select -s /Applications/Xcode.app` |
| `no booted tvOS simulator found` | Nothing is running and `udid: booted` cannot pick a device. Boot one (`xcrun simctl boot <udid>`) or set `devices.sim.udid` to a UDID from `xcrun simctl list devices` so Argus can boot it itself. |
| `osascript was denied Accessibility access to the Simulator` | Grant your terminal Accessibility permission, then restart the terminal. |
| Key presses land in the wrong window | Keys go to the frontmost Simulator window. Run one simulator at a time and do not click away during a run. |
| `Instrumentation unreachable: Connection refused` | The app is not running, or you built Release. The listener is `#if DEBUG` — build with the `Debug` configuration (the shared scheme's default). |
| Pixel assertions fail with the swatch far off | You are on an *Apple TV 4K* simulator (3840×2160 screenshots). Use a 1080p **Apple TV** device. |
| `text_present` never matches | `tesseract` is missing (`brew install tesseract`), or the screenshot is dark-on-dark — check the artifact saved by TV-009. |
| `Application not installed` on reset | `app_path` points at a stale or missing bundle. Rebuild, or set `ARGUS_TVOS_APP`. |
| Argus leaves a simulator running | By design — the adapter never shuts a simulator down. `xcrun simctl shutdown all` when you are finished. |
