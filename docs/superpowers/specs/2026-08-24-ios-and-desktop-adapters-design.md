# iOS and Desktop Device Adapters — Design

**Date:** 2026-08-24
**Status:** approved in conversation; awaiting spec review

## Goal

Extend Argus to two more application targets, each a first-class `Device`
(`src/argus/adapters/base.py`), unit-tested without hardware, documented, and
covered by `argus validate`:

1. **iOS** (`type: ios`) — "like Android": screenshots, app lifecycle, logs,
   and the full gesture set (`tap`, `swipe`, `long_press`, `drag`,
   `multi_touch`, `pinch`) on simulators **and** physical devices.
2. **Desktop** (`type: desktop`) — native applications on **Windows**,
   **Ubuntu/Linux** and **macOS** through one adapter: launch the app as a
   process, screenshot the display, drive it with mouse and keyboard, capture
   its stdout/stderr as logs.

The two are independent sub-projects; iOS is built first, desktop second,
each with its own implementation plan and pull request.

## Non-goals

- Starting WebDriverAgent, Xcode or the Simulator for the user.
- Simulator-only shortcuts (`idb`, `simctl` touch tricks) — WebDriverAgent
  covers both simulator and device with one code path.
- Desktop window management (focus, move, resize), per-window screenshots,
  or accessibility-tree inspection.
- Multi-touch / pinch on desktop. No portable OS-level touch injection
  exists; the operation raises `DeviceCapabilityError` with a remediation
  (keyboard zoom via `device.key`).
- Any new mandatory dependency. Desktop needs `pyautogui` behind an
  `argus[desktop]` extra; iOS uses only the standard library.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| iOS touch driver | **WebDriverAgent (WDA) over HTTP** | `xcrun simctl` has no touch injection at all; `idb` is simulator-only and single-finger. WDA's W3C Actions endpoint supports true multi-finger sequences, so pinch works, and the same HTTP API serves simulators and physical devices. |
| iOS HTTP client | **`urllib.request`, no new dependency** | A handful of JSON endpoints; `requests` is not worth an extra install for this. |
| iOS coordinates | **Tests use screenshot pixels; adapter converts to WDA points** | Consistent with every other adapter (Android, browser): the coordinate an author reads off a screenshot is the one they type. WDA takes points; the scale factor is read once from `/wda/screen`. |
| iOS logs | **Optional `log_command`, streamed in a thread** | WDA has no log endpoint. On simulators `xcrun simctl spawn <udid> log stream …` works; on devices `idevicesyslog` does. Same pattern as Yocto's `log_command` and `tvos_sim`'s stream thread. No command → `supports_logs=False`. |
| Desktop input/screenshot library | **`pyautogui`** | One cross-platform dependency for screenshots, mouse and keyboard on all three OSes. Alternatives (pywinauto / xdotool / osascript) would mean three backends to build and document. |
| Desktop OS coverage | **One `desktop` type, platform label from the host OS** | Windows, Linux and macOS differ only in permissions/prerequisites, not in the operations. `platform:` defaults to `windows` / `linux` / `macos` so tests can still target one OS with `platforms:`. |
| Desktop app lifecycle | **`subprocess.Popen` of a configured command** | The app under test is a local process; that also gives us stdout/stderr as logs for free. |
| Desktop coordinates | **Tests use screenshot pixels** | On HiDPI displays (macOS Retina, Windows scaling) `pyautogui` reports logical size while screenshots are physical pixels. The adapter measures the ratio once (screenshot size ÷ `pyautogui.size()`) and divides gesture coordinates by it, so authors again type what they see on the screenshot. |

## Shared contracts (both adapters)

- Implement `Device`; register in `register_builtin_devices` (imports stay
  alphabetical: android, appletv, browser, desktop, esp32, fake, ios, roku,
  tvos_sim, yocto).
- `capabilities` reflect configuration honestly (e.g. `supports_logs` only
  with a `log_command`; `supports_multi_touch=False` on desktop).
- Every failure is a remediated exception: connection problems →
  `DeviceConnectionError`, missing config → `ConfigurationError`, bad image
  data → `ScreenshotError`, unsupported operation → `DeviceCapabilityError`.
- Adapter-specific settings live in free-form `DeviceConfig` options; no core
  model changes.
- Gesture semantics match `Device`'s docstrings (drag = press, hold, move;
  pinch = base-class two-finger `multi_touch`).
- Docs: a new page per adapter, a row in `docs/adapters.md`, the supported
  list in `README.md`, `docs/configuration.md` examples, CHANGELOG entry.

---

## 1. iOS adapter (`ios`)

### Prerequisites (documented in `docs/ios.md`)

- macOS with Xcode; a WebDriverAgent build running against the target:
  `xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner
  -destination 'id=<udid>' test` (or the Xcode "Test" button). Physical
  devices additionally need a signing team and the WDA bundle id changed.
- WDA reachable at `url` (default `http://127.0.0.1:8100`; for devices,
  forward the port with `iproxy 8100 8100` or use the device IP).

### Configuration

```yaml
devices:
  iphone:
    type: ios
    platform: ios                  # explicit, like every other adapter doc
    bundle_id: com.example.app     # required
    url: http://127.0.0.1:8100     # optional, WebDriverAgent base URL
    timeout: 30                    # optional, seconds per HTTP call
    log_command: xcrun simctl spawn booted log stream --style compact --predicate 'process == "Example"'
```

`from_config` raises `ConfigurationError` when `bundle_id` is missing.

### Components

**`WdaClient` (Protocol)** — `request(method, path, body=None) -> dict`.
The production implementation (`_HttpWdaClient`) wraps `urllib.request`
with the configured timeout, raises `DeviceConnectionError` on socket/HTTP
errors, and decodes the JSON body. Injected via an `client_factory`
constructor argument so tests provide an in-memory fake.

**`IosAdapter(Device)`** — holds the session id, the point→pixel scale, the
log buffer (`deque(maxlen=5000)`) and the optional log stream thread.

### Operation mapping

| `Device` method | WebDriverAgent call |
| --- | --- |
| `connect()` | `GET /status` (raise remediated error if unreachable) → `POST /session` `{"capabilities": {"alwaysMatch": {"bundleId": <bundle_id>}}}`; store `sessionId`; start log stream if configured |
| `disconnect()` | stop log stream; `DELETE /session/<id>` (errors ignored) |
| `is_available()` | `GET /status` succeeds |
| `health_check()` | `/status` + `wda/apps/state` for the bundle → `HealthCheckResult` with `app_running` |
| `start_application()` | `POST /session/<id>/wda/apps/launch` `{"bundleId": …}` |
| `stop_application()` | `POST /session/<id>/wda/apps/terminate` `{"bundleId": …}` |
| `reset_application()` | terminate then launch (WDA cannot wipe app data; documented) |
| `is_application_running()` | `POST /session/<id>/wda/apps/state` → `value == 4` (running foreground) |
| `screenshot()` | `GET /screenshot` → base64 PNG → RGB `Image`; decode failure → `ScreenshotError` |
| `get_screen_info()` | `GET /session/<id>/window/size` (points) × `GET /session/<id>/wda/screen` `scale` → pixel width/height; cached |
| `get_logs(lines)` | last `lines` entries of the buffer; `DeviceCapabilityError` without `log_command` |
| `tap`, `swipe`, `long_press`, `drag`, `multi_touch` | one `_actions(fingers)` helper (below); `pinch` inherited |
| `press_key(key)` | `HOME` → `POST /session/<id>/wda/homescreen`; `VOLUME_UP` / `VOLUME_DOWN` / `LOCK` → `POST /session/<id>/wda/pressButton`; anything else is typed with `POST /session/<id>/wda/keys` `{"value": [chars]}` (`ENTER` → `"\n"`, `DEL`/`BACKSPACE` → `"\b"`) |

### Gesture engine — `_actions`

All touch input goes through `POST /session/<id>/actions`. Each finger is
one W3C input source:

```json
{"type": "pointer", "id": "finger0", "parameters": {"pointerType": "touch"},
 "actions": [
   {"type": "pointerMove", "duration": 0, "x": 100, "y": 200},
   {"type": "pointerDown", "button": 0},
   {"type": "pause", "duration": 500},
   {"type": "pointerMove", "duration": 250, "x": 150, "y": 200},
   {"type": "pointerUp", "button": 0}
 ]}
```

- Coordinates are divided by the cached scale (pixels → points) and rounded.
- `tap(x, y)`: move, down, up.
- `swipe(...)`: move, down, `pointerMove(duration=duration_ms)` to the end, up.
- `long_press(x, y, ms)`: move, down, `pause(ms)`, up.
- `drag(...)`: move, down, `pause(hold_ms)`, `pointerMove(duration_ms)`, up.
- `multi_touch(fingers, ms)`: one source per finger; each path segment is a
  `pointerMove` with `duration = ms / (len(path) - 1)`; all sources are sent
  in the same request so WDA executes them concurrently.
- After the request, `POST /session/<id>/actions` is followed by
  `DELETE /session/<id>/actions` (releases pointer state), errors ignored.

### Errors

- Transport failure → `DeviceConnectionError` with remediation "Is
  WebDriverAgent running? See docs/ios.md".
- WDA error response (`{"value": {"error": …, "message": …}}`) →
  `DeviceConnectionError` carrying `error` and `message`; session-related
  errors (`invalid session id`) add "reconnect the device".
- Session-less use (`tap` before `connect`) → `DeviceConnectionError`.

### Preflight

`DeviceCheck` already calls `is_available()` / `health_check()`; the new
adapter's messages make the WDA prerequisite explicit. No new check class.

### Tests (`tests/unit/test_ios_adapter.py`)

- Fake `WdaClient` recording `(method, path, body)` and returning canned
  responses (status, session, screen size/scale, screenshot PNG).
- Assertions on the exact request bodies for: session creation, launch /
  terminate / state, screenshot decoding and failure, screen scale
  conversion, each gesture (including a pinch that produces two mirrored
  touch sources), key mapping, log stream capture (with a fake spawner as in
  `test_tvos_sim_adapter.py`), missing `bundle_id`, unreachable WDA, WDA
  error payloads, registry registration, `from_config`.
- `tests/integration/test_ios_live.py`: skipped unless `ARGUS_WDA_URL` and
  `ARGUS_IOS_BUNDLE_ID` are set; connects, screenshots, taps.

---

## 2. Desktop adapter (`desktop`)

### Prerequisites (documented in `docs/desktop.md`)

- `pip install "argus[desktop]"` (`pyautogui`).
- **Linux:** an X11 session (`DISPLAY` set); `sudo apt install scrot
  python3-tk python3-dev` for pyautogui's screenshot/input backends. Wayland
  sessions must run under XWayland or `Xvfb`.
- **macOS:** Screen Recording and Accessibility permission for the terminal
  (System Settings → Privacy & Security).
- **Windows:** nothing extra; run the terminal at the same integrity level as
  the app under test.

### Configuration

```yaml
devices:
  desktop_app:
    type: desktop
    # platform defaults to the host OS: windows | linux | macos
    command: ./build/ExampleApp          # required; executable or script
    args: ["--fullscreen"]               # optional
    cwd: ./build                         # optional
    env: {EXAMPLE_ENV: "1"}              # optional, merged over os.environ
    startup_wait: 2s                     # optional, sleep after launch
    stop_timeout: 5s                     # optional, terminate → kill grace
    reset_command: ./scripts/reset.sh    # optional, run between stop and start
    region: [0, 0, 1920, 1080]           # optional, screenshot crop (pixels)
```

`from_config` raises `ConfigurationError` when `command` is missing;
`platform` when unset is derived from `sys.platform`.

### Components

**`DesktopBackend` (Protocol)** — the slice of `pyautogui` the adapter uses:
`size()`, `screenshot()`, `click(x, y)`, `mouseDown(x, y)`, `mouseUp()`,
`moveTo(x, y, duration)`, `press(key)`, `hotkey(*keys)`. Production
implementation imports `pyautogui` lazily (remediated `DeviceConnectionError`
when missing) and sets `pyautogui.FAILSAFE = False`,
`pyautogui.PAUSE = 0` so gestures are not slowed by the library defaults.
Injected via a `backend_factory` constructor argument for tests.

**`_ProcessHandle`** — wraps `subprocess.Popen` with stdout/stderr merged
into one pipe read by a daemon thread into the shared log deque.

**`DesktopAdapter(Device)`** — owns the backend, the process handle, the
log buffer and the cached pixel ratio.

### Operation mapping

| `Device` method | Implementation |
| --- | --- |
| `connect()` | create backend (import check), `size()` must succeed (a missing display raises `DeviceConnectionError` with the OS-specific remediation) |
| `disconnect()` | `stop_application()` if running |
| `is_available()` | backend importable and `size()` works |
| `health_check()` | screen size + `app_running` |
| `start_application()` | `Popen([command, *args], cwd, env, stdout=PIPE, stderr=STDOUT)`; sleep `startup_wait`; clear logs |
| `stop_application()` | `terminate()`; wait `stop_timeout`; `kill()` if still alive |
| `reset_application()` | stop → `reset_command` (via `subprocess.run`, non-zero exit → `DeviceConnectionError`) → start |
| `is_application_running()` | process exists and `poll() is None` |
| `screenshot()` | `backend.screenshot()` (PIL image) → RGB; cropped to `region` if set; a fully black image on macOS → `ScreenshotError` mentioning Screen Recording permission |
| `get_screen_info()` | screenshot size in pixels (respecting `region`); pixel ratio = screenshot width ÷ `size().width`, cached |
| `get_logs(lines)` | last `lines` of the process output buffer |
| `tap(x, y)` | `click(px(x), px(y))` |
| `swipe(...)` | `mouseDown(from)` → `moveTo(to, duration)` → `mouseUp()` |
| `long_press(x, y, ms)` | `mouseDown` → `time.sleep(ms)` → `mouseUp` |
| `drag(...)` | `mouseDown` → `sleep(hold)` → `moveTo(to, duration)` → `mouseUp` |
| `multi_touch`, `pinch` | `DeviceCapabilityError`: "desktop has no touch injection; zoom with `device.key: Ctrl+Plus` / `Cmd+Plus`" |
| `press_key(key)` | `Ctrl+Plus`-style chords → `hotkey(...)`; Android names mapped (`BACK`→`escape`, `ENTER`→`enter`, `DPAD_*`→arrow keys, `HOME`/`END`/`PAGE_UP`/…); single characters and pyautogui names pass through |

`px()` converts screenshot pixels to logical coordinates (`/ ratio`, plus
the `region` offset) so authors always type screenshot coordinates.

### Capabilities

`supports_screenshot`, `supports_tap`, `supports_swipe`,
`supports_long_press`, `supports_drag`, `supports_keyboard`,
`supports_app_lifecycle`, `supports_logs` = `True`;
`supports_multi_touch` = `False`.

### Preflight

`DeviceCheck` / `DeviceScreenshotCheck` cover reachability and screenshots.
`connect()` messages carry the per-OS remediation (missing `pyautogui`,
no `DISPLAY`, macOS permissions).

### Tests (`tests/unit/test_desktop_adapter.py`)

- Fake backend recording calls, returning a configurable screenshot size and
  logical size (to test the HiDPI ratio, e.g. 2×).
- Process lifecycle with a real child: `sys.executable -c` script that prints
  lines and sleeps; asserts logs captured, `is_application_running`,
  terminate/kill path, `reset_command` failure.
- Every gesture asserts the exact backend call sequence and converted
  coordinates; `multi_touch`/`pinch` raise `DeviceCapabilityError`.
- Key mapping, `region` cropping, missing `command`, missing `pyautogui`
  remediation, platform derivation on each `sys.platform` (monkeypatched),
  registry registration, `from_config`.
- `tests/integration/test_desktop_live.py`: real screenshot + size, skipped
  when `pyautogui` is missing or no display is available.

---

## Documentation

- `docs/ios.md`, `docs/desktop.md` (prerequisites, configuration, operation
  tables, gesture support, troubleshooting).
- `docs/adapters.md`: two rows in the built-in table.
- `README.md`: supported platforms sentence gains **iOS** and **Windows /
  Linux / macOS desktop**; `docs/test-authoring.md` gesture note lists
  where pinch/multi-touch work (Android, iOS, chromium) and where they don't
  (desktop, TV platforms).
- `pyproject.toml`: `desktop = ["pyautogui>=0.9.54"]`, included in `all`.
- `CHANGELOG.md` "Added" entries for both adapters.

## Delivery

Two sequential branches / PRs off `main`, each with its own implementation
plan under `docs/superpowers/plans/`:

1. `feature/ios-adapter`
2. `feature/desktop-adapter`
