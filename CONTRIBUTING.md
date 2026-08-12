# Contributing

## Development setup

```bash
git clone <repository>
cd universal-test-framework
./install.sh --dev        # or .\install.ps1 -Dev on Windows
```

## Running the framework's own tests

The self-test suite needs **no hardware** — it uses fake devices, a fake
backend, and a local HTTP server:

```bash
.venv/bin/python -m pytest                # everything
.venv/bin/python -m pytest tests/unit     # fast unit tests only
.venv/bin/python -m pytest -m integration # integration tests only
```

## Quality gates

Before submitting changes:

```bash
.venv/bin/ruff check src tests     # lint (must be clean)
.venv/bin/python -m pytest         # tests (must pass)
.venv/bin/mypy src                 # types (keep the noise down)
```

## Design rules

These are the architectural invariants; changes that violate them will be
rejected regardless of how useful the feature is:

1. **The engine stays platform-agnostic.** Anything ADB-, SSH-, or
   HTTP-specific lives in an adapter behind an interface.
2. **Instrumentation never replaces observation.** A test may *read*
   application-internal state for diagnostics or synchronization, but a
   visual expectation passes only on externally captured evidence.
3. **No global state.** Services are constructed by `RunSession`/`TestRunner`
   and injected via `TestContext`.
4. **New capabilities are plugins.** Actions, conditions, verifiers, devices,
   OCR and screenshot providers register by name (or entry point); adding one
   must not modify the engine.
5. **Every external operation has a timeout** and raises a specific
   exception from `argus.exceptions` with actionable remediation text.
6. **Secrets never reach logs or artifacts.** Use `${ENV_VAR}` configuration
   references; the logging layer redacts, but don't rely on it.

## Adding a plugin

See [docs/plugin-development.md](docs/plugin-development.md) for worked
examples (new action, new condition, new device adapter, new OCR provider).

## Commit style

Small, focused commits with imperative subjects ("Add Weston screenshot
provider"), a body explaining *why* when the change isn't obvious, and tests
alongside the code they cover.
