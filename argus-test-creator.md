# Claude Code Build Prompt — Argus Test Creator

## Mission

Build a **new, separate companion application** named **`argus-test-creator`** for the existing Argus testing framework.

The product is a visual test-authoring application. Its job is to make it dramatically easier for a human to create high-quality Argus YAML tests without manually writing YAML.

**Do not turn Argus itself into a GUI application. Do not add a recorder/wizard UI to the Argus repository.**

The architectural relationship is:

```text
                         ARGUS ECOSYSTEM

        ┌─────────────────────────────────────────┐
        │                  ARGUS                  │
        │                                         │
        │ Test execution engine                  │
        │ Device/backend adapters                │
        │ Assertions / visual verification       │
        │ OCR / OpenCV                           │
        │ Reporting                              │
        │ CLI / MCP                              │
        └────────────────────┬────────────────────┘
                             │
                       Argus Test YAML
                             │
              ┌──────────────┴──────────────┐
              │                             │
              ▼                             ▼
   ┌───────────────────────┐      ┌──────────────────────┐
   │ argus-test-creator    │      │ Manual YAML authoring │
   │                       │      │ / other future tools │
   │ • Test Wizard         │      └──────────────────────┘
   │ • Recorder            │
   │ • Assertion Authoring │
   │ • Test Editor         │
   │ • Import / Export     │
   │ • Future AI Authoring │
   └───────────────────────┘
```

The name **Argus Test Creator** is intentional. "Recorder" is a capability/mode inside the product, not the product name.

---

# 1. First instruction: understand the existing Argus repository before coding

Before creating code, thoroughly inspect the current Argus repository:

https://github.com/kireol/argus

The current Argus repository is a cross-platform functional and visual testing framework. Its central architectural rule is that the test engine is platform-agnostic and device/backend/instrumentation details live behind adapters.

Current Argus uses:

- Python 3.12+
- Pydantic 2
- PyYAML
- httpx
- Typer
- Rich
- OpenCV
- Pillow
- NumPy
- platformdirs
- optional OCR via pytesseract
- optional Playwright
- optional pyautogui
- optional Android/other adapters
- MCP support

Current Argus tests are declarative YAML.

Examples of existing actions include:

- `backend.set`
- `backend.get`
- `backend.post`
- `backend.put`
- `backend.patch`
- `backend.delete`
- `device.start`
- `device.stop`
- `device.restart`
- `device.reset`
- `device.tap`
- `device.swipe`
- `device.long_press`
- `device.drag`
- `device.pinch`
- `device.multi_touch`
- `device.key`
- `wait_until`
- `verify`
- `wait`
- `screenshot`
- `log`
- `shell.run`

Existing conditions include:

- `image_present`
- `image_not_present`
- `screenshot_matches`
- `text_present`
- `text_not_present`
- `pixel_matches`
- `instrumentation_value`
- `application_state`
- `backend_value`
- `log_contains`
- `now_playing`
- composition using `all`, `any`, and `not`

Do not blindly rely on the list above. **Inspect the current repository and use the actual current implementation as the source of truth.**

Read at minimum:

- `README.md`
- `docs/architecture.md`
- `docs/test-authoring.md`
- `docs/adapters.md`
- `docs/configuration.md`
- `docs/plugin-development.md`
- relevant adapter documentation
- current Pydantic models
- current action/condition registries
- current service layer
- current CLI
- current MCP implementation
- current fake adapter
- current tests

The current Argus architecture deliberately separates:

1. Backend — what state the system was told to have
2. Instrumentation — what the app claims internally
3. Device — how Argus communicates with the target
4. Observation — what is actually visible
5. Verification — whether the observation matches expectations

Preserve that philosophy.

---

# 2. Critical architectural boundary

`argus-test-creator` is a **separate application**.

Do NOT:

- add a GUI to Argus
- add recorder-specific UI to Argus
- duplicate Argus's test execution engine
- copy Argus's device implementations unnecessarily
- fork the Argus test runner
- create a second competing test format
- create a second assertion language
- make the Creator responsible for executing tests internally
- tightly import dozens of private Argus modules

The Creator's primary output is:

> **A valid Argus test definition and its associated assets.**

Argus remains responsible for execution.

The Creator may integrate with Argus through well-defined boundaries such as:

- invoking the installed `argus` CLI
- consuming documented/public APIs where genuinely appropriate
- optionally using a small compatibility/integration package
- reading Argus configuration
- invoking Argus validation
- eventually communicating through a stable service/API boundary

Prefer **loose coupling**.

If the Creator needs something from Argus that is currently private, stop and design a clean integration boundary rather than importing private implementation details everywhere.

---

# 3. Product vision

Argus Test Creator should allow a QA engineer who knows little or no YAML to create a professional Argus test.

The core workflow is:

```text
Create Test
     ↓
Choose target
     ↓
Record / build test
     ↓
Observe application
     ↓
Add assertions
     ↓
Review generated steps
     ↓
Edit / reorder / remove steps
     ↓
Validate
     ↓
Save/export Argus YAML
     ↓
Optionally run with Argus
```

The product should support multiple authoring modes.

Initial modes:

1. **Recorder**
2. **Wizard/manual step authoring**
3. **Existing YAML editor/import**

Future modes should be architecturally possible:

4. **AI-assisted authoring**
5. **Intent-based authoring**
6. **AI exploration**

Do not implement speculative AI functionality merely because the architecture supports it.

---

# 4. Core product principle

The product is NOT merely a mouse/keyboard recorder.

It is a **Test Creator**.

A raw recorder produces:

```text
click
wait 300ms
click
type
wait 500ms
scroll
click
```

The Creator should help turn that into a maintainable test:

```yaml
steps:
  - action: device.tap
    ...

  - action: wait_until
    condition:
      type: image_present
      ...

  - action: device.key
    ...

  - action: verify
    condition:
      type: text_present
      text: "Batman Begins"
```

The system must favor **semantic, maintainable test steps** over noisy raw input streams.

However, never invent behavior that was not observed or explicitly requested by the user.

---

# 5. Authoring model: the most important architectural decision

Do NOT record directly into final YAML.

Create a separate, strongly typed intermediate representation called something like:

```text
AuthoringDocument
```

or

```text
TestAuthoringModel
```

The exact name is yours to choose, but the concept is mandatory.

Architecture:

```text
User interaction
       ↓
Raw recording events
       ↓
Observation model
       ↓
Authoring model
       ↓
Human review/edit
       ↓
Argus YAML
```

The authoring model must be independent from the final YAML serialization format.

It should support:

- test metadata
- ordered steps
- raw actions
- normalized actions
- observations
- screenshots
- OCR observations
- assertion candidates
- explicit assertions
- target/device metadata
- capabilities
- timing
- provenance
- source recording information
- asset references
- warnings
- validation errors
- user annotations

Every generated step should ideally retain provenance such as:

```text
generated from recording event #17
```

This makes debugging the Creator much easier.

---

# 6. Proposed domain model

Design clean Pydantic/domain models.

At minimum consider:

```text
AuthoringDocument
RecordingSession
RecordingEvent
NormalizedAction
Observation
ScreenCapture
OCRObservation
AssertionDefinition
AssertionCandidate
TestStepDraft
TestMetadata
TargetProfile
DeviceCapabilities
AssetReference
AuthoringWarning
ValidationIssue
```

Do not blindly implement every field below. Design the model cleanly.

A recording event might contain:

```text
id
timestamp
event_type
target_id
coordinates
input_data
screenshot_before
screenshot_after
screen_info
ocr_snapshot
platform
device
capabilities
duration
metadata
```

An observation should represent what the Creator actually observed.

Do not confuse:

- "the application claims X"
- "the user clicked X"
- "OCR found X"
- "the screen visually contains X"

Those are different facts.

---

# 7. Recorder architecture

Recorder functionality must be implemented behind a platform-neutral interface.

For example:

```text
RecorderAdapter
    ├── BrowserRecorder
    ├── DesktopRecorder
    ├── AndroidRecorder
    ├── IOSRecorder
    ├── RokuRecorder
    ├── AppleTVRecorder
    ├── ESP32Recorder
    └── YoctoRecorder
```

Do NOT require every adapter to support every capability.

Use capability discovery.

Conceptually:

```python
RecorderCapabilities(
    supports_tap=True,
    supports_swipe=True,
    supports_keyboard=True,
    supports_mouse=True,
    supports_touch=True,
    supports_screenshot=True,
    supports_live_screen=True,
    supports_ocr=True,
    supports_element_metadata=False,
    ...
)
```

Unsupported capabilities must be explicit.

Never silently pretend a capability exists.

---

# 8. Do not make platform-specific assumptions

Argus supports multiple types of targets.

At minimum account architecturally for:

- web browsers
- desktop applications
- Android
- iOS
- Roku
- Apple TV
- ESP32
- Yocto / embedded Linux

The Creator should expose only capabilities actually supported by the selected target.

Example:

```text
Selected target: Roku

Available:
✓ screenshot
✓ key input
✓ tap/input if supported
✓ visual verification
✓ OCR if configured

Unavailable:
✗ pinch
✗ desktop mouse events
✗ DOM selectors
```

The UI should adapt to capabilities.

---

# 9. Browser recording

For browser targets, use Playwright where appropriate.

However, do not turn the Creator into a selector-dependent Selenium recorder.

Argus is fundamentally visual/external-observation oriented.

Browser-specific metadata can be captured as **additional evidence**, but the generated Argus test should remain valid under the Argus testing philosophy.

Capture useful information such as:

- URL
- viewport
- browser
- mouse coordinates
- keyboard input
- screenshots
- visible text
- possibly DOM metadata
- element bounding boxes
- accessible names where available
- navigation events

But keep these as observations/metadata unless they are explicitly converted into a supported Argus action.

---

# 10. Desktop recording

Support Windows/macOS/Linux where practical.

Desktop recording should capture:

- mouse click
- mouse movement only when useful
- mouse down/up
- drag
- double-click
- keyboard input
- hotkeys
- scroll
- screenshots
- window/screen information

Do NOT record every mouse movement by default.

Mouse movement is generally noise.

Provide a clean normalized event model.

For example:

```text
MouseDown + MouseMove* + MouseUp
```

should become:

```text
drag
```

when the gesture is meaningful.

Double-click should not become two unrelated clicks unless the target platform requires that representation.

---

# 11. Android recording

Use the existing Argus Android/ADB ecosystem where appropriate, but keep the Creator separate.

Investigate the best reliable mechanism for observing user interaction.

Do not invent a fake implementation.

Possible mechanisms may include:

- ADB input/event streams
- device screen capture
- scrcpy integration where appropriate
- controlled input through ADB
- screenshot polling

If a reliable generic recording mechanism is not available for a platform, implement the platform adapter with the supported subset and clearly report limitations.

The architecture must make it possible to improve the adapter later without redesigning the Creator.

---

# 12. Recorder UI

The primary UI should have a clean layout.

Suggested structure:

```text
┌──────────────────────────────────────────────────────────────┐
│ Argus Test Creator                           ● Connected     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Target / Device                                            │
│  [ Android - Pixel 8                         ▼ ]            │
│                                                              │
├───────────────────────────────┬──────────────────────────────┤
│                               │                              │
│       LIVE TARGET             │       TEST STEPS             │
│                               │                              │
│    ┌─────────────────────┐    │  1. Start application       │
│    │                     │    │  2. Tap "Movies"             │
│    │     APPLICATION     │    │  3. Tap "Search"             │
│    │                     │    │  4. Type "Batman"            │
│    │                     │    │  5. Wait for results         │
│    │                     │    │  6. Verify "Batman Begins"   │
│    └─────────────────────┘    │                              │
│                               │                              │
├───────────────────────────────┴──────────────────────────────┤
│ [Record] [Pause] [Add Step] [Add Verification] [Undo]       │
├──────────────────────────────────────────────────────────────┤
│ Status: Recording                                             │
└──────────────────────────────────────────────────────────────┘
```

This is a conceptual layout, not a pixel-perfect requirement.

Prioritize:

- clarity
- responsiveness
- low cognitive load
- keyboard accessibility
- large touch/click targets
- visible state
- minimal visual clutter

---

# 13. Recording modes

Implement two conceptual recording modes in the architecture.

## Exact mode

"Record what I do."

The Creator captures actions faithfully.

Useful for:

- reproducing bugs
- deterministic workflows
- unusual gestures
- debugging

## Smart/normalized mode

Normalize obvious noise.

Examples:

```text
tap
wait 100ms
tap
wait 200ms
```

may remain two taps if they are genuinely distinct.

But:

```text
mouse down
17 mouse move events
mouse up
```

should become one drag.

Do not use AI for this initial normalization.

Use deterministic rules first.

---

# 14. Assertion authoring

This is one of the most important features.

At any point during recording, the user can select:

> **Add Verification**

The Creator should capture the current screen and present options based on capabilities.

Example:

```text
Add Verification

○ Text is visible
○ Text is NOT visible
○ Image is visible
○ Image is NOT visible
○ Screen matches reference
○ Region matches reference
○ Pixel matches
○ Application state
○ Backend value
○ Log contains
○ Media is playing
```

Only show supported assertion types.

The Creator should use the current Argus condition registry/schema where possible, but must not become tightly coupled to private implementation details.

---

# 15. Visual assertion authoring

For:

```text
image_present
image_not_present
screenshot_matches
```

provide a visual region-selection tool.

User workflow:

```text
Current screenshot
       ↓
drag rectangle
       ↓
preview crop
       ↓
choose assertion type
       ↓
set threshold
       ↓
save asset
       ↓
insert assertion
```

For example:

```yaml
- action: verify
  condition:
    type: image_present
    image: assets/images/batman.png
    threshold: 0.90
    region:
      x: 100
      y: 200
      width: 300
      height: 400
```

Use the exact current Argus YAML schema where applicable.

Do not invent incompatible YAML fields.

---

# 16. OCR assertion authoring

Allow the user to select text visually or choose from detected OCR text.

Example:

```text
Detected text:

[ Batman Begins ]
[ Play ]
[ More Info ]

Select:
[ Batman Begins ]

Assertion:
Text is present
```

Generate the current Argus-compatible condition.

Support:

- exact text
- case-sensitive option where supported
- region
- negation where supported

OCR should be performed asynchronously so the UI never freezes.

---

# 17. Automatic assertion suggestions

The first implementation can use deterministic heuristics.

After an action that causes a meaningful screen change:

```text
Before screenshot
        ↓
Action
        ↓
After screenshot
        ↓
Compare
        ↓
OCR
        ↓
Suggest useful verification
```

For example:

```text
Argus Test Creator noticed:

✓ Screen changed significantly
✓ New text detected: "Batman Begins"
✓ New image region detected

Suggested verification:
[ Text "Batman Begins" appears ]

[ Add ] [ Ignore ]
```

Suggestions must never silently become test assertions.

The human remains in control.

---

# 18. Wait handling

One of Argus's important principles is:

> Do not use arbitrary fixed waits when the test can wait for a meaningful condition.

The Creator should therefore avoid generating:

```yaml
- action: wait
  duration: 2s
```

unless the user explicitly requests a fixed wait or the target requires it.

Prefer:

```yaml
- action: wait_until
  condition:
    ...
  timeout: ...
```

When the Creator detects a stable post-action state, suggest a meaningful wait/verification.

Do not generate fragile "sleep after every action" tests.

---

# 19. Step editing

Every recorded/generated step must be editable.

Required operations:

- rename step
- edit parameters
- edit coordinates
- change action type where compatible
- add step
- delete step
- duplicate step
- reorder step
- disable step temporarily
- convert action into verification where appropriate
- add notes
- inspect source/provenance
- undo/redo

Use a command-based undo/redo architecture rather than scattering state mutations through UI handlers.

---

# 20. Test metadata editor

Provide a clean editor for:

- ID
- name
- description
- feature
- tags
- platforms
- priority
- timeout
- requires/devices
- parameters
- retry policy
- setup
- teardown

Use the current Argus schema as the source of truth.

Provide validation as the user edits.

---

# 21. YAML preview/editor

The Creator should always be able to show the generated YAML.

Provide:

```text
Visual Editor
     ↕
YAML Preview
```

Changes in the visual model should regenerate YAML.

For MVP, YAML can be treated as a generated artifact rather than a fully bidirectional structured editor.

However, architecture should allow a future bidirectional YAML editor.

Do not create a fragile system where UI state is parsed back from arbitrary YAML on every keystroke.

---

# 22. Import existing Argus tests

Support opening an existing Argus YAML test.

Workflow:

```text
Open Argus Test
       ↓
Parse YAML
       ↓
Validate against current schema
       ↓
Convert into AuthoringDocument
       ↓
Edit visually
       ↓
Export
```

If a feature/action/condition is unknown, do not destroy it.

Represent unknown content as an "unsupported/custom" node and preserve the original YAML where possible.

Round-trip safety is important.

---

# 23. Asset management

The Creator must manage visual assets carefully.

For example:

```text
my-test/
├── test.yaml
└── assets/
    ├── images/
    │   ├── movie.png
    │   └── player.png
    └── ...
```

Do not scatter temporary screenshots throughout the test directory.

Use a temporary workspace for:

- recording screenshots
- intermediate crops
- OCR images
- previews

Only copy/promote assets into the test project when the user accepts them.

Use content hashes or another deterministic mechanism to avoid unnecessary duplicate assets.

---

# 24. Recording session storage

Recording sessions should be recoverable.

If the application crashes during a 20-minute recording, the user should not lose everything.

Use an append-friendly session journal or periodic snapshots.

Conceptually:

```text
session/
├── session.json
├── events/
├── screenshots/
└── assets/
```

The exact implementation is up to you.

Requirements:

- atomic writes
- crash recovery
- periodic checkpointing
- cleanup of abandoned sessions
- no unbounded memory growth

Do not hold an entire high-frequency screenshot stream in RAM.

---

# 25. Performance requirements

Performance is a first-class requirement.

The UI must remain responsive while:

- recording
- taking screenshots
- OCR is running
- image comparison is running
- assets are being saved
- devices are communicating
- YAML is being generated
- validation is occurring

Use asynchronous/background workers where appropriate.

Never perform:

- OCR
- OpenCV processing
- large image encoding
- device communication
- filesystem-heavy operations

on the UI thread.

Use bounded queues for high-frequency recorder events.

Implement backpressure.

For screenshot streams, avoid saving full-resolution frames unnecessarily.

Possible strategy:

```text
Live preview:
    low/medium resolution

Event evidence:
    full resolution only when required

Assertion asset:
    cropped/normalized region
```

Make screenshot capture frequency configurable.

---

# 26. Memory requirements

Do not keep every screenshot in memory.

Use:

```text
capture
  ↓
process
  ↓
persist or discard
  ↓
release memory
```

Use lazy loading for historical screenshots.

Thumbnails should be generated once and cached.

The UI should not decode 500 full-resolution screenshots just because a recording has 500 events.

---

# 27. Threading/concurrency model

Use a clear concurrency architecture.

Recommended conceptual layers:

```text
UI Thread
   │
   ├── Command Queue
   │
   ▼
Application Services
   │
   ├── Recorder Worker
   ├── Screenshot Worker
   ├── OCR Worker
   ├── Image Analysis Worker
   ├── Device Worker
   └── Persistence Worker
```

Do not create arbitrary threads throughout the codebase.

Centralize concurrency.

Ensure clean shutdown.

Every worker must have:

- cancellation
- timeout
- exception propagation
- lifecycle management

---

# 28. Event-driven internal architecture

Prefer events over tightly coupled UI callbacks.

For example:

```text
RecordingStarted
RecordingStopped
ActionObserved
ScreenshotCaptured
OCRCompleted
ScreenChanged
AssertionSuggested
AssertionAdded
StepAdded
StepRemoved
StepChanged
ValidationCompleted
TargetConnected
TargetDisconnected
```

The UI subscribes to these events.

This will make future:

- plugins
- remote recording
- AI
- alternate UIs
- automation
- testing

much easier.

---

# 29. Plugin architecture

Design the Creator so platform support can grow without modifying the core.

For example:

```text
creator/
├── core/
├── authoring/
├── recording/
├── observations/
├── assertions/
├── assets/
├── serialization/
├── validation/
├── integrations/
├── ui/
└── adapters/
    ├── browser/
    ├── desktop/
    ├── android/
    ├── ios/
    ├── roku/
    ├── tvos/
    ├── esp32/
    └── yocto/
```

Use interfaces/protocols.

The exact package layout can differ, but the separation of concerns must remain.

---

# 30. Recommended technology direction

Choose technology based on the existing Argus ecosystem and the cross-platform requirements.

A strong default is:

- Python 3.12+
- Pydantic 2
- PySide6 for desktop UI
- asyncio/background workers where appropriate
- Pillow/OpenCV for image handling
- pytesseract or a clean OCR provider abstraction
- Playwright for browser integration
- existing Argus adapters/integration where appropriate
- pytest
- Ruff
- mypy

However:

**Do not blindly follow this recommendation.**

First inspect the repository and evaluate whether a different UI technology provides materially better cross-platform recording, packaging, maintainability, or performance.

If you choose a different stack, document why.

The important requirements are:

- cross-platform
- fast
- maintainable
- independently deployable
- testable
- plugin-friendly
- easy for future developers/AI coding agents to modify

---

# 31. Packaging and installation

The application should eventually be easy for a user to install.

Design for:

- Windows
- macOS
- Linux

Do not make the user manually construct a Python environment just to use the GUI if a packaged distribution is practical.

Support developer mode as well.

Target UX:

```text
Install Argus Test Creator
        ↓
Launch
        ↓
Choose/create Argus project
        ↓
Create test
```

Keep packaging configuration separate from application logic.

---

# 32. Argus integration

Create an explicit integration layer.

Possible responsibilities:

```text
ArgusIntegration
├── discover installed Argus
├── validate environment
├── validate test
├── run test
├── locate project
├── inspect supported schema
└── retrieve results
```

Do not hardcode the path to `argus`.

Support:

- configured executable
- PATH lookup
- virtual environment
- project-local executable where appropriate

The Creator should detect when Argus is unavailable and explain exactly how to configure it.

---

# 33. "Run Test" feature

The Creator should eventually provide:

```text
[ Save ]
[ Validate ]
[ Run with Argus ]
```

When Run is selected:

1. Save the current document.
2. Validate it.
3. Invoke Argus.
4. Capture structured output.
5. Display progress.
6. Show final result.
7. Provide a link/open action for the Argus report/artifacts.

Do NOT implement a second test execution engine.

---

# 34. Validation

Validation should happen at multiple levels.

### UI validation

Examples:

```text
ID is required
Feature is required
Step has invalid parameter
```

### Authoring model validation

Pydantic/domain validation.

### Argus schema validation

Use the current Argus-compatible schema.

### Optional final validation

Run:

```text
argus validate
```

or the current equivalent when available.

Validation errors should be actionable.

Example:

```text
Invalid condition: image_present

Missing:
  image

Step:
  "Verify movie artwork"

Fix:
  Select an image asset.
```

---

# 35. YAML generation

Generated YAML must be:

- deterministic
- readable
- stable
- human-editable
- compatible with Argus
- minimally noisy

Do not serialize internal UI state into YAML.

Do not emit unnecessary defaults.

Preserve meaningful ordering.

Use stable formatting.

Example:

```yaml
id: MOV-001
name: Movie artwork appears
feature: Movies
tags:
  - smoke
  - movies
steps:
  - action: device.tap
    x: 420
    y: 310

  - action: wait_until
    condition:
      type: image_present
      image: assets/images/movie.png
      threshold: 0.9
    timeout: 10s

  - action: verify
    condition:
      type: text_present
      text: "Batman Begins"
```

Use the actual current Argus schema rather than assuming this exact formatting.

---

# 36. Do not generate bad tests

The Creator should actively guard against common test-authoring mistakes.

Examples:

### Bad

```yaml
- action: wait
  duration: 2s
- action: wait
  duration: 3s
- action: wait
  duration: 5s
```

### Better

```yaml
- action: wait_until
  condition:
    type: image_present
    ...
```

Other problems to detect:

- duplicate IDs
- missing assertions
- excessive fixed waits
- redundant taps
- impossible capabilities
- unsupported gestures
- missing image assets
- overly broad screenshot comparisons
- extremely low image thresholds
- unbounded OCR regions
- unresolved variables
- platform mismatch
- invalid device requirements

Warnings should be helpful but should not prevent expert users from doing unusual things when valid.

---

# 37. Test quality analysis

Create a `TestQualityAnalyzer` service.

It can report:

```text
Test Quality

✓ Has a meaningful name
✓ Uses synchronization
✓ Contains verification
⚠ Contains 3 fixed waits
⚠ Step 7 appears redundant
⚠ Screenshot assertion covers entire 1920x1080 screen
✓ Assets are referenced
```

This should be deterministic in V1.

Architect it so AI-based analysis can replace/augment it later.

---

# 38. Undo/redo

Use a proper command model.

For example:

```text
Command
├── AddStepCommand
├── DeleteStepCommand
├── MoveStepCommand
├── EditStepCommand
├── AddAssertionCommand
├── RemoveAssertionCommand
├── SetMetadataCommand
└── AddAssetCommand
```

Do not implement undo by taking huge full-application snapshots unless there is a compelling reason.

The document should have a clear revision state.

---

# 39. Persistence

Use a project format that is simple and transparent.

The generated Argus test remains:

```text
test.yaml
```

Creator-specific project/session metadata should not pollute Argus YAML.

If a project file is needed, make it explicit, for example:

```text
.argus-creator/
```

or another clearly named directory.

Do not make the generated test dependent on the Creator being installed.

A user must be able to take the generated YAML/assets and run them with Argus alone.

This is a hard requirement.

---

# 40. Future AI architecture

Do NOT implement a full AI exploration engine in the initial version.

But design clean extension points.

Future pipeline:

```text
Human intent
     ↓
AI authoring assistant
     ↓
Observation history
     ↓
Authoring model
     ↓
Human approval
     ↓
Argus YAML
```

Later:

```text
Application
     ↓
Observation
     ↓
AI exploration engine
     ↓
Candidate tests
     ↓
Argus Test Creator
     ↓
Human review
     ↓
Argus tests
```

Do not make the core architecture depend on an LLM provider.

If AI is eventually added, support a provider abstraction.

---

# 41. Security

Treat application content as untrusted.

The Creator may encounter:

- arbitrary screenshots
- OCR text
- web content
- logs
- application strings
- device output

Do not automatically execute commands found in screen text, OCR, logs, or web content.

Do not allow an observed string to become a shell command without explicit user action.

If `shell.run` is authored, make the user explicitly approve/edit it.

Do not transmit screenshots or test data to external AI services by default.

The Creator should be local-first.

---

# 42. Logging and diagnostics

Use structured logging.

Never log:

- passwords
- tokens
- API keys
- credentials

Provide a diagnostic mode.

When recording fails, show:

```text
Recording failed

Target:
Android / Pixel 8

Operation:
Screenshot

Error:
...

Suggested action:
Reconnect device and retry.

Technical details:
...
```

Do not expose raw stack traces to normal users.

Allow advanced users to expand technical details.

---

# 43. Accessibility

The UI should support:

- keyboard navigation
- visible focus
- sensible tab order
- accessible labels
- screen reader-friendly controls where supported
- no critical operation requiring a mouse

---

# 44. Testing strategy

This project must be heavily tested.

Minimum test layers:

## Unit tests

Test:

- authoring models
- event normalization
- command/undo system
- YAML generation
- YAML parsing
- validation
- asset management
- quality analyzer
- capability handling
- project persistence

## Integration tests

Test:

- fake recorder
- fake device
- fake screenshots
- OCR provider
- image provider
- Argus integration
- project round-trip

## UI tests

Use an appropriate headless/offscreen strategy for as much as possible.

Test:

- creating a test
- adding steps
- editing steps
- adding verification
- saving
- reopening
- undo/redo
- validation errors

## Performance tests

At minimum test:

- 1,000 recorded events
- 10,000 lightweight events
- hundreds of screenshots
- large screenshots
- long OCR sessions
- repeated undo/redo

The application must not degrade catastrophically as recordings become large.

---

# 45. Fake target / development environment

Create a fake target/recorder environment so developers can work without real hardware.

The fake system should be able to:

- emit screenshots
- emit taps
- emit swipes
- emit keyboard events
- simulate screen transitions
- return capabilities
- provide deterministic OCR
- simulate device connection/disconnection

This should become the basis of most automated tests.

---

# 46. Example application/demo

Create a simple demo target that allows the Creator to demonstrate the complete workflow without external hardware.

For example:

```text
Movies Demo

Home
 ├── Movies
 │    ├── Search
 │    ├── Movie list
 │    └── Movie details
 └── Settings
```

The demo should support:

- clicking/tapping
- typing
- screen changes
- visible text
- images
- loading states
- a deliberate failure mode

The goal is to demonstrate:

```text
Record
→ observe
→ add verification
→ generate YAML
→ validate
→ run through Argus
→ view result
```

Do not modify the real Argus example apps unless explicitly necessary. Prefer a separate Creator demo fixture/application.

---

# 47. Repository structure

Create a clean repository.

A reasonable starting point:

```text
argus-test-creator/
├── README.md
├── LICENSE
├── pyproject.toml
├── CONTRIBUTING.md
├── CHANGELOG.md
├── docs/
│   ├── architecture.md
│   ├── getting-started.md
│   ├── recording.md
│   ├── assertions.md
│   ├── integrations.md
│   ├── plugin-development.md
│   └── troubleshooting.md
├── src/
│   └── argus_test_creator/
│       ├── app/
│       ├── core/
│       ├── models/
│       ├── authoring/
│       ├── recording/
│       ├── observation/
│       ├── assertions/
│       ├── assets/
│       ├── serialization/
│       ├── validation/
│       ├── quality/
│       ├── integrations/
│       ├── plugins/
│       └── ui/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── performance/
│   └── fixtures/
├── examples/
└── scripts/
```

Adjust the structure if your architectural investigation shows a better organization.

Avoid giant modules.

Prefer cohesive modules with explicit dependencies.

---

# 48. Dependency rules

Establish dependency direction.

A strong target is:

```text
UI
 ↓
Application services
 ↓
Domain/core
 ↓
Ports/interfaces
 ↑
Adapters/integrations
```

The domain/core must not depend on PySide6.

The authoring model must not depend on Playwright.

The authoring model must not depend on ADB.

The serializer must not depend on UI classes.

The recorder adapters must not mutate UI state directly.

The UI should issue commands/use cases.

This is important for long-term maintainability.

---

# 49. Configuration

Support a clear configuration hierarchy.

Potential sources:

1. built-in defaults
2. project configuration
3. user configuration
4. environment variables
5. explicit CLI arguments

Do not hardcode device addresses.

Do not hardcode paths.

Use platform-appropriate user data directories.

Keep secrets out of configuration files when possible.

---

# 50. CLI

Even though the product is GUI-first, provide a small CLI for automation and diagnostics.

For example:

```text
argus-test-creator --help
argus-test-creator version
argus-test-creator validate project/
argus-test-creator export project/
argus-test-creator doctor
```

Do not duplicate Argus CLI functionality.

The Creator CLI should operate on Creator projects and integrations.

---

# 51. "Doctor" command

Provide a diagnostic command that checks:

- Python/runtime if relevant
- Creator installation
- Argus installation
- Argus executable
- Playwright availability
- OCR availability
- screenshot support
- configured targets
- permissions required for desktop recording
- ADB availability
- connected Android devices
- writable project directories

Output should be easy to understand.

---

# 52. Documentation requirements

Documentation is part of the implementation.

Write:

## README

Explain:

- what Argus Test Creator is
- why it exists separately from Argus
- installation
- quick start
- recording a first test
- exporting to Argus
- running with Argus

## Architecture

Explain:

- separation from Argus
- authoring model
- recorder abstraction
- observation model
- assertion system
- integration boundary
- event system
- persistence
- plugin model

## Recording guide

Explain:

- browser
- desktop
- Android
- capabilities
- recording limitations

## Assertion guide

Explain:

- visual assertions
- OCR
- regions
- suggestions
- synchronization

## Developer guide

Explain:

- adding a recorder adapter
- adding an assertion provider
- adding a new authoring command
- testing
- packaging

---

# 53. Code quality requirements

Use:

- type hints
- Pydantic/domain models where appropriate
- small cohesive functions
- dependency injection
- explicit interfaces
- structured errors
- deterministic serialization
- testable services

Avoid:

- global mutable state
- singleton-heavy architecture
- giant UI classes
- business logic inside UI callbacks
- hidden background threads
- arbitrary sleeps
- duplicated schemas
- magic strings scattered throughout the code

Run:

```text
pytest
ruff
mypy
```

and ensure the project is clean.

---

# 54. Error handling requirements

Every external operation needs:

- timeout
- cancellation where appropriate
- clear error classification
- actionable remediation

Examples:

```text
TargetConnectionError
ScreenshotError
RecordingError
OCRProviderError
AssetError
SerializationError
ArgusIntegrationError
ValidationError
UnsupportedCapabilityError
```

Do not expose low-level exceptions directly to normal users.

---

# 55. Do not over-engineer V1

Build a strong foundation, but do not implement everything imaginable.

The first usable milestone should be:

```text
Launch Creator
    ↓
Select a target
    ↓
Record basic interactions
    ↓
Capture screenshots
    ↓
Add text/image verification
    ↓
Edit steps
    ↓
Generate valid Argus YAML
    ↓
Save test + assets
    ↓
Validate
    ↓
Optionally run with Argus
```

That is the MVP.

Do not implement:

- autonomous AI exploration
- autonomous test generation
- cloud accounts
- collaboration servers
- test management integrations
- analytics platforms
- complicated plugin marketplaces

unless explicitly requested later.

Architect for them; don't build them now.

---

# 56. Development phases

Implement in phases and keep the application runnable after every phase.

## Phase 0 — Repository and architecture

- inspect Argus thoroughly
- establish project structure
- establish domain models
- establish event system
- establish persistence
- establish tests
- create fake target

Deliverable:

A running Creator shell with a fake test project.

## Phase 1 — Test authoring

Implement:

- metadata editor
- step list
- add/edit/delete/reorder
- assertion editor
- YAML generation
- validation
- save/load
- undo/redo

Deliverable:

Create a test without recording.

## Phase 2 — Recorder framework

Implement:

- RecorderAdapter interface
- recording session
- event model
- normalization
- screenshot capture
- event persistence
- fake recorder

Deliverable:

Record a fake target and generate a test.

## Phase 3 — First real adapter

Implement the easiest/highest-value real target first based on repository analysis.

Browser is a likely candidate because Playwright provides strong observability.

Deliverable:

Record a real browser workflow.

## Phase 4 — Desktop

Implement desktop recording where supported.

## Phase 5 — Android

Implement Android recording with honest capability reporting.

## Phase 6 — Assertion authoring polish

Implement:

- region selection
- image crop
- OCR selection
- assertion suggestions
- quality analysis

## Phase 7 — Argus integration

Implement:

- discover Argus
- validate
- run
- show result
- open report

## Phase 8 — Packaging

Produce:

- Windows package
- macOS package
- Linux package

as practical.

---

# 57. Definition of done

Do not declare the project complete because the UI launches.

The initial production-quality milestone is complete only when:

- [ ] Argus itself was not modified to add Creator UI.
- [ ] A clean authoring model exists.
- [ ] Recording is separate from final YAML generation.
- [ ] Recorder functionality is a mode/capability inside Test Creator.
- [ ] Target capabilities are explicit.
- [ ] Unsupported capabilities fail clearly.
- [ ] User can create a test without YAML.
- [ ] User can record a test.
- [ ] User can add visual assertions.
- [ ] User can add OCR assertions.
- [ ] User can edit/reorder/delete steps.
- [ ] Undo/redo works.
- [ ] Generated YAML is valid Argus YAML.
- [ ] Assets are stored correctly.
- [ ] Recording sessions can recover after interruption.
- [ ] UI remains responsive during expensive work.
- [ ] Large recordings do not cause unbounded memory growth.
- [ ] Existing Argus tests can be imported without destroying unsupported content.
- [ ] Creator can validate using Argus.
- [ ] Creator can optionally run a test using Argus.
- [ ] Fake target tests cover the core architecture.
- [ ] Unit/integration/performance tests exist.
- [ ] Documentation exists.
- [ ] Packaging is documented.
- [ ] No core business logic lives in UI callbacks.
- [ ] No private Argus internals are scattered throughout the Creator.
- [ ] The Creator-generated YAML works without the Creator being installed.

---

# 58. Important implementation rule for Claude Code

You are not being asked to merely produce a prototype.

Build this as a **maintainable open-source project** that another developer can understand and modify.

Before implementing a major subsystem:

1. inspect the relevant Argus implementation
2. identify the public/stable contract
3. define the Creator-side interface
4. implement the smallest clean version
5. write tests
6. document the decision

Do not make assumptions about Argus APIs when the repository can be inspected.

Do not duplicate existing Argus behavior without a reason.

Do not change Argus to make the Creator easier to implement.

If an Argus integration point is insufficient, document the limitation and create a clean boundary rather than creating tight coupling.

---

# 59. Required final workflow demonstration

Before considering the first milestone complete, demonstrate this complete flow using the fake target and then at least one real supported target:

```text
Launch Argus Test Creator
        ↓
Create New Test
        ↓
Select target
        ↓
Start Recording
        ↓
Perform several interactions
        ↓
Stop Recording
        ↓
Review normalized actions
        ↓
Select a screenshot region
        ↓
Add image verification
        ↓
Add OCR/text verification
        ↓
Reorder/edit a step
        ↓
Open YAML preview
        ↓
Validate
        ↓
Save project
        ↓
Close Creator
        ↓
Reopen project
        ↓
Verify test is unchanged
        ↓
Run through Argus
        ↓
Display Argus result/report
```

The final implementation should make this workflow feel natural rather than like a developer tool.

---

# 60. Product philosophy

Keep these principles visible while developing:

### Argus is the engine.

### Argus Test Creator is the authoring experience.

### Recorder is a capability inside Test Creator.

### YAML is the portable contract.

### The Authoring Model is the internal contract.

### Observations are evidence.

### Assertions are explicit.

### The human remains in control.

### Platform-specific behavior belongs behind adapters.

### Expensive operations happen off the UI thread.

### Generated tests should be maintainable, not merely replayable.

### Build for future AI without making V1 depend on AI.

### Prefer simple, composable architecture over clever architecture.

---

# 61. Start now

Do not immediately write hundreds of files.

First:

1. Inspect the current Argus repository thoroughly.
2. Produce a concise architecture assessment for the Creator.
3. Identify the exact Argus test schema and integration points.
4. Identify which existing Argus components can be safely consumed externally.
5. Identify the best first recording target.
6. Create the repository structure.
7. Implement Phase 0.
8. Run all tests.
9. Then proceed phase-by-phase.

At each phase, keep the application runnable.

When you encounter an architectural choice, favor:

- loose coupling
- explicit interfaces
- dependency injection
- testability
- platform isolation
- performance
- deterministic behavior
- future extensibility

Do not sacrifice those qualities just to get a demo working faster.

The ultimate goal is a polished application called:

# Argus Test Creator

with:

> **Record it. Author it. Verify it. Export it. Run it with Argus.**

