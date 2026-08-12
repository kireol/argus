# Design Decisions & Assumptions

The specification (see `argus.rtf` at the repository root) left several
choices to engineering judgment. This records what was decided and why, so
future changes are made knowingly.

## Naming

- **Project: Argus; package/CLI: `argus`.** The spec mandates the `argus`
  command and suggests "universal-test-framework"; the repository carries
  the product name Argus. All user-facing behavior uses `argus`.

## Technology choices

- **Python 3.12+, synchronous engine.** The spec allows asyncio "where it
  provides real benefit"; V1 runs tests sequentially against one device at
  a time, so a synchronous engine is simpler, more debuggable, and loses
  nothing. Connection reuse and image caching deliver the performance
  requirements. If a parallel scheduler lands later, worker processes (one
  per device) are the intended model, not asyncio.
- **Typer + rich** for the CLI (spec preference), **httpx** with a pooled
  sync client, **opencv-python-headless** (no GUI dependencies),
  **paramiko** for SSH (pure Python — works on Windows without an ssh
  binary), **pytesseract** as the first OCR provider.
- **pytest is a dev-dependency only.** The spec permits pytest markers
  internally, but the runner is self-contained; pytest runs the framework's
  *own* tests. This keeps test authors fully inside YAML and the reporting
  pipeline uniform.

## Semantics

- **A test runs once per matching platform.** `platforms: [android, yocto]`
  with both devices configured yields two executions (reported separately,
  e.g. `MOV-001 (android)`). `--platform` narrows this. A test with no
  `platforms` runs once with no device bound.
- **Device selection:** `requires.devices` pins named devices; otherwise
  the first configured device for the platform (alphabetical) is used.
- **`--max-failures N` implies continue-until-N** — combining it with
  stop-on-first-failure would be contradictory, so an explicit
  `--max-failures` disables stop-on-first.
- **Failure categories** (`assertion`, `timeout`, `device_connection`,
  `backend`, `screenshot`, `error`) drive retry eligibility. Assertion
  failures are never retryable — the spec's "don't hide real bugs" rule.
- **Unresolved `${ENV}` in configuration degrades, not crashes.** The value
  stays literal, the component reports as *not configured*, and only a test
  that needs it fails. This lets one committed config serve machines with
  different capabilities.
- **Preflight is scoped to the selected tests.** OCR is only required if a
  selected test uses text conditions; the backend only if backend actions
  appear; each device only if its platform is requested. Optional
  components report as warnings, never fatal.

## Fake ecosystem

The spec requires examples that run against fakes. Rather than canned
always-pass screenshots, the fake backend holds state in memory and fake
devices **render that state** (artwork + title text) into their
screenshots. The example suite therefore exercises the real pipeline —
state change → capture → OpenCV/OCR verification — with zero hardware, and
an intentionally broken expectation genuinely fails.

## Security defaults

- SSH host-key verification: `reject` unknown hosts by default;
  `auto_add` is an explicit opt-in for lab devices.
- TLS verification on by default; disabling is per-backend configuration.
- Secrets only via environment variables; the logging layer additionally
  redacts `Authorization`/token/key patterns from all log output and
  artifacts.

## Deferred (deliberately, per spec §60/§57)

- GUI (event bus + JSON report schema are the prepared contract).
- Parallel scheduler and resource locking (architecture supports it: no
  globals, named devices, `requires.devices`).
- Additional conditions (`region_changed`/`region_unchanged` need a
  baseline-observation protocol — design before implementing).
- Appium, normalized coordinates, data-driven test matrices, alert
  providers beyond the terminal.

## License

MIT as a placeholder — for an internal product, replace `LICENSE` with the
company's standard internal license text.
