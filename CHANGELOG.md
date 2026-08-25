# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `ios` device adapter: iOS apps on simulators and physical devices through
  WebDriverAgent — screenshots, launch/terminate, W3C-Actions gestures (tap,
  swipe, long press, drag, multi-touch, pinch), key input, and optional
  `log_command` logs. Platform label `ios`.
- Touch gestures: `device.long_press`, `device.drag` (press, hold, then
  move), `device.pinch` and `device.multi_touch` actions, with matching
  `Device` methods and `supports_long_press` / `supports_drag` /
  `supports_multi_touch` capability flags. Android implements pinch and
  multi-touch with evdev `sendevent` streams (touchscreen auto-detected via
  `getevent -p`, overridable with `input_device`), drag with
  `input draganddrop` on API 30+; the browser adapter maps long press and
  drag to the mouse and multi-touch/pinch to CDP touch events (chromium).
- `results.save_comparison_images` and CLI `--save-comparisons`: save
  actual/expected/diff for image verifies (pass or fail) and retain them for
  the HTML report.
- `log_contains` condition: assert that recent device logs (Android logcat,
  Yocto `log_command`, browser console) contain a substring or regex; usable
  in `wait_until` and `verify`, negatable with `not:`.
- `browser` device adapter (Playwright, optional `argus[browser]` extra):
  screenshots, click/drag/keyboard input, `about:blank`-based app lifecycle,
  and browser console captured as device logs. Platform label `web`.
- `now_playing` condition and `Device.get_playback_state()` hook: assert on a
  device's media playback state (state, title, app id, position advancing);
  usable in `wait_until`, negatable with `not:`.
- `roku` device adapter: ECP remote/launch control, developer-installer
  screenshots and sideloading, BrightScript console captured as device logs.
- `tvos_sim` device adapter: tvOS Simulator via `xcrun simctl` (screenshots,
  launch/terminate, `log stream`) with remote keys sent through `osascript`.
- `appletv` device adapter (pyatv, optional `argus[appletv]` extra): remote
  keys, app launch, and now-playing state for `now_playing` assertions.
- `esp32` device adapter (optional `argus[esp32]` extra): serial or Wokwi
  transport, framebuffer screenshots (mono/gray/RGB565/RGB888) via the shipped
  Arduino/MicroPython Argus agent, key input, DTR/RTS reset, optional esptool
  flashing, and agent status/state as `instrumentation: {type: device}`.

### Changed
- CLI help groups filter/failure flags under a **Run options** panel on both
  `argus --help` and `argus run --help` (same flags work before or after `run`).
- HTML `report.html` groups tests by feature, supports pass/fail/skip filters,
  and embeds artifact images (`actual.png`, `expected.png`, `diff.png`, and
  any other screenshots) with relative paths. The CLI prints the report path
  after each run.
- Faster visual waits by default: `wait.default_poll_interval` is `500ms`;
  multiscale matching tries scale `1.0` first and stops at threshold;
  `wait_until` skips per-poll screen-info adb calls; `verify` reuses a
  matching preceding `wait_until` result (`wait.reuse_wait_result_on_verify`).
  Android/Yocto cache `get_screen_info()` after the first probe.

## [0.1.0] — 2026-08-12

Initial release.

### Added
- Declarative YAML test definitions validated by Pydantic (unique IDs, fail-fast).
- Test engine with setup/steps/teardown lifecycle, guaranteed teardown,
  central failure policy (stop-on-failure default, `--continue-on-failure`,
  `--max-failures`), and explicit opt-in retries by failure category.
- Filtering by test ID, feature, tag, platform, and boolean tag expressions.
- Condition system (`image_present`, `image_not_present`, `text_present`,
  `text_not_present`, `screenshot_matches`, `pixel_matches`,
  `instrumentation_value`, `application_state`, `backend_value`) with
  `all`/`any`/`not` composition.
- `wait_until` condition polling with timeout and poll interval — no sleeps.
- OpenCV visual verification: template matching with threshold, region of
  interest, named regions, scale tolerance, grayscale mode; screenshot
  comparison with tolerance; cached reference images.
- Optional OCR subsystem (Tesseract provider, pluggable).
- Device abstraction with discoverable capabilities; Android adapter (ADB),
  Yocto adapter (paramiko SSH, configurable screenshot providers), and fake
  adapters for hardware-free development.
- Generic HTTP backend adapter (httpx) with auth, retries, pooling, TLS.
- HTTP application instrumentation protocol with capability discovery.
- Modular pre-flight system; failing required checks block test execution.
- `argus` CLI: `run`, `validate` (`--framework-only`), `list`, `init`,
  `version`, `update`, and `--dry-run`.
- Artifacts per test (actual/expected/diff images, logs, instrumentation
  state, metadata) with success-retention policy.
- Console, JSON, JUnit XML, and HTML reporters driven by an event bus
  (ready for a future GUI).
- Structured logging with secret redaction.
- Installers (`install.sh`, `install.ps1`) with uv support and
  post-install health check.
- Framework self-test suite (unit + integration, 150+ tests) and example
  test suite that runs against fake adapters.
