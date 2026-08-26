# Contributing

## Ground rules

1. **Argus is not modified to make the Creator easier.** If an Argus integration point is
   insufficient, document the limitation in `docs/integrations.md` and design a boundary here.
2. **Dependency direction** (enforced by review; see `docs/architecture.md`):
   `ui → app → (authoring | recording | observation | …) → models/core ← adapters/integrations`.
   The domain never imports Qt, Playwright, ADB or Argus internals.
3. **No business logic in UI callbacks.** The UI calls `CreatorApp` use-cases and subscribes to
   events.
4. **Every mutation of a document is a Command** (`authoring/commands.py`) so undo/redo stays
   correct.
5. **Capabilities are explicit.** Never pretend a target supports something; report limitations.
6. **Background work goes through `WorkerPool`.** No ad-hoc threads.

## Workflow

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev,ocr,browser]"
.venv/bin/python -m pytest
.venv/bin/ruff check src tests && .venv/bin/mypy src
```

Add tests with every change: unit tests for models/services, an integration test with the fake
target for behaviour, a UI test (offscreen, `pytest-qt`) for anything the user clicks.

When Argus adds an action or condition, update `src/argus_test_creator/argus_schema/` — the
integration test `test_schema_catalog_in_sync_with_installed_argus` fails until you do.

## Commit messages

Imperative mood, one logical change per commit. Reference the docs you updated.
