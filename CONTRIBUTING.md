# Contributing

This repository is a monorepo with two installable projects:

| Directory | Package | What it is |
|-----------|---------|------------|
| `argus/` | `argus` | The test engine and CLI |
| `argus-test-creator/` | `argus-test-creator` | The desktop authoring app (recorder, wizard, editor) |

Both share one virtual environment at the repository root (`.venv/`), one
`CHANGELOG.md`, one `LICENSE`, and one version number.

## Development setup

```bash
git clone https://github.com/kireol/argus
cd argus
./install.sh --dev        # or .\install.ps1 -Dev on Windows
```

This installs both packages editable, with their `dev` extras, into `.venv/`.

## Quality gates

Run the gate of every project you touched. Neither needs hardware — they use
fake devices, a fake backend and a local HTTP server (Argus) or the fake
recording target and an offscreen Qt platform (Creator).

```bash
# Argus
cd argus
../.venv/bin/ruff check src tests
../.venv/bin/mypy src
../.venv/bin/python -m pytest              # everything
../.venv/bin/python -m pytest tests/unit   # fast unit tests only

# Test Creator (ruff + mypy + pytest in one go)
cd argus-test-creator
scripts/dev.sh
```

The Creator's integration tests run the real `argus` from `.venv/`; if it is
not installed they are skipped.

## Design rules — Argus (`argus/`)

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

See [argus/docs/plugin-development.md](argus/docs/plugin-development.md) for
worked examples (new action, new condition, new device adapter, new OCR
provider).

## Design rules — Test Creator (`argus-test-creator/`)

1. **Argus is not modified to make the Creator easier.** The Creator never
   imports Argus; it drives the installed `argus` CLI as a subprocess. If an
   Argus integration point is insufficient, document the limitation in
   `argus-test-creator/docs/integrations.md` and design a boundary there.
2. **Dependency direction** (see `argus-test-creator/docs/architecture.md`):
   `ui → app → (authoring | recording | observation | …) → models/core ← adapters/integrations`.
   The domain never imports Qt, Playwright, ADB or Argus internals.
3. **No business logic in UI callbacks.** The UI calls `CreatorApp` use-cases
   and subscribes to events.
4. **Every mutation of a document is a Command** (`authoring/commands.py`) so
   undo/redo stays correct.
5. **Capabilities are explicit.** Never pretend a target supports something;
   report limitations.
6. **Background work goes through `WorkerPool`.** No ad-hoc threads.

When Argus gains an action or condition, mirror it in
`argus-test-creator/src/argus_test_creator/argus_schema/` — the integration
test `test_schema_catalog_in_sync_with_installed_argus` fails until you do.

## Commit style

Small, focused commits with imperative subjects ("Add Weston screenshot
provider"), a body explaining *why* when the change isn't obvious, and tests
alongside the code they cover. Record user-visible changes in `CHANGELOG.md`.
