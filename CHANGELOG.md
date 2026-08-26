# Changelog

## [Unreleased]

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

### Fixed
- `ui.bridge.watch` could miss the result of a job that finished before its signals were
  connected.
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
