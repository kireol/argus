# Test Authoring

Tests are YAML files under `test_suites/` (configurable via `test_paths`).
A file holds one test, or several under a top-level `tests:` list. They are
written for QA personnel: declarative, no Python.

## Anatomy of a test

```yaml
id: MOV-001                      # required, unique across the whole suite
name: Movie artwork appears      # required, short human title
description: >                   # optional but encouraged
  Verify that the correct movie artwork appears on screen
  when the backend changes the selected movie.
feature: Movies                  # required, groups tests in reports/filters
tags: [smoke, movies, visual]    # optional, for filtering
platforms: [android, yocto]      # runs once per platform with a configured device

priority: high                   # optional metadata
timeout: 60s                     # optional overall budget (informational)

requires:                        # optional resource requirements
  devices: [yocto-living-room]   # pin to a specific configured device

parameters:                      # optional variables, referenced as ${name}
  movie_id: 123
  movie_image: movie_123.png

retry:                           # optional, explicit, category-limited
  count: 2
  only: [timeout, device_connection]

setup:                           # optional, runs before steps
  - action: device.reset

steps:                           # required, the test body
  - action: backend.set
    data:
      movieId: ${movie_id}
  - action: wait_until
    condition:
      type: image_present
      image: ${movie_image}
      threshold: 0.90
    timeout: 10s
    poll_interval: 250ms
  - action: verify
    condition:
      type: image_present
      image: ${movie_image}

teardown:                        # optional, ALWAYS runs, even on failure
  - action: backend.set
    data:
      movieId: null
```

IDs must be unique — duplicate IDs abort the run before any test executes.

## Actions

| Action | Parameters | Does |
| --- | --- | --- |
| `backend.set` | `data`, optional `endpoint` | POST state to the backend (the canonical way to drive the app) |
| `backend.get` / `post` / `put` / `patch` / `delete` | `endpoint`, optional `data`, `params`, `expect_status` | raw backend request |
| `device.start` / `stop` / `restart` / `reset` | — | application lifecycle |
| `device.tap` | `x`, `y` | tap the screen |
| `device.swipe` | `from_x`, `from_y`, `to_x`, `to_y`, optional `duration` | swipe |
| `device.key` | `key` (e.g. `HOME`, `BACK`, `DPAD_UP`) | key press |
| `wait_until` | `condition`, `timeout`, `poll_interval` | poll a condition — **the** synchronization tool |
| `verify` | `condition` | evaluate once; fails the test if false |
| `wait` | `duration` | fixed sleep — discouraged, logs a warning |
| `screenshot` | optional `file` | capture and save to the test's artifacts |
| `log` | `message` | write to the structured log |
| `shell.run` | `command`, optional `args` (list), `timeout`, `cwd`, `expect_exit` | run a host command (simulators, helpers, etc.) |

Every step may carry an optional `name:` used in reports.

## Conditions

Used by `wait_until` and `verify`:

```yaml
condition:
  type: image_present        # see table below
  image: movie_123.png
  threshold: 0.92            # optional, overrides config default
  region: movie_artwork      # optional: named region or inline {x,y,width,height}
```

| Type | Key parameters | True when |
| --- | --- | --- |
| `image_present` | `image`, `threshold`, `region`, `grayscale`, `scale_tolerance`, `mask_background`, `mask_luminance` | reference image found in screenshot |
| `image_not_present` | same | reference image NOT found |
| `screenshot_matches` | `image`, `threshold`, `region` | whole screenshot (or region) similar to reference |
| `text_present` | `text`, `region`, `case_sensitive` | OCR finds the text |
| `text_not_present` | same | OCR does not find the text |
| `pixel_matches` | `x`, `y`, `color` (`"#rrggbb"` or `[r,g,b]`), `tolerance` | pixel has the color |
| `instrumentation_value` | `key`, `equals` or `contains` | app's `/test/status` field matches |
| `application_state` | `key` (dotted), `equals` or `contains` | app's `/test/state` value matches |
| `backend_value` | `key` (dotted), `equals`, optional `endpoint` | backend state value matches |
| `log_contains` | `text` or `pattern` (regex), `lines` (default 200), `case_sensitive` | recent device logs (logcat / `log_command` / browser console) contain the text; negate with `not:`. `pattern` is matched with `re.MULTILINE`, so `^`/`$` anchor to individual log line boundaries, not the whole scanned block |

Log assertions poll like any other condition, so they work in `wait_until`:

```yaml
- action: wait_until
  timeout: 10s
  condition:
    type: log_contains
    pattern: "Player: state=(PLAYING|BUFFERING)"
```

### Composition

```yaml
condition:
  all:
    - type: image_present
      image: movie.png
    - type: text_present
      text: "Star Wars"
```

`all`, `any`, and `not` nest arbitrarily. Within one evaluation pass a single
screenshot is shared by every visual sub-condition — composition never costs
extra captures.

## Synchronization

Never use `wait` to "give the app time". Say what you're waiting **for**:

```yaml
- action: wait_until
  condition:
    type: image_present
    image: movie_123.png
  timeout: 15s
  poll_interval: 250ms
```

The condition is checked immediately, then polled. On timeout the step fails
with category `timeout` (retryable, if the test opts in).

## Variables

`parameters` define per-test variables; `variables` in configuration define
global ones. Reference them anywhere in step parameters as `${name}`.
A step value that is *only* a reference keeps its type
(`movieId: ${movie_id}` stays an integer). `${name:-default}` provides a
fallback. Unresolved references fail the test with a clear message.

## Test data (spec §53)

Prefer referencing shared data over duplicating it. Keep shared values in
configuration `variables`, or point `parameters` at IDs
(`movie: movie_123`) and let the backend own the details. Data files under
`assets/test-data/` are conventionally YAML and may be referenced from
custom actions/conditions.

## Retries (spec §37)

Retries are **opt-in and category-limited**. Assertion failures are never
retryable — a flaky assertion is a bug to fix, not to hide:

```yaml
retry:
  count: 2
  only: [timeout, device_connection]   # also allowed: backend, screenshot
```

## Isolation (spec §38)

Do not assume execution order. Reset what you rely on in `setup:`
(`device.reset`, a `backend.set` to a known state) and clean up in
`teardown:` — teardown runs even when the test fails.
