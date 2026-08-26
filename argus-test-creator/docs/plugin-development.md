# Developer guide

## Adding a recorder adapter

1. Create `adapters/<platform>/recorder.py` implementing `recording.adapter.RecorderAdapter`
   (and `ControllableRecorder` if the Creator must send input).
2. Report capabilities honestly: start from `targets.catalog.PLATFORM_CAPABILITIES[platform]`
   and refine at `connect()` time; list `limitations`.
3. Push `RecordingEvent`s into the `EventSink` from your own thread; mark high-frequency
   events `droppable=True`. Never touch the document or the UI.
4. Raise `TargetConnectionError` / `ScreenshotError` / `RecordingError` with `remediation`.
5. Register: `def register(registry): registry.register("<kind>", YourRecorder)` and add an
   entry point under `[project.entry-points."argus_test_creator.recorders"]`.
6. Add a `TargetProfile` (built-in in `targets/catalog.py` or via user config `targets:`) whose
   `argus_device_type`/`argus_device_options` make Argus address the same thing; mirror recorder
   settings in `app.context._sync_argus_options`.
7. Test with the fake ecosystem patterns in `tests/integration/test_fake_recording.py`.

## Adding an assertion provider / condition

Conditions come from Argus. Add the spec to `argus_schema/conditions.py` (params, capability
requirements, `visual=True` if it can be authored from a screenshot); the validator, dialogs and
quality analyzer pick it up. If the condition needs a new observation source (e.g. a new OCR
engine) implement `observation.ocr.OCRProvider` and wire it in `create_ocr_provider`.

## Adding an authoring command

Subclass `authoring.commands.DocumentCommand` with `do`/`undo` storing only what is needed to
revert; expose it through `AuthoringService` (which publishes the event); add a unit test that
applies, undoes and redoes it.

## Testing

* `tests/unit` — models, commands, normalizer, YAML, validation, quality, assets, captures,
  project, journal, config, schema/targets.
* `tests/integration` — fake recording session, app flow, Argus (skips without Argus),
  browser (skips without Playwright/Chromium).
* `tests/ui` — offscreen PySide6 via `pytest-qt`.
* `tests/performance` — 10k events, 1k events → YAML, undo/redo, 200 screenshots memory, 4K
  diff.

## Packaging

See `docs/packaging.md`.
