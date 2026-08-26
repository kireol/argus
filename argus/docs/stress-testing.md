# Stress, Monkey and Chaos Testing

`argus stress` deliberately exercises an application under unpredictable and adverse
conditions and reports what the application *actually did*. It combines three modes on one
engine:

| Mode | What it does | Typical findings |
|---|---|---|
| **Monkey** | Randomized, screen-aware UI actions: taps, swipes, scrolls, back/home, typing, lifecycle changes. | crashes, hangs, error screens, dead-end navigation |
| **Stress** | The same actions at high frequency, in bursts, for a long time, with cooldowns and limits. | leaks, slowdowns, "unresponsive" states, input-flood bugs |
| **Chaos** | Backend mutations and fault injection *underneath* the running app: create/update/delete/disable the entities on screen, inject latency/timeouts/HTTP errors. | stale UI, operations that succeed on deleted data, lost updates, missing error handling |

The core principle: **Argus mutates the world underneath the application while observing
what the application does**, through the same screenshots, OCR and image comparison the
functional engine uses. No application instrumentation is required (it is used when present).

Every run is **deterministic for a seed**, keeps an **append-only trace**, collects
**evidence** for each failure, can be **replayed**, and failing sequences can be
**minimized** to the shortest reproduction.

## Quick start

```bash
# self-contained example: a fake store app with a deliberate stale-checkout bug
argus stress --scenario examples/stress/checkout-chaos.yaml

# same run, same seed → same logical sequence
argus stress --scenario examples/stress/checkout-chaos.yaml --seed 84729163

# see the plan without applying any mutation
argus stress --scenario examples/stress/checkout-chaos.yaml --dry-run

argus stress list
argus stress replay <run-id>        # or: latest
argus stress minimize <run-id>      # delta-debug the failing sequence
```

A run prints a summary and, for each application failure, the sequence that led there,
the likely issue, the seed and the replay command:

```text
Argus Stress Run
----------------

Run ID:       2026-08-26_14-02-11-9163
Seed:         84729163
Scenario:     checkout-chaos
Device:       store (DemoStoreDevice)
Duration:     58s
Status:       completed — max actions reached (300)

Actions:      300
Mutations:    41
Faults:       0
Observations: 300

Failures:
  Critical:   1
  Error:      0
  Warning:    2
  Info:       0

Reproducible failures:
  3

Failure #1  (2026-08-26_14-02-11-9163-F001)
Category: Unexpected Success
Severity: CRITICAL
Detector: stale_entity  (confidence 85%)

Sequence:
   62 TAP Interstellar
   63 TAP Add to cart / Checkout
   64 MUTATION   DELETE products/3
   65 TAP Add to cart / Checkout

Likely issue:
  'order confirmed' shown for products 'Interstellar' after backend delete — operation
  succeeded on stale state

Seed:
  84729163

Replay:
  argus stress replay 2026-08-26_14-02-11-9163
```

Exit codes: `0` no error/critical application failure, `1` failures found, `2` configuration
problem, `3` infrastructure error (device/backend/harness), `130` cancelled.

## Configuration

Stress settings live under `stress:` in the Argus configuration, or in a scenario file
passed with `--scenario` (same keys at the root or under `stress:`; a scenario file may also
carry `backend:` / `devices:` overrides so it is self-contained). CLI flags override files.

```yaml
stress:
  name: checkout-chaos
  seed: 84729163            # omit for a fresh seed (always reported)
  device: store             # required when several devices are configured
  duration: 10m             # shorthand for limits.duration
  max_actions: 10000        # shorthand for limits.max_actions

  monkey:
    enabled: true
    actions:                # weight, or {weight, enabled, params}
      tap: 40
      swipe: 15
      scroll: 15
      back: 10
      type_text: 10
      long_press: 3
      double_tap: 2
      reload: 5
      background: 5
      restart: 1
      rotate: 2             # skipped automatically when the device cannot rotate
      wait: 5
    delay: {min: 0.05, max: 0.75}
    burst_probability: 0.05 # repeat an action rapidly (stress)
    burst_max: 5
    targets:
      prefer_known: 0.85    # meaningful targets vs. random coordinates
      use_ocr: true
      ocr_refresh_every: 5
      regions:              # named interaction regions (optional)
        - {name: "Save", x: 40, y: 1080, width: 640, height: 80, weight: 3, actions: [tap]}
      avoid_words: ["Sign out", "Delete account"]
    typing:
      words: ["batman", "matrix", "1234"]
      allow_unicode: true
      allow_special: true

  backend_mutations:
    enabled: true
    probability: 0.15       # per step
    contextual_probability: 0.8   # prefer entities visible on screen
    style: auto             # state | rest | auto
    schema_endpoint: /api/stress/schema   # REST discovery (optional)
    operations:
      create: {enabled: true, weight: 10}
      update: {enabled: true, weight: 50}
      delete: {enabled: false, weight: 10}
      duplicate: 5
      disable: 5
      archive: 3
    entities:               # explicit contract (used when discovery is unavailable/partial)
      products:
        operations: [create, update, delete]
        id_field: id
        state_key: products       # state-document collections
        path: /api/products       # REST collections
        current_key: current_product   # state key naming the entity on screen
        fields:
          title: {type: string, display: true}
          price: {type: number, min: 0, max: 100}
          stock: {type: integer, min: 0, max: 50}
          status: {type: enum, values: [active, disabled]}
          released: {type: date}
        disable: {status: disabled}
    timing: {before_action: 1, after_action: 2, delayed: 1}
    delayed_max: 1s
    reconcile_timeout: 3s   # how long the UI may take to reflect a mutation
    scheduled:              # targeted scenarios
      - {mutation: delete, entity: products, timing: on_context, when_text: "Your cart",
         delay: 500ms, repeat: true}
      - {mutation: update, entity: products, entity_id: 3, after_action_index: 20,
         data: {price: 0}}

  data_mutations:           # strategies applied to create/update payloads
    probability: 0.5
    max_per_mutation: 2
    "null": true
    empty: true
    missing: true
    duplicate: true
    very_long_string: true
    special_characters: true
    unicode: true
    zero: true
    negative: true
    minimum: true
    maximum: true
    out_of_range: true
    invalid_enum: true
    past_date: true
    future_date: true

  faults:
    enabled: false
    injector: backend       # backend | fake | <plugin>
    probability: 0.05
    types: {latency: 3, timeout: 1, http_error: 2, disconnect: 1, empty_response: 1,
            malformed_response: 1, duplicate_response: 1}
    latency_max: 2s
    duration_max: 5s
    http_statuses: [400, 401, 403, 404, 409, 429, 500]

  limits:
    duration: 10m
    max_actions: 10000
    max_mutations: null
    max_consecutive_actions: 50
    cooldown: 0s
    max_runtime: null       # hard wall-clock cap
    observe_every: 1        # screenshot every N actions

  failures:
    stop_on_first: false
    max_failures: null
    stop_severity: error
    restart_after_crash: true
    disabled_detectors: []
    error_words: ["error", "exception", "crashed", "something went wrong"]
    success_words: ["order confirmed", "thank you", "saved", "success"]
    stale_window_actions: 25
    frozen_after_actions: 8

  safety:
    allow_destructive_mutations: false
    allowed_entities: [products]
    denied_entities: []
    allowed_operations: []
    denied_operations: []
    environment: test
    allowed_environments: [test, local, fake]
    require_capabilities: true
    dry_run: false

  evidence:
    history: 50
    observations: 3
    save_screenshots: true
    save_logs: true
    sample_every: 0
    max_failures_with_evidence: 100

  results_dir: results/stress
```

Every strategy, action, mutation, detector and injector is a registered component; unknown
names are reported, never fatal.

## Safety

The engine will **never** perform a destructive mutation (delete, disable, archive) unless
*all* of the following hold:

1. `safety.allow_destructive_mutations: true` (or `--allow-destructive`);
2. the entity is allowed (`allowed_entities` when set, never `denied_entities`);
3. the operation is allowed;
4. the backend declares the entity and operation (`require_capabilities`);
5. the environment is **known** and **allowed**: `safety.environment` names it, or the
   backend declares it (`environment` in its state or schema contract). A contradiction
   between the two counts as unknown — and unknown means *refuse*.

`--dry-run` plans everything and blocks every mutation, printing the sequence with
`[BLOCKED: dry run]` markers. Blocked mutations are recorded in the trace and counted in the
summary.

## Backends and schema discovery

Mutations go through a *mutation backend* built on the Argus backend adapter:

* **state** (`style: state`) — entities are collections inside the state document served by
  `GET/POST backend.state_endpoint` (the Argus fake/demo world works this way). Collections
  are discovered automatically: any state key holding a list of objects with `id`.
* **rest** (`style: rest`) — conventional collections: `GET /products`, `POST /products`,
  `PATCH /products/{id}`, `DELETE /products/{id}`. Configure `path` per entity or a
  `schema_endpoint` that returns the contract:

```json
{
  "environment": "test",
  "entities": {
    "products": {
      "operations": ["create", "update", "delete"],
      "fields": {"title": "string", "price": {"type": "number", "max": 100},
                 "status": ["active", "disabled"]}
    }
  }
}
```

Explicit `entities:` configuration always overrides and fills gaps in discovery. Backends
that cannot support concurrent mutation declare `supports_concurrency: false` in the contract.

### Context-aware mutations

The engine prefers to mutate what the user is looking at. Context comes from
*extractors*:

* **state** — `entities.<name>.current_key` names the state field holding the entity on
  screen (`current_product: 3` → products/3);
* **OCR** — display fields (`display: true`, or `title`/`name`/`label`) of known entities are
  matched against the words OCR reads from the latest screenshot;
* plugins (`argus.stress.extractors`) — application-specific sources (URLs, instrumentation).

`scheduled:` mutations express targeted races: `timing: on_context` fires when a relevant
entity is on screen; `when_text` further gates on visible text; `delay` waits before the
mutation; `repeat`/`max_times` control how often.

## Failure detection

Detectors combine signals and assign their own severity and confidence:

| Detector | Signal | Category / severity |
|---|---|---|
| `action_error` | the action itself failed | application ERROR — or *infrastructure* WARNING / *unsupported* INFO |
| `mutation_error` | the backend refused an allowed mutation | backend WARNING |
| `crash` | `is_application_running()` turned false | crash CRITICAL |
| `blank_screen` | uniform screenshot | visual ERROR |
| `frozen_screen` | screen unchanged across N change-expecting actions | hang ERROR |
| `error_screen` | OCR finds an `error_words` phrase | application ERROR |
| `stale_entity` | a deleted/disabled entity's label is still on screen after `reconcile_timeout` | stale_state WARNING |
| `stale_entity` | …together with a `success_words` phrase that was *not* there when the mutation landed | **unexpected_success CRITICAL** |

Infrastructure problems (device gone, screenshot failed, detector bug) are reported
separately — never as application bugs. Three consecutive infrastructure failures abort the
run. Every application failure gets an evidence directory:

```text
results/stress/<run-id>/
  run.json                    seed, scenario, device, summary, failures
  trace.jsonl                 every action / mutation / fault / failure, in order
  failures/<failure-id>/
    before.png  after.png  ocr.txt  history.json  logs.txt  backend_state.json  failure.json
```

## Reproduction, replay, minimization

* **Seed** — `argus stress --seed N --scenario S` regenerates the same logical sequence
  (same Argus version, configuration and device).
* **Replay** — `argus stress replay <run-id>` re-executes the recorded trace: actions,
  mutations, faults and delays in order, through the same detectors. The report says how
  many of the original failure signatures reproduced.
* **Minimize** — `argus stress minimize <run-id> [--failure <id|category:detector>]` runs
  delta debugging over the trace: remove a chunk, replay, keep the reduction when the same
  failure signature reproduces, halve the chunk size, stop at granularity 1 or
  `minimize_max_iterations`. Replays are cached by subset. The minimal run is saved as a new
  run (`minimized_from`).

The trace is the source of truth; human-readable logs are never needed to reproduce.

## Performance and scale

Histories are bounded (`evidence.history`), only the last `evidence.observations`
screenshots are kept in memory, the trace streams to disk, backend schemas and entity
listings are cached, OCR runs at most every `targets.ocr_refresh_every` actions, deadlines
use a monotonic clock. Independent runs own their RNG, trace, artifacts and state, so
several can execute concurrently (each with its own device).

## Extending

All extension points are registries with entry-point groups; none require touching the
engine.

| Extension | Base class | Entry-point group |
|---|---|---|
| UI action | `argus.stress.actions.StressActionType` | `argus.stress.actions` |
| Mutation type | `argus.stress.mutations.MutationType` | `argus.stress.mutations` |
| Data mutation strategy | `argus.stress.mutations.DataMutationStrategy` | `argus.stress.data_mutations` |
| Failure detector | `argus.stress.detectors.FailureDetector` | `argus.stress.detectors` |
| Fault injector | `argus.stress.faults.FaultInjector` | `argus.stress.faults` |
| Context extractor | `argus.stress.extractors.ContextExtractor` | `argus.stress.extractors` |
| Target provider | `argus.stress.targets.TargetProvider` | (`StressComponents.target_providers`) |
| Mutation backend | `argus.stress.mutations.MutationBackend` protocol | (`StressComponents.mutation_backend`) |

```python
# myplugin/stress.py
from argus.stress.actions import StressActionType
from argus.stress.models import StressAction

class ShakeAction(StressActionType):
    name = "shake"
    requires = ("shake",)          # DeviceCapabilities flag or an adapter method name

    def generate(self, context, targets, params):
        return StressAction(action_type=self.name)

    def perform(self, context, action):
        context.require_device().shake()

def register(registry):
    registry.register(ShakeAction())
```

```toml
[project.entry-points."argus.stress.actions"]
myplugin = "myplugin.stress:register"
```

Devices gain lifecycle chaos by implementing optional methods the probe looks for:
`rotate(orientation)`, `background_application()`, `foreground_application()`, `reload()`,
`type_text(text)`, `clear_text()`, and `screen_text()` (an exact text layer that replaces
OCR — used by the demo store).

Future AI-driven exploration can consume the same structures — `StressContext`
(observations, action/mutation history, entity context, failures) and the trace — without
any change to the engine; the current implementation has no AI dependency.

## The example scenario

`examples/stress/checkout-chaos.yaml` drives the built-in demo store (device and backend
type `stress_demo`): a catalog → product → cart → checkout flow rendered from the fake
backend's state. With `buggy: true` the app accepts a checkout for a product the backend
deleted while the cart was open; the scenario's scheduled mutation creates exactly that
race and the `stale_entity` detector reports an **unexpected success** with before/after
screenshots and the replayable sequence. Set `buggy: false` and the same scenario reports no
critical failure. Add `"<script>"` to `monkey.typing.words` to also exercise crash handling:
typing it into the search box crashes the demo app — the run records the crash, relaunches
the app and continues.
