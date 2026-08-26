# Changelog

## [Unreleased]

### Added
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
