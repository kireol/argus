# Architecture

## The one rule

```text
                    ┌──────────────┐
                    │ Test YAML    │      declarative, no Python
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ Test Engine  │      platform-agnostic
                    └──────┬───────┘
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
         Backend        Device      Instrumentation
         Adapter        Adapter        Adapter
             │       ┌─────┴─────┐       │
             │       ▼           ▼       │
             │    Android      Yocto     │
             └─────────────┬─────────────┘
                           ▼
                     Observation
                           ▼
                      Verifiers
                           ▼
                        Result
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
          Console         JSON         JUnit / HTML
                                         │
                                         ▼
                                  Future GUI / CI
```

The engine knows nothing about ADB commands, SSH, REST details, or GUI
concerns. Test definitions know nothing about Python. Instrumentation
enhances diagnostics but **never** replaces external visual verification:
if the app claims `image_loaded: true` but the artwork is not on screen,
the test fails.

## Package map

```text
src/argus/
├── models/           Pydantic data models: test definitions, results,
│                     observations, regions, health checks
├── config/           configuration models + layered loader (env expansion)
├── engine/           loader, filters, TestContext, RunSession, wait system,
│                     TestRunner (the public service API)
├── adapters/         Device ABC + capabilities, registry, android (ADB),
│                     yocto (SSH + ScreenshotProvider), backend (httpx), fakes
├── instrumentation/  instrumentation protocol + HTTP client
├── actions/          Action ABC + registry + built-ins (backend.*, device.*,
│                     wait, wait_until, verify, screenshot, log)
├── conditions/       Condition ABC + factory + built-ins + all/any/not
├── verifiers/        Verifier ABC, AssetStore (cached reference images),
│                     OpenCV image verifiers, OCR text verifiers
├── ocr/              OCRProvider ABC, tesseract + fake providers
├── preflight/        PreflightCheck ABC, built-in checks, preflight runner
├── artifacts/        per-run/per-test artifact directories, retention
├── reporting/        console (event-driven), JSON, JUnit, HTML, alerts
├── events/           event model + synchronous EventBus
├── logging/          structured logging, secret redaction
├── cli/              Typer CLI — a thin client of TestRunner
└── utilities/        duration parsing, variable expansion
```

## Separation of concerns (spec §2)

Five concepts are kept strictly apart:

| Concept | Owner | Question it answers |
| --- | --- | --- |
| Backend | `BackendAdapter` | What state was the system told to have? |
| Instrumentation | `InstrumentationClient` | What does the app *claim* internally? |
| Device | `Device` | How do we talk to the hardware? |
| Observation | `Observation` | What is actually visible? |
| Verification | `Verifier` | Does the observation match the expectation? |

## Execution flow

1. **Load & validate** — every YAML file is parsed into `TestDefinition`
   models; duplicate IDs or invalid fields abort before anything runs.
2. **Filter** — `TestFilter` selects by id/feature/tag/platform/expression.
3. **Pre-flight** — `build_preflight_checks` assembles only the checks the
   selected tests need (backend if backend actions are used, OCR if text
   conditions are used, each required device + screenshot + instrumentation).
   A failing *required* check stops the run: zero tests execute, one clear
   error is printed, the preflight report is saved.
4. **Execute** — per test × platform: a `TestContext` is assembled from the
   session's shared services, then setup → steps → teardown run (teardown
   always runs). Failures are categorized (`assertion`, `timeout`,
   `device_connection`, `backend`, `screenshot`, `error`) and only
   explicitly-configured categories are retried.
5. **Diagnose** — on failure the runner captures actual/expected/diff
   images, device logs, instrumentation state, and metadata into the test's
   artifact directory.
6. **Report** — every state change is published on the `EventBus`; the
   console reporter renders live, and JSON/JUnit/HTML reports are written at
   the end of the run.

## Sessions, contexts, and lifetimes

- **`RunSession`** (one per run) owns expensive shared things: connected
  devices (a device stays connected for the whole session), the pooled HTTP
  backend client, instrumentation clients, the reference-image cache, and
  the action/condition registries. Nothing in it is global — a future GUI
  can hold several sessions.
- **`TestContext`** (one per test execution) carries the session services
  plus per-test state: variables, artifacts, a context-bound logger, and the
  last observation. Actions and conditions receive everything through it —
  dependency injection, no singletons.

## Events (GUI-readiness, spec §41–43)

`TestRunner` publishes `TestRunStarted`, `PreflightStarted`,
`PreflightCheckCompleted`, `PreflightCompleted`, `TestStarted`,
`ActionStarted`, `ActionCompleted`, `TestPassed`, `TestFailed`,
`TestSkipped`, `TestRunCompleted`. The console reporter is just one
subscriber; a GUI subscribes to the same bus and consumes the same
`report.json` schema. The CLI never contains engine logic:

```python
from argus.config import load_config
from argus.engine import TestRunner, TestFilter
from argus.engine.runner import RunOptions

runner = TestRunner(load_config())
result = runner.run(RunOptions(filters=TestFilter(features=["movies"])))
```

## Extension points (spec §54)

| Extensible thing | Interface | Registration |
| --- | --- | --- |
| Actions | `argus.actions.Action` | `ActionRegistry.register` / entry point `argus.actions` |
| Conditions | `argus.conditions.Condition` | `ConditionFactory.register` / entry point `argus.conditions` |
| Devices | `argus.adapters.Device` | `DeviceRegistry.register` / entry point `argus.devices` |
| Screenshot providers | `argus.adapters.ScreenshotProvider` | adapter configuration |
| OCR providers | `argus.ocr.OCRProvider` | `create_ocr_provider` |
| Verifiers | `argus.verifiers.Verifier` | used by conditions |
| Pre-flight checks | `argus.preflight.PreflightCheck` | `build_preflight_checks` |
| Alerts | `argus.reporting.alerts.AlertProvider` | CLI wiring |
| Reporters | subscribe to `EventBus` | `TestRunner(config, events)` |

## Parallelism & resources (spec §34–36)

V1 executes tests sequentially — deliberately. The architecture is ready for
workers: sessions have no shared mutable global state, tests declare
`requires.devices`, and devices are addressed by name (`yocto-living-room`),
so a future scheduler can allocate one worker per device without engine
changes. Do not parallelize tests against the same device.

## Error handling

All framework errors derive from `argus.exceptions.UTFError` and carry a
`remediation` hint. Specific classes (`ConfigurationError`, `PreflightError`,
`DeviceConnectionError`, `BackendError`, `InstrumentationError`,
`ScreenshotError`, `VerificationError`, `TestDefinitionError`,
`TestExecutionError`, `TimeoutExceededError`, `AssetError`) map onto failure
categories used by retry policies. Every external operation — HTTP call, SSH
command, adb invocation, screenshot, wait — has a timeout.
