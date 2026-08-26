# Roku and Apple TV Device Adapters — Design

**Date:** 2026-08-24
**Status:** approved in conversation; awaiting spec review

## Goal

Let Argus tests drive and verify TV applications on three setups the user has:

1. A **Roku** in developer mode running a **sideloaded** channel.
2. The **tvOS Simulator** (macOS + Xcode) running the user's tvOS app.
3. A **physical Apple TV**, controlled through **pyatv**.

Each becomes a first-class `Device` implementation (`src/argus/adapters/base.py`),
registered by name, unit-tested without hardware, and documented. A new
`now_playing` condition gives the physical Apple TV (which cannot produce
screenshots) something real to assert on.

## Non-goals

- HDMI capture-card screenshots (would serve store apps on real hardware; a
  possible later adapter).
- Store (non-sideloaded) Roku channels — ECP cannot screenshot them.
- DOM/accessibility-tree inspection on any platform.
- Trackpad/touch gestures on tvOS (`tap`/`swipe` stay unsupported).

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| One adapter per setup vs. one Apple TV adapter with a `transport` switch | **Three types: `roku`, `tvos_sim`, `appletv`** | Capabilities differ fundamentally (simulator: screenshots+logs, no key API; physical: keys+playback state, no screenshots). One class would advertise capabilities that flip on a config value. |
| tvOS Simulator key input | **`osascript` keystrokes to the Simulator app** | `xcrun simctl` has no key-press command. AppleScript matches the Simulator's own remote shortcuts. Requires Accessibility permission; the adapter detects failure and raises a remediated error. |
| Physical Apple TV verification | **`now_playing` condition backed by pyatv metadata** | No screenshot/log path exists for a physical Apple TV; playback state (playing/paused, title, app, position) is the observable that matters for a media app. |
| Roku channel install | **Optional `channel_zip` sideload on `connect()`** | Cheap to add on top of the dev-installer auth already needed for screenshots; keeps CI-style runs self-contained. |
| Platform labels | `roku`, `tvos_sim`, `appletv`; every doc example sets `platform:` explicitly | The runner filters on `DeviceConfig.effective_platform` (config `platform` or `type`), not on `Device.platform` — lesson from the browser adapter. |

## Shared contracts (all three adapters)

- Implement `Device`; register in `argus.adapters.registry.register_builtin_devices`
  (imports alphabetical: android, appletv, browser, fake, roku, tvos_sim, yocto).
- Unsupported operations raise `DeviceCapabilityError` via `Device._unsupported`.
- Missing optional tooling (`pyatv`, `xcrun`) → `DeviceConnectionError` with a
  `remediation=` string. Importing `argus` and running the non-integration test
  suite must work with none of the tooling installed.
- Each adapter takes an injectable transport (`request`/`run`/`atv_factory`
  callables) so unit tests run against fakes; one `integration`-marked test per
  adapter runs against real hardware/Simulator and skips with a clear reason when
  the environment variable(s) below are unset.
- Console/log lines are stored oldest-first in a bounded `deque` (5000) and
  `get_logs(lines)` returns the last `lines` joined by `\n` — identical to the
  browser adapter so `log_contains` behaves the same everywhere.
- Android-style key names are accepted everywhere (`KEYCODE_` prefix stripped,
  case-insensitive) and mapped per platform; unknown names pass through unchanged.

## 1. `roku` adapter — `src/argus/adapters/roku.py`

**Dependencies:** stdlib only (`urllib.request` with `HTTPDigestAuthHandler`,
`socket`, `threading`).

**Config (`DeviceConfig.options`):**

| Option | Default | Meaning |
| --- | --- | --- |
| `host` | required | Roku IP/hostname |
| `dev_password` | none | Developer-mode password (`rokudev` user). Required for screenshots and sideloading. |
| `channel_zip` | none | Path to a channel `.zip`; if set, sideloaded on `connect()` via `POST /plugin_install` (`mysubmit=Install`). |
| `ecp_port` | `8060` | External Control Protocol port |
| `debug_port` | `8085` | BrightScript console (telnet) port |
| `timeout` | `10` | seconds per HTTP call |

**Operations:**

| Device method | Implementation |
| --- | --- |
| `connect` | `GET /query/device-info` (validates host); optional sideload; start the log reader thread. |
| `disconnect` | stop the log reader thread, close the socket. |
| `is_available` | always `True` (stdlib). |
| `health_check` | `GET /query/device-info` ok → healthy (details: model, software version, `is-dev`). |
| `start_application` | `POST /launch/dev`; clears the log buffer. |
| `stop_application` | `POST /keypress/Home`. |
| `reset_application` | stop + start. |
| `is_application_running` | `GET /query/active-app` → app id `dev`. |
| `screenshot` | `POST http://host/plugin_inspect` (`mysubmit=Screenshot`, digest auth) then `GET /pkgs/dev.jpg` → RGB `Image`. Without `dev_password`: `supports_screenshot=False` and `_unsupported("screenshot")` with remediation "enable developer mode and set dev_password". |
| `get_screen_info` | from `device-info` `ui-resolution` (e.g. `1080p` → 1920×1080; `720p` → 1280×720). |
| `get_logs` | last N lines from the debug-port reader. |
| `press_key` | `POST /keypress/<Key>`. Map: `DPAD_UP/DOWN/LEFT/RIGHT`→`Up/Down/Left/Right`, `ENTER`/`DPAD_CENTER`→`Select`, `BACK`→`Back`, `HOME`→`Home`, `MEDIA_PLAY_PAUSE`→`Play`, `MEDIA_REWIND`→`Rev`, `MEDIA_FAST_FORWARD`→`Fwd`, `INFO`→`Info`; single characters → `Lit_<urlencoded char>`. |
| `tap`, `swipe` | unsupported. |

**Log reader:** a daemon thread connects to `host:debug_port` on `connect()`,
reads lines, appends to the deque; reconnects with backoff if the socket drops
(Roku resets the console on channel launch). Errors are logged at debug level,
never raised into the test.

**Capabilities:** keyboard, app_lifecycle, logs, instrumentation; screenshot iff
`dev_password` set. Platform label `roku`.

**Tests:** `tests/unit/test_roku_adapter.py` — `pytest-httpserver` fakes ECP and the
dev installer (asserting digest auth is attempted, screenshot bytes decode,
key mapping, launch/active-app flow); a fake TCP server thread emits log lines to
test the reader. Integration test `tests/integration/test_roku_adapter_e2e.py`
skipped unless `ARGUS_ROKU_HOST` (and `ARGUS_ROKU_DEV_PASSWORD` for screenshot
assertions) are set.

## 2. `tvos_sim` adapter — `src/argus/adapters/tvos_sim.py`

**Dependencies:** macOS with Xcode (`xcrun simctl`, `osascript`); no pip extra.

**Config:**

| Option | Default | Meaning |
| --- | --- | --- |
| `bundle_id` | required | App bundle identifier |
| `udid` | `booted` | Simulator UDID or `booted` |
| `app_path` | none | `.app` bundle; installed on `connect()` and on `reset_application` |
| `boot` | `true` | boot the simulator on `connect()` if needed (`simctl boot` + `bootstatus -b`) |
| `process_name` | last dotted component of `bundle_id` | predicate for `log stream` |
| `timeout` | `30` | seconds per command |

**Operations:**

| Device method | Implementation |
| --- | --- |
| `connect` | verify `xcrun simctl list devices -j` contains the target; boot if requested; install if `app_path`; open the Simulator app (`open -a Simulator`) so keystrokes have a target; start `simctl spawn <udid> log stream --style compact --predicate 'process == "<process_name>"'` as a subprocess whose stdout feeds the log deque. |
| `disconnect` | terminate the log subprocess. Does **not** shut the simulator down. |
| `is_available` | `xcrun` on PATH and `xcrun simctl help` exits 0. |
| `health_check` | target device state is `Booted`. |
| `start_application` | `simctl launch <udid> <bundle_id>`; clears logs. |
| `stop_application` | `simctl terminate <udid> <bundle_id>`. |
| `reset_application` | terminate; if `app_path`: `uninstall` + `install`; launch. |
| `is_application_running` | adapter-tracked flag: `True` after a successful `launch`, `False` after `terminate`/`disconnect` (mirrors the browser adapter; `launchctl` probing is unreliable across Xcode versions). |
| `screenshot` | `simctl io <udid> screenshot --type png -` → PNG bytes on stdout → RGB `Image`. |
| `get_screen_info` | from the screenshot size (cached after first capture) or `simctl list devices -j` device type. |
| `get_logs` | last N lines from the log subprocess. |
| `press_key` | `osascript -e 'tell application "Simulator" to activate' -e 'tell application "System Events" to key code <n>'`. Map: `DPAD_*`→arrow key codes, `ENTER`/`DPAD_CENTER`→Return, `BACK`/`MENU`→Escape, `MEDIA_PLAY_PAUSE`→Space, `HOME`→Cmd+Shift+H. A non-zero exit mentioning "not allowed assistive access" → `DeviceConnectionError` with remediation "grant Accessibility permission to your terminal in System Settings". |
| `tap`, `swipe` | unsupported. |

**Capabilities:** screenshot, keyboard, app_lifecycle, logs, instrumentation.
Platform label `tvos_sim`.

**Tests:** `tests/unit/test_tvos_sim_adapter.py` injects a `run` callable that records
`(argv)` and returns canned `(returncode, stdout, stderr)`; asserts command
sequences for connect/launch/reset/screenshot, PNG decoding, key mapping to
`osascript` invocations, and the Accessibility-permission remediation. Log
reader tested with a fake process object exposing `stdout`. Integration test
`tests/integration/test_tvos_sim_adapter_e2e.py` skipped unless
`ARGUS_TVOS_SIM_BUNDLE_ID` is set and a booted tvOS simulator exists.

## 3. `appletv` adapter — `src/argus/adapters/appletv.py`

**Dependencies:** optional extra `appletv = ["pyatv>=0.14"]`; `all` extra updated;
mypy override for `pyatv.*`.

**Config:**

| Option | Default | Meaning |
| --- | --- | --- |
| `host` | one of `host`/`identifier` required | Apple TV address |
| `identifier` | | pyatv device identifier (alternative to `host`) |
| `credentials` | required | mapping `{companion: "...", airplay: "..."}` from `atvremote wizard` |
| `app_id` | required | bundle id to launch (`apps.launch_app`) |
| `timeout` | `10` | seconds per call |

**Threading model:** the adapter owns an `asyncio` event loop running on a daemon
thread, created in `connect()`. Every operation submits a coroutine with
`asyncio.run_coroutine_threadsafe(...).result(timeout)`. `disconnect()` closes the
pyatv connection and stops the loop. One connection per session.

**Operations:**

| Device method | Implementation |
| --- | --- |
| `connect` | `pyatv.scan` (by host/identifier) → `pyatv.connect` with credentials. Missing pyatv → `DeviceConnectionError` (remediation `pip install "argus[appletv]"`); pairing failure → `DeviceConnectionError` (remediation `atvremote wizard`). |
| `is_available` | `import pyatv` succeeds. |
| `health_check` | connected and `power.power_state` is `On`. |
| `start_application` | `apps.launch_app(app_id)`. |
| `stop_application` | `remote_control.home()` (tvOS has no kill API). |
| `reset_application` | home, then launch. |
| `is_application_running` | `metadata.app` identifier equals `app_id`. |
| `press_key` | `remote_control.<method>()`: `DPAD_*`→`up/down/left/right`, `ENTER`/`DPAD_CENTER`→`select`, `BACK`/`MENU`→`menu`, `HOME`→`home`, `MEDIA_PLAY_PAUSE`→`play_pause`, `MEDIA_PLAY`→`play`, `MEDIA_PAUSE`→`pause`, `MEDIA_NEXT`/`MEDIA_PREVIOUS`→`next/previous`, `VOLUME_UP/DOWN`→`volume_up/down`; unknown → `DeviceCapabilityError`. |
| `get_playback_state` | `metadata.playing()` → `PlaybackState`. |
| `screenshot`, `get_logs`, `tap`, `swipe` | unsupported. |

**Capabilities:** keyboard, app_lifecycle, playback_state, instrumentation.
Platform label `appletv`.

**Tests:** `tests/unit/test_appletv_adapter.py` injects `atv_factory` returning a fake
with `remote_control`, `apps`, `metadata`, `power` attributes whose async methods
record calls; covers the loop thread lifecycle (connect/disconnect twice is
safe), key mapping, launch/running detection, playback-state mapping, and the
missing-pyatv remediation. Integration test skipped unless `ARGUS_APPLETV_HOST`
and `ARGUS_APPLETV_CREDENTIALS` (JSON) are set.

## 4. Playback state and the `now_playing` condition

**Model (`src/argus/models/common.py`):**

```python
class PlaybackState(BaseModel):
    state: Literal["playing", "paused", "stopped", "idle", "loading", "seeking"]
    title: str | None = None
    app_id: str | None = None
    position: float | None = None      # seconds
    duration: float | None = None      # seconds
```

**Device hook (`src/argus/adapters/base.py`):** `DeviceCapabilities.supports_playback_state: bool = False`;
`Device.get_playback_state(self) -> PlaybackState` default raises
`self._unsupported("playback state")`. `FakeDevice` gains a settable
`playback_state` attribute and reports the capability, so conditions are
unit-testable.

**Condition `now_playing` (`src/argus/conditions/builtin.py`):**

| Param | Meaning |
| --- | --- |
| `state` | expected state string (optional) |
| `title` | substring match on title, case-insensitive (optional) |
| `app_id` | exact match (optional) |
| `position_advancing` | `true` → sample twice, `interval` (default `1.0`s) apart, require `position` increased |

At least one param is required (else `ConditionError`). `needs_observation` is
`False`; it reads `context.require_device().get_playback_state()` on every
evaluation so it works in `wait_until`. Devices without
`supports_playback_state` → `ConditionError` "does not support playback state".
Result `details` carries the observed `PlaybackState` dict (and both samples when
`position_advancing`). Negation via `not:`.

## 5. Documentation and packaging

- `docs/roku.md`, `docs/tvos.md` (Simulator + physical Apple TV sections), each
  with a config example that sets `platform:`; rows in `docs/adapters.md`, the
  README support sentence and docs table, `docs/getting-started.md` device line,
  `docs/configuration.md` type comment, `now_playing` row and example in
  `docs/test-authoring.md`; CHANGELOG bullets under `[Unreleased]`.
- `pyproject.toml`: `appletv` extra, `all` extra, keywords `roku`, `tvos`, `apple tv`.

## Error handling summary

| Situation | Error |
| --- | --- |
| Roku unreachable / dev installer 401 | `DeviceConnectionError` with remediation (check host / dev password) |
| Screenshot on Roku without `dev_password`, on `appletv` | `DeviceCapabilityError` |
| `xcrun` missing or no such simulator | `DeviceConnectionError` (install Xcode / `xcrun simctl list`) |
| `osascript` denied assistive access | `DeviceConnectionError` (grant Accessibility permission) |
| `pyatv` missing / pairing failed | `DeviceConnectionError` (`pip install "argus[appletv]"` / `atvremote wizard`) |
| `now_playing`/`log_contains` on a device lacking the capability | `ConditionError` |

## Testing strategy

- Unit suites per adapter with injected transports; no network, no Xcode, no pyatv
  needed. All pass with none of the optional tooling installed.
- `now_playing` tests through `FakeDevice.playback_state`, including the
  two-sample `position_advancing` path (interval overridden to ~0 in tests).
- One `integration`-marked e2e test per adapter, gated on environment variables,
  skipping with a clear reason otherwise.
- Gate for every task: no new failures versus the branch baseline (which already
  carries unrelated failures in `test_text_verifiers`, `test_console_reporter`,
  `test_ocr_tesseract`, plus ruff/mypy errors in `runner.py`, `reporting/html.py`,
  `ocr/preprocess.py`, `verifiers/image.py`); ruff/mypy clean on touched files.

## Implementation order

1. `PlaybackState` model + `Device.get_playback_state` hook + `FakeDevice` support
   + `now_playing` condition (small, unblocks the Apple TV adapter).
2. `roku` adapter (stdlib only, most self-contained).
3. `tvos_sim` adapter.
4. `appletv` adapter (+ `pyproject` extra).
5. Documentation and registry/README/CHANGELOG cross-links.

Tasks 2–4 are independent and can be implemented in parallel worktrees.
