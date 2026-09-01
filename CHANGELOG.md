# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.2.7] - 2026-09-01

### Added
- **`skip:` on tests and features.** `skip: true` or `skip: <reason>` in a
  test definition (or in a `features:` entry, which covers every test of
  that feature) reports the test as **skipped** with the reason — on the
  console, in `report.html` / `report.json` / `junit.xml`, in `argus list`
  and in the MCP `argus_list_tests` summary — without running it. Skipped
  tests trigger no feature/suite setup and no device pre-flight.

## [1.2.6] - 2026-08-27

### Added
- **In-run app metrics.** While each test runs, Argus samples the app under
  test (FPS, jank, RSS / CPU / threads, system/app uptime, system load,
  available memory).
  Reports show **min, max, average, and median** for every metric, plus the
  chronological samples next to that test in `report.html`, `report.json`,
  `junit.xml`, and `metrics.json`. Android **FPS** is the display refresh
  rate from `dumpsys SurfaceFlinger --latency` (~60 Hz). Unique HWUI/Compose
  submits are **App FPS** — an idle app often only invalidates ~2
  times/s, which previously showed up as FPS ≈ 2. Disable or slow sampling
  with `metrics.enabled` / `metrics.interval`. Console metric lines are
  omitted when `--no-logs` or `--quiet` is set; HTML / JSON / JUnit still
  include them.

### Fixed
- **Oversized reference images no longer fail before matching.** If a
  golden is larger than the search region (e.g. 96×112 turn-signal PNG in
  an 80×80 crop), Argus downscales it to the largest size that still fits.
  Tests do not need `scale_tolerance` for that case. Matching still errors
  when even a 16px template cannot fit the region.
- **On-screen icons smaller than the golden.** After native scale,
  `image_present` also tries shrinking the template down to 16px so a
  96×112 Figma master still matches a ~22px on-screen instance. Native hits
  still exit after one `matchTemplate` call. `image_not_present` does
  **not** auto-shrink: a 140px Park `P` at 28px was matching speedo
  chrome at 0.94 (VM-POWER-001). Explicit `scale_tolerance` still applies.
- **`--save-comparisons` on composite `wait_until`.** `all` / `any`
  image conditions now write `actual.png` plus per-child `*_expected.png`
  (previously only leaf `image_present` steps produced comparison files).

## [1.2.5] - 2026-08-26

### Added
- **OCR evidence with `--save-comparisons`.** Text verifications
  (`text_present` / `text_not_present`) now save evidence like image verifies:
  `actual.png`, `ocr.txt` (per step: expected text, region, verdict, the text
  OCR actually read) and `<n>_ocr_region.png` with the OCR region outlined —
  for every step when `results.save_comparison_images` / `--save-comparisons`
  is on, and for the failing step on failure. The artifact directory is kept
  on success and `report.html` shows the screenshot plus a collapsible
  "OCR evidence" block. Text verification results now record their `region`.

## [1.2.4] - 2026-08-26

### Added
- **Suite-level `setup` / `teardown`.** A top-level `suite:` block in a test
  file runs Argus steps once per run — before the first selected test and
  after the last one (teardown always runs, even after failures, an early stop
  or Ctrl+C). Optional `device:` binds a configured device for `device.*`
  steps. A failed suite setup fails every selected test
  (`Suite setup failed: …`, category `suite_setup`) without executing them.
  New events `SuiteSetupStarted/Completed`, `SuiteTeardownStarted/Completed`;
  the console shows a **Suite** section. See `argus/docs/test-authoring.md`.

## [1.2.3] - 2026-08-26

### Added
- `argus --help` ends with a **Typical commands** section (e.g.
  `argus run --config config/2360x1300.yaml --no-logs --all --save-comparisons`);
  the same list is in `argus/docs/cli.md`.

## [1.2.2] - 2026-08-26

### Added
- **Stress / monkey / chaos testing (`argus stress`).** A deterministic,
  extensible subsystem (`argus.stress`) that drives randomized screen-aware UI
  actions, mutates backend entities underneath the app (context-aware, with
  data-mutation strategies and explicit safety boundaries), injects faults
  through pluggable injectors, detects failures from observable behaviour
  (crash, hang, blank/error screens, stale state, unexpected success), collects
  evidence, records an append-only trace, and supports `--dry-run`, seed
  reproduction, `argus stress replay <run-id>` and delta-debugging
  `argus stress minimize <run-id>`. Ships a self-contained demo store
  (`type: stress_demo`) and `examples/stress/checkout-chaos.yaml`.
  See `argus/docs/stress-testing.md`.

### Changed
- **Repository layout (monorepo).** Argus now lives in `argus/` and the Argus
  Test Creator (previously the separate `argus-test-creator` repository,
  imported with its history) in `argus-test-creator/`. The repository root is a
  uv workspace holding only shared files: `install.sh` / `install.ps1`,
  `action.yml`, this changelog, LICENSE and CONTRIBUTING. One `.venv` at the
  root serves both projects. `uses: kireol/argus@v1` keeps working unchanged.
  Run the CLI from `argus/` (or pass `--config`) — the default
  `test_paths: ["test_suites"]` is relative to the working directory, and
  `results/` is created there.
- Both packages share one version number from now on (1.2.1).

### Added
- First-class CI/CD integration: `argus ci run` orchestrates the existing
  engine with CI provider detection (GitHub Actions, GitLab CI, Jenkins,
  Azure Pipelines, generic `CI=true`, local), named suites (`ci.suites`, with
  `extends`), run-level retry of transient failures with per-attempt evidence
  and flaky detection, structured failure classification, a provider-neutral
  quality-policy engine (`failures`, `visual_regression`, `known_failure`,
  `flaky`, `required`), a deterministic `argus-results/` directory
  (`report.json` schema v1, `junit.xml` with CI properties, `report.html`
  with context/badges, `metadata/{ci,git,environment,preflight}.json`, DEBUG
  run log), GitHub job summaries and annotations, device-partitioned parallel
  workers (`--workers`, `sequential`/`balanced`), cooperative cancellation
  (SIGINT/SIGTERM → `status: cancelled`, `not_run` tests, exit 8), and a
  stable exit-code contract (0–8). A thin composite GitHub Action
  (`action.yml`, usable as `kireol/argus@v1`), `examples/ci/`, and
  `docs/ci-cd.md`.
- Engine: `RunOptions.retry` (run-level `RetryOverride`), `RunOptions.cancel`
  (cancellation token → `RunStatus.CANCELLED`), `RunOptions.skip_setup`,
  `RunOptions.results_dir`; `TestResult.flaky` / `initial_failure` /
  `attempt_history`; retries write to `<id>_attemptN/` instead of
  overwriting evidence. `write_junit_report`/`write_html_report` accept
  optional properties, header fields, notices and badges. `FakeDevice`
  `fail_first_screenshots` option for exercising retries.
- `examples/`: nine complete, buildable sample apps ("Argus Demo"), one per
  supported target (backend, web, desktop, android, ios, tvos, roku, esp32,
  yocto), each with its own `argus.yaml`, `tests/demo.yaml` suite, and
  README (Prerequisites/Build/Run/Tests/Troubleshooting); `examples/README.md`
  indexes all nine and documents the shared demo-app spec.
  `tests/unit/test_examples.py` loads every `examples/*/argus*.yaml` with
  the real config/test loaders as a regression check.
- Feature-level `setup`/`teardown`: a top-level `features:` block in test
  files runs steps once per feature (per platform) before its first selected
  test and after its last, with `FeatureSetup*`/`FeatureTeardown*` events and
  console lines. A failed feature setup fails that feature's tests without
  running them; teardown always runs. See `docs/test-authoring.md`.

### Fixed
- GitHub Action: new `working-directory` input (relative `argus.yml` paths
  resolve against it — needed when Argus lives in a subdirectory, as in this
  monorepo) and a new absolute `output-dir` output; `report-json`,
  `junit-xml`, `report-html` are now absolute paths. The repository workflow
  runs the action from `argus/`, installs the Qt platform libraries the
  Creator's pytest-qt needs, checks the job summary through Argus's run log
  (`$GITHUB_STEP_SUMMARY` is per-step), and the `ci run --help` test strips
  the ANSI styling Typer emits under `GITHUB_ACTIONS`.
- Android: every adb command now targets the resolved serial (`adb -s`), so
  commands issued before `connect()` can no longer hit "more than one
  device/emulator"; `is_application_running` treats `pidof`'s non-zero exit
  as "not running" instead of raising a connection error.

### Added
- Android: when no `serial` is configured and several devices/emulators are
  connected, interactive runs list them with numbers and prompt for a choice
  (with an `ANDROID_SERIAL` hint); `ANDROID_SERIAL` from the environment is
  honoured; a single connected device is used automatically.
- `desktop` device adapter (optional `argus[desktop]` extra, pyautogui): native
  applications on Windows, Linux and macOS — launch/stop/reset as a
  subprocess with stdout/stderr as logs, screenshots (optional `region` crop,
  HiDPI-aware pixel coordinates), mouse tap/swipe/long-press/drag, keyboard
  incl. `Ctrl+Shift+x` chords. Platform label defaults to the host OS
  (`windows` / `linux` / `macos`).
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

---

## Test Creator

Changes to `argus-test-creator/` (its changelog before the monorepo merge on
2026-08-26; later changes are recorded in the sections above).

### Added
- Android recording via `adb shell getevent`: device discovery with explicit selection,
  touchscreen discovery from `getevent -lp`, streaming subprocess with clean shutdown,
  typed raw-event parser, multi-touch slot tracking, coordinate mapping (axis ranges,
  inversion, rotation), gesture recognition (tap, swipe, long press, multi-touch, hardware
  keys), disconnect/reconnect, Android panel and diagnostics view in the GUI, `doctor`
  Android chain, fake ADB for tests, performance benchmark, and an Android recording guide.
- Generic `GESTURE` recording event and `MULTI_TOUCH` normalized action
  (`device.multi_touch`); `supports_hardware_keys` capability; `TargetLost`/`TargetRestored`
  session events.
- Authoring model (`AuthoringDocument`) independent of the Argus YAML format, with provenance
  on every step.
- Command-based undo/redo, metadata editor, step list with edit/rename/duplicate/reorder/
  disable/convert, YAML preview, validation panel, quality analyzer.
- Recorder framework: `RecorderAdapter` interface, bounded event sink with backpressure,
  crash-safe session journal with recovery, deterministic normalization (exact and smart modes).
- Recorder adapters: fake (Movies demo), browser (Playwright), desktop (pynput + mss),
  Android (ADB, controlled input).
- Assertion authoring: region selection with crop preview, OCR text picking, deterministic
  assertion suggestions after screen changes, image asset management with content-hash dedup.
- Argus integration: discovery, `argus validate`, `argus run` with `report.json` parsing,
  schema inspection.
- CLI: `version`, `new`, `gui`, `validate`, `export`, `doctor`, `demo`.
- Documentation and PyInstaller packaging configuration.

### Fixed
- `ui.bridge.watch` could miss the result of a job that finished before its signals were
  connected.
