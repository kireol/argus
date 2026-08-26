# Architecture

## Separation from Argus

```text
┌──────────────────────────────┐        argus CLI (subprocess)        ┌──────────────────┐
│      Argus Test Creator      │ ───────────────────────────────────▶ │      Argus       │
│  authoring · recording · UI  │ ◀─────────────────────────────────── │  engine · adapters│
└──────────────────────────────┘   exit codes, report.json, stdout    └──────────────────┘
                 │
                 ▼  writes
   tests/<ID>.yaml · assets/images/*.png · argus.yaml
```

The Creator talks to Argus only through public, documented surfaces:

| Need | Boundary |
| --- | --- |
| Is Argus installed? Which version? | `argus --version` (discovery order: configured → `ARGUS_EXECUTABLE` → project `.venv` → `PATH` → interpreter prefix) |
| Validate a test | `argus validate --config argus.yaml --framework-only` (+ `argus list`) |
| Run a test | `argus run --config argus.yaml --test ID --no-logs`; result from `<results.dir>/<stamp>/report.json` (schema_version 1) and exit code |
| Which actions/conditions exist? | `argus_schema/` (data), cross-checked against the installed Argus by running a one-line script in *Argus's* interpreter |
| Address a device | the `devices:` entry the Creator upserts into `argus.yaml` from the `TargetProfile` |

The Creator never imports `argus.*`. It therefore works with any Argus installation, in any
virtualenv, and never depends on private engine details.

## Package layout and dependency direction

```text
ui/            PySide6 widgets, dialogs, MainWindow            (depends on app)
app/           CreatorApp use-cases, configuration, demo flow  (depends on everything below)
authoring/     AuthoringService + Commands (undo/redo)
recording/     RecorderAdapter protocol, EventSink, SessionJournal, EventNormalizer, RecordingSession
observation/   CaptureStore, OCR providers, screen diff, AssertionSuggester
assertions/    (assertion catalog lives in argus_schema; authoring helpers in app)
assets/        AssetManager (workspace crops → promoted project assets)
serialization/ YAML writer / reader (round-trip safe)
validation/    DocumentValidator (actionable issues)
quality/       TestQualityAnalyzer (deterministic; replaceable by AI later)
project/       CreatorProject on-disk layout
targets/       target catalog + per-platform capabilities
argus_schema/  Argus actions & conditions as data
integrations/  ArgusIntegration (subprocess boundary)
adapters/      fake · browser · desktop · android recorder adapters   (depend on recording/, models/)
demo/          Movies demo app (Pillow renderer) + web version
models/, core/ Pydantic models; errors, events, commands, workers, paths, logging
```

Rules: `models`/`core` depend on nothing else; the authoring model does not know Playwright or
ADB; the serializer does not know Qt; recorder adapters never touch UI or documents — they push
`RecordingEvent`s into an `EventSink`.

## The authoring model (internal contract)

```text
user interaction → RecordingEvent (raw, faithful)
                → NormalizedAction (deterministic rules, stable ids)
                → StepDraft in an AuthoringDocument (edited by humans, with Provenance)
                → Argus YAML (serializer)
```

`AuthoringDocument` holds metadata, ordered steps (+ setup/teardown), asset references, the
target profile, warnings, annotations, session ids and any unknown top-level YAML fields. A step
carries `action`, `params`, an optional structured `condition` (mirrors Argus's
`ConditionSpec` including `all/any/not`), `enabled`, `notes`, `custom` (unknown action kept
verbatim) and `provenance` ("generated from recording event(s) evt_…").

The observation models keep different facts apart: what the user did (`RecordingEvent`), what the
screen looked like (`ScreenCapture`, on disk), what OCR read (`OCRObservation`). None is treated
as truth about the application.

## Recorder abstraction and capabilities

`RecorderAdapter` (protocol): `connect/disconnect`, `capabilities`, `screenshot()`,
`screen_size()`, `start_recording(sink)`, `stop_recording()`, `describe_limitations()`.
Optionally `ControllableRecorder` (`send_tap/send_key/send_text`) for targets whose input the
Creator must send (fake demo, Android, Roku-style remotes).

`RecorderCapabilities` is explicit per target (`targets/catalog.py` derives it from Argus's
`DeviceCapabilities` per adapter intersected with what the recorder can observe). The UI only
offers supported actions and assertion types; `CreatorApp.require_capability` raises
`UnsupportedCapabilityError` otherwise. Nothing is silently pretended.

Adapters register through the `argus_test_creator.recorders` entry-point group, so new
platforms are plugins.

## Recording pipeline and concurrency

```text
adapter thread ──push──▶ EventSink (bounded queue; pointer moves are droppable → backpressure)
                              │
                       drain thread (RecordingSession)
                              ├─ after-capture per gesture end → CaptureStore (PNG on disk)
                              ├─ append to events.jsonl (fsync every 25) + session.json checkpoint
                              ├─ normalize (pure) → ActionObserved / ActionUpdated events
                              └─ WorkerPool job: diff + OCR + AssertionSuggester → events
```

* All expensive work runs in `core.workers.WorkerPool` (cancellation, timeout, exception
  propagation, shutdown). The UI marshals events/jobs to the GUI thread via `ui.bridge`.
* Screenshots are never held in RAM by the model; thumbnails are generated once and cached in a
  bounded LRU.
* Crash recovery replays `events.jsonl` (torn last line ignored) and re-normalizes.

## Event system

`core.events.EventBus` — typed, thread-safe, subscriber failures isolated. Domain events:
`RecordingStarted/Paused/Resumed/Stopped`, `ActionObserved`, `ActionUpdated`,
`ScreenshotCaptured`, `OCRCompleted`, `ScreenChanged`, `AssertionSuggested`,
`RecordingFailed`, `StepAdded/Removed/Changed`, `AssertionAdded`, `MetadataChanged`,
`DocumentChanged/Replaced`, `TargetConnected/Disconnected`, `ValidationCompleted`,
`RunStarted/Output/Finished`, `ProjectOpened`.

## Persistence

* `tests/<ID>.yaml` — the contract (deterministic key order, no defaults, block lists).
* `.argus-creator/documents/<ID>.json` — the authoring document (provenance). If the YAML is
  edited outside the Creator (newer mtime) the YAML wins on load.
* `.argus-creator/sessions/<stamp>/` — journals + screenshots; abandoned sessions are cleaned
  after 7 days.
* `.argus-creator/workspace/` — temporary crops/previews; never referenced by tests.

## Undo/redo

`core.commands.CommandStack` with small reversible commands (`AddStep`, `DeleteStep`,
`MoveStep`, `EditStep`, `DuplicateStep`, `SetCondition`, `SetMetadata` (merges consecutive
edits of one field), `AddAsset`, `SetLifecycleSteps`, `AddSteps`). No document snapshots.

## Future AI

`quality.QualityAnalyzer` and `observation.AssertionSuggester` are the extension points for
AI-assisted analysis; `AuthoringService` is the only way to change a document, so an assistant
would propose `StepDraft`s that a human approves. No LLM provider is referenced anywhere.

## Technology choice

PySide6 was kept as recommended: the whole Argus ecosystem is Python; Qt gives native
cross-platform windows, an offscreen platform for tests, keyboard accessibility for free, and
PyInstaller packaging. Alternatives (Electron/Tauri + Python sidecar) would add a second language
and IPC layer for no recording benefit — Playwright, pynput/mss and ADB are all Python-driven.
