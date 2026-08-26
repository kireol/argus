# Android `getevent` Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Observe real touches/keys on an Android device through `adb shell getevent` and turn them into semantic Argus steps using the existing recorder → session → normalizer → steps pipeline.

**Architecture:** A new `adapters/android` package splits the work into an ADB boundary (`AdbClient` + streaming `AdbProcess`), a pure textual `GetEventParser`, a multi-touch `TouchState` machine, an `AndroidCoordinateMapper`, and an `AndroidGestureRecognizer` that emits `RecognizedGesture`s. The existing `AndroidRecorder` consumes those gestures and pushes *semantic* `RecordingEvent`s (new generic `GESTURE` event type) into the existing `EventSink`; the normalizer maps them 1:1 to `NormalizedAction`s (incl. a new `MULTI_TOUCH` kind → `device.multi_touch`). A `FakeAdbClient` plus fixture files make everything testable without hardware.

**Tech Stack:** Python 3.12, pydantic v2, subprocess/threading, PySide6 (UI), pytest.

**Spec:** `argus-test-creator-android-getevent-prompt.md` (repo root).

## Global Constraints

- Never `shell=True`; serial/paths are always argv items; every ADB call uses `-s SERIAL`.
- Never hardcode `/dev/input/eventN`; never silently pick the first of several devices.
- Raw events never reach the UI or the YAML; UI updates throttled (≥100 ms).
- No `subprocess.run()` for the live stream; getevent is a `Popen` stream with clean shutdown.
- Raw events are logged at DEBUG only; structured event names `android.*`.
- Keep the existing suite green after every task (`.venv/bin/python -m pytest`, `ruff`, `mypy`).
- No real device is available on the development machine — real-device checks are manual (documented).

---

## File map

| File | Responsibility |
|---|---|
| `adapters/android/models.py` | `AndroidDevice`, `AxisRange`, `AndroidInputDevice`, `EventType`, `AndroidRawInputEvent`, `RecognizedGesture` + `Tap/Swipe/LongPress/MultiTouch/KeyPress/UnknownGesture`, `TouchPoint` |
| `adapters/android/adb.py` | `AdbClient` protocol, `AdbProcess` (streaming Popen), `SubprocessAdbClient` (real), `parse_devices_output`, `parse_wm_size`, `parse_rotation` |
| `adapters/android/getevent_parser.py` | `GetEventParser.parse_line()` (stream) + `parse_input_devices()` (`getevent -lp` listing) |
| `adapters/android/touch_state.py` | `TouchSlot`, `TouchState.feed()` → `TouchFrame` on `SYN_REPORT` |
| `adapters/android/coordinates.py` | `AndroidCoordinateMapper.map(raw_x, raw_y) -> Point` (ranges, inversion, rotation) |
| `adapters/android/keys.py` | `linux_key_to_argus(name) -> str \| None`, `KEY_MAP` |
| `adapters/android/gestures.py` | `GestureConfig`, `AndroidGestureRecognizer.feed(raw_event) -> list[RecognizedGesture]`, `flush()` |
| `adapters/android/diagnostics.py` | `AndroidRecordingDiagnostics` (thread-safe counters + snapshot) |
| `adapters/android/fake_adb.py` | `FakeAdbClient` (fixtures, scripted device lists, disconnect simulation) |
| `adapters/android/recorder.py` | `AndroidRecorder` rewired: discovery, getevent thread, gestures → `RecordingEvent`s, reconnect |
| `models/recording.py` | `RecordingEventType.GESTURE`, `CONNECTION_LOST`, `CONNECTION_RESTORED`; `NormalizedActionKind.MULTI_TOUCH` |
| `models/capabilities.py` | `supports_hardware_keys` |
| `recording/normalizer.py` | GESTURE → action (tap/swipe/long_press/multi_touch), keeps other rules |
| `recording/steps.py` | MULTI_TOUCH → `device.multi_touch` |
| `recording/session.py` | GESTURE is a gesture end (after-capture); `TargetLost`/`TargetRestored` events, auto-pause on CONNECTION_LOST |
| `cli/doctor.py` | Android section: adb, devices, selected device, version, input devices, touchscreen, getevent, screenshot |
| `ui/widgets/android_panel.py` | `AndroidPanel` (device selector, status, input device, resolution; recording counters, 250 ms throttle) + `AndroidDiagnosticsDialog` |
| `ui/main_window.py` | Show panel for android targets; disconnect prompt (Reconnect / Stop); Target → Android diagnostics… |
| `targets/catalog.py` | Android profile settings/description |
| `tests/fixtures/android/*.txt` | tap, swipe, long_press, multitouch, key, malformed, devices listing |
| `tests/unit/test_android_*.py` | parser, touch state, coordinates, keys, gestures, adb client/process, recorder |
| `tests/integration/test_android_recording.py` | FakeAdb → session → steps → YAML; multi-device; disconnect/reconnect |
| `tests/performance/test_android_performance.py` | synthetic multi-minute stream throughput/memory |
| `docs/android-recording.md`, `docs/recording.md`, `README.md`, `CHANGELOG.md` | guide + limitations |

## Tasks (each: failing tests → implement → suite green → commit)

1. **Models + parser** — `models.py`, `getevent_parser.py`, fixtures; tests for `-lt` lines (hex + named values, `ffffffff` → -1, timestamps), malformed lines → `None` + counter, unknown codes preserved, `-lp` listing → `AndroidInputDevice` with axis ranges and `is_touchscreen`.
2. **Touch state** — slot protocol (A/B), `TRACKING_ID` start/end, `SYN_REPORT` frame boundary, non-slot (`ABS_X/ABS_Y` + `BTN_TOUCH`) fallback.
3. **Coordinate mapper** — scaling, inversion, rotation 0/90/180/270, degenerate ranges.
4. **Keys** — Linux `KEY_BACK` → `BACK` etc.; unknown → `KEY_<NAME>` passthrough (never dropped).
5. **Gesture recognizer** — tap/long-press/swipe/multi-touch/key, configurable `GestureConfig`; no false taps on multi-touch; flush on stop.
6. **ADB layer** — `AdbClient` protocol, `SubprocessAdbClient` (argv only), `AdbProcess` streaming with stop/kill/timeout/stderr, `FakeAdbClient`.
7. **Model/normalizer/steps changes** — `GESTURE`, `MULTI_TOUCH`, `supports_hardware_keys`; existing tests untouched.
8. **AndroidRecorder** — discovery (`list_devices`, explicit selection), device info, touchscreen discovery, getevent thread, gestures → events, diagnostics, connection loss + reconnect, no orphan processes.
9. **Session integration** — after-capture for GESTURE, `TargetLost`/`TargetRestored`, auto-pause.
10. **Doctor** — Android diagnostics with remediation.
11. **UI** — `AndroidPanel`, diagnostics dialog, disconnect prompt.
12. **Integration + performance tests**.
13. **Docs** — guide, limitations, README/CHANGELOG.
14. **Full suite + ruff + mypy.**
