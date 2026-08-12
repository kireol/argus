# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project adheres to [Semantic Versioning](https://semver.org/).

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
