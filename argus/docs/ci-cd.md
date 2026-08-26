# CI/CD integration

`argus ci run` makes Argus a CI/CD-aware test execution platform without tying
the test engine to any CI vendor. It is an **orchestration layer** over the
same engine `argus run` uses:

```text
                    Argus Test Engine  (TestRunner / RunSession / adapters)
                           ▲
                 ┌─────────┴─────────┐
            argus run            argus ci run
            (local)              ┌────┼──────────┐
                              Context  Policy  Reporting
                                 │        │        │
                            detection  gates   provider adapters
                                                (GitHub, generic, …)
```

What the CI layer adds on top of the engine:

| Concern | What you get |
|---|---|
| Provider detection | GitHub Actions, GitLab CI, Jenkins, Azure Pipelines, generic (`CI=true`), local |
| Suites | Named selection policies (`--suite pr`) resolved into the engine's own filters |
| Retry | Explicit, run-level retry of *transient* failures only; flaky tests are reported |
| Quality gates | Failure / visual regression / known-failure / flaky / required-suite policies |
| Artifacts | Deterministic `argus-results/` with `report.json`, `junit.xml`, `report.html`, metadata, evidence |
| Provider reporting | GitHub job summary + annotations via GitHub's environment mechanisms (no API/token) |
| Workers | Optional parallel execution with device partitioning |
| Exit codes | A stable, documented contract for pipelines to branch on |

Everything works outside CI too (`provider = local`), so you can rehearse a
pipeline on your machine:

```bash
argus ci run --config examples/ci/argus-ci.yml --suite pr --dry-run
argus ci run --config examples/ci/argus-ci.yml --suite pr
```

## `argus ci run`

```text
argus ci run [--suite NAME] [selectors] [--provider P] [--config FILE]
             [--dry-run] [--fail-fast] [--retry N] [--workers N] [--strategy S]
             [--output-dir DIR] [--no-report] [--no-artifacts] [--skip-preflight]
             [--verbose] [--quiet]
```

| Option | Meaning |
|---|---|
| `--suite/-s NAME` | Run a suite from `ci.suites` |
| `--test/-t`, `--feature/-f`, `--tag`, `--platform/-p` | The same selectors as `argus run`; they **narrow** the suite (AND) |
| `--provider` | `auto` (default; detect) or `github`, `gitlab`, `jenkins`, `azure`, `generic`, `local` |
| `--config/-c` | Configuration file (same precedence as every other command) |
| `--dry-run` | Detect, resolve, validate, list — execute nothing (see below) |
| `--fail-fast / --no-fail-fast` | Stop after the first failure (default: run everything, `ci.execution.fail_fast`) |
| `--retry N` | Total attempts per test for transient failures (overrides `ci.retry`) |
| `--workers/-w N`, `--strategy` | Parallel workers and scheduling (`sequential` / `balanced`) |
| `--output-dir/-o` | Artifact directory (default `ci.artifacts.directory` = `argus-results`) |
| `--no-report` | Skip provider publishing (job summary / annotations) |
| `--no-artifacts` | Write no artifact directory at all (console + exit code only) |
| `--skip-preflight` | Skip pre-flight checks (not recommended) |
| `--verbose/-v`, `--quiet/-q` | Logging verbosity; the run log under `logs/argus/` is always DEBUG |

Console output is plain text: one line per test, no cursor control, colors only
when a TTY supports them. It reads the same in a log viewer.

```text
Argus CI
────────

Provider:  GitHub Actions
Suite:     pr
Branch:    feature/x
Commit:    abc1234
PR:        #142
Tests:     8
Workers:   1 (sequential)
Retries:   2 attempt(s) on timeout, device_error, connection_error, screenshot_capture_error
Run ID:    20260826-123240-3efb83

Running tests...

✓ 1/8 APP-001    Application reports ready (android)  3ms
✓ 2/8 MOV-001    Movie artwork appears (android)  77ms
✗ 3/8 MOV-004    Changing movies shows new artwork (android)  196ms
    Verification failed: image movie_456.png not found (confidence 0.31)
…
────────────────────────────
Argus CI Result

Passed:   7
Failed:   1
Skipped:  0
Flaky:    0

Policy:
  fail visual_regression: 1 visual regression(s)

Result: FAILED (exit 1: one or more tests failed)

Artifacts:
  /work/argus-results
```

### Selection precedence

Selection is deterministic and composes with AND:

1. **CLI selectors** (`--test`, `--feature`, `--tag`, `--platform`) narrow…
2. **the suite's selectors** (`ci.suites.<name>`), which narrow…
3. **the default**: every test.

Lists of the same kind intersect (features, platforms, IDs); tags accumulate
(a test must carry *all* of them, exactly like repeating `--tag` on
`argus run`); tag expressions are conjoined. A CLI selector never replaces a
suite selector: `argus ci run --suite smoke --tag player` runs tests that
belong to the smoke suite **and** carry the `player` tag. Disjoint selectors
select nothing (exit code 2), never everything.

### Dry run

`argus ci run --dry-run` detects the provider, loads configuration, resolves
the suite and tests, runs the pre-flight checks, and prints what *would* run:
tests per platform, workers, retry policy, artifact location. It executes no
test, writes no artifacts, and never touches baselines. Exit code `0` when
everything is ready, `3` when pre-flight fails, `2`/`4` for configuration or
test-definition errors.

## Configuration

Everything lives under `ci:` in your normal Argus configuration; every key is
optional. A complete, commented example: [`examples/ci/argus-ci.yml`](../examples/ci/argus-ci.yml).

```yaml
ci:
  enabled: true
  provider: auto                 # auto | github | gitlab | jenkins | azure | generic | local

  suites:
    pr:      { tags: [smoke] }
    merge:   { extends: pr, tags: [critical] }     # smoke AND critical
    nightly: { features: [Movies, Player] }

  retry:
    enabled: true
    max_attempts: 2              # total attempts (2 = one retry)
    on: [timeout, device_error, connection_error, screenshot_capture_error]

  execution:
    workers: 1
    strategy: balanced           # sequential | balanced
    fail_fast: false

  artifacts:
    enabled: true
    directory: argus-results
    retain_on_success: true
    save_comparisons: false      # keep actual/expected/diff for passing image verifies too

  policy:
    required: [pr]
    failures:          { action: fail }
    visual_regression: { action: fail }
    known_failure:     { action: warn }
    flaky:             { action: warn }

  known_failures:
    - test: MOV-002
      reason: "OCR flakiness tracked in ARGUS-123"
      platform: yocto            # optional: only on this platform

  reporting:
    summary: true                # provider job summary
    annotations: true            # provider annotations for failed tests
    max_annotations: 20
```

Invalid configuration fails before anything runs, with the offending path, the
value, and what is allowed (exit code 2):

```text
CONFIGURATION ERROR
Invalid configuration (argus.yml):
ci.retry.max_attempts
  Input should be a valid integer, unable to parse string as an integer [input_value='many']
```

> YAML 1.1 parses a bare `on:` key as the boolean `true`; Argus accepts both
> `on:` and `"on":` under `ci.retry`.

## Suites

A suite is a **selection policy**, not a second way to define tests. Each
suite lists the same selectors the CLI accepts — `tags`, `features`,
`platforms`, `tests` — and resolves to an engine `TestFilter`. `extends`
merges another suite's selectors first (lists are unioned; remember that tags
are ANDed, so `extends: pr` + `tags: [critical]` means *smoke and critical*).
Unknown suites and `extends` cycles are configuration errors.

## Retry policy

Retries are explicit and never silent:

- Disabled by default. Enable with `ci.retry.enabled: true` or `--retry N`.
- `max_attempts` is the **total** number of attempts per test.
- Only the categories in `on` are retried. Accepted names: `timeout`
  (`device_timeout`), `device_error` (`device_disconnected`), `connection_error`
  (`transient_transport_error`), `screenshot_capture_error`. Assertion
  failures and visual regressions are never retryable — retrying them hides
  real bugs.
- A test's own `retry:` block still applies; the effective policy is the
  more generous of the two (larger attempt count, union of categories).
- Every attempt is recorded (`attempt_history` in `report.json`) and every
  attempt's evidence is kept: attempt 1 in `tests/<ID>_<platform>/`, attempt
  *N* in `tests/<ID>_<platform>_attemptN/`.
- A test that fails and then passes is reported as **flaky** (`outcome:
  flaky_passed`, `flaky: true`, `initial_failure: <category>`) — it counts as
  passed, and the `flaky` policy decides whether that fails the pipeline.

```json
{
  "test_id": "MOV-004",
  "status": "passed",
  "outcome": "flaky_passed",
  "attempts": 2,
  "flaky": true,
  "initial_failure": "screenshot",
  "attempt_history": [
    {"attempt": 1, "status": "failed", "failure_category": "screenshot", "artifact_dir": "…/tests/MOV-004_android"},
    {"attempt": 2, "status": "passed"}
  ]
}
```

## Failure classification

Every failed test carries a structured `failure_category` derived from the
engine's own categories (no error-string matching):

| Category | Meaning |
|---|---|
| `assertion_failure` | A verification failed (non-image) |
| `visual_regression` | An image verification failed |
| `timeout` | A `wait_until` timed out |
| `device_error` | Device connection lost / unavailable |
| `connection_error` | Backend request failed |
| `screenshot_capture_error` | Screen capture failed |
| `test_failure` | Feature setup failed or an unclassified failure |
| `internal_error` | An action crashed |
| `configuration_error`, `test_definition_error`, `infrastructure_error`, `policy_failure` | Run-level errors |

Per-test **outcomes** in CI reports: `passed`, `flaky_passed`, `failed`,
`error`, `known_failure`, `skipped`, `not_run`. Tests that never executed
because the run stopped (fail-fast, cancellation, environment failure) are
`not_run` — never reported as passed or merely skipped.

## Quality policy

After execution the policy engine evaluates the results — it knows nothing
about CI providers:

| Rule | Triggers when | Default |
|---|---|---|
| `failures` | any test failed/errored (excluding visual regressions and known failures) | `fail` |
| `visual_regression` | any image verification failed | `fail` |
| `known_failure` | a test listed in `known_failures` failed | `warn` |
| `flaky` | a test passed only after a retry | `warn` |
| `required` | a required suite's selected tests did not all pass, or could not execute | always `fail` |

Actions: `fail` (exit code 1 for `failures`/`visual_regression`, 6 for the
others), `warn` (reported, pipeline passes), `ignore`. Known failures are
marked `KNOWN_FAILURE` in every report — they never disappear — and are
excluded from the `failures` rule. With `failures: {action: warn}` the run
status is still `failed` in `report.json`, but the exit code is 0.

## Artifacts

```text
argus-results/
├── report.json              canonical machine-readable report (schema_version 1)
├── junit.xml                JUnit XML with CI properties per test case
├── report.html              self-contained HTML report (context, badges, evidence)
├── tests/
│   ├── MOV-004_android/     actual.png expected.png diff.png logs.txt metadata.json …
│   └── MOV-004_android_attempt2/
├── logs/argus/argus.log     DEBUG-level JSON run log (secrets redacted)
└── metadata/
    ├── ci.json              normalized CI context
    ├── git.json             commit / branch / dirty flag (best effort)
    ├── environment.json     Argus + Python + OS versions, whitelisted variables
    └── preflight.json       pre-flight results
```

- The directory is created if missing. Argus-owned entries (the files and
  folders above) from a previous run are removed first; anything else in the
  directory is left alone. The project root, its parents, `/` and `$HOME` are
  refused as artifact directories.
- Artifacts are written even when tests fail, when the environment fails
  (metadata + preflight + `report.json`), and — best effort — when Argus
  itself crashes.
- All paths inside the directory come from sanitized components; a hostile
  test name or branch name cannot escape it.
- No secrets: only whitelisted environment facts are recorded; every JSON/log
  file passes through the same redaction as Argus logging.

### `report.json`

```json
{
  "schema_version": 1,
  "report": "argus-ci",
  "argus_version": "1.1.10",
  "run": {"run_id": "20260826-123240-3efb83", "status": "failed", "suite": "pr",
          "provider": "github", "started_at": "2026-08-26T12:32:40.123Z",
          "finished_at": "…", "duration": 0.906, "workers": 1, "strategy": "sequential",
          "retry": {"enabled": true, "max_attempts": 2, "on": ["timeout", "…"]},
          "selection": {"tags": ["smoke"], "suite": "pr"}, "error": null,
          "timings": {"plan": 0.004, "preflight": 0.025, "execution": 0.789, "reports": 0.002, "total": 0.906}},
  "ci":      {"provider": "github", "repository": "kireol/argus", "branch": "main", "commit_sha": "…", "pull_request": "142", "…": "…"},
  "summary": {"status": "failed", "policy_status": "failed", "total": 8, "passed": 7, "failed": 1,
              "errored": 0, "skipped": 0, "not_run": 0, "flaky": 0, "known_failures": 0,
              "visual_regressions": 1, "duration": 0.906},
  "tests":   [{"test_id": "MOV-004", "platform": "android", "status": "failed", "outcome": "failed",
               "failure_category": "visual_regression", "failure_message": "…", "attempts": 1,
               "artifacts": ["tests/MOV-004_android/actual.png", "…"], "attempt_history": []}],
  "artifacts": [{"path": "junit.xml", "kind": "report", "size": 2794}],
  "policy":  {"status": "failed", "violations": [{"rule": "visual_regression", "action": "fail",
              "message": "1 visual regression(s)", "tests": ["MOV-004 [android]"]}]},
  "preflight": [{"name": "Device fake_android", "passed": true, "required": true}]
}
```

`schema_version` only changes for backward-incompatible edits; new fields are
added without bumping it. Timestamps are UTC ISO 8601; durations come from a
monotonic clock. Run IDs are `YYYYMMDD-HHMMSS-<6 hex>` (random suffix, so two
runs in the same second never collide).

### JUnit

`junit.xml` maps `passed → testcase`, `failed → <failure>`, `error → <error>`,
`skipped/not_run → <skipped>`. Each `<testsuite>` carries `argus.run_id`,
`argus.provider`, `argus.suite`, `argus.commit`, `argus.branch` properties;
each `<testcase>` carries `attempts`, `outcome`, `failure_category`, `flaky`,
`known_failure`. Failure bodies include the failing step, its message, and the
evidence path — never binary data.

## Exit codes

| Code | Name | When |
|---:|---|---|
| 0 | `SUCCESS` | All required tests passed and the policy is satisfied (warnings allowed) |
| 1 | `TEST_FAILURE` | The `failures` or `visual_regression` policy failed |
| 2 | `CONFIGURATION_ERROR` | Invalid configuration, unknown suite/provider, empty selection |
| 3 | `ENVIRONMENT_ERROR` | Pre-flight failed, `setup` commands failed, devices unavailable |
| 4 | `TEST_DEFINITION_ERROR` | A test definition is invalid |
| 5 | `CI_ERROR` | Reports/artifacts/provider publishing could not be written |
| 6 | `POLICY_FAILURE` | A quality gate other than raw failures failed (`required`, `flaky`, `known_failure`) |
| 7 | `INTERNAL_ERROR` | Unexpected Argus error (stack trace in the run log) |
| 8 | `CANCELLED` | SIGINT/SIGTERM before the run finished |

`argus run` keeps its historical codes (`0/1/2/3`); the contract above applies
to `argus ci run` only. The code is centralized in `argus.ci.exit_codes.ExitCode`.

## GitHub Actions

### The action

```yaml
- uses: kireol/argus@v1        # the action lives at the repository root (action.yml)
  with:
    suite: pr
    config: argus.yml
    workers: 2
    retry: 2
    upload-artifacts: true
```

The action is deliberately thin: it installs Argus from the action's own
checkout (`install: true`, with optional `extras: browser,ocr`), invokes
`argus ci run` with the inputs mapped to flags, uploads `argus-results/`
with `actions/upload-artifact@v4` (`upload-artifacts`, `artifact-name`),
propagates the exit code, and exposes `status`, `exit-code`, `report-json`,
`junit-xml`, `report-html`, `output-dir` outputs (all absolute paths). Inputs:
`suite`, `config`, `provider` (default `github`), `workers`, `retry`, `tags`,
`platforms`, `output-dir`, `working-directory` (run from a subdirectory — in a
monorepo, relative paths in `argus.yml` resolve against it), `extra-args`,
`python-version`, `install`, `extras`. Put everything else in `argus.yml` —
action inputs only override what they name.

The `action.yml` at the repository root can be published as a standalone
`kireol/argus-action` repository without changes; pin whichever you use with a
tag.

### Job summary and annotations

When `GITHUB_ACTIONS=true` Argus:

- appends a Markdown summary to `$GITHUB_STEP_SUMMARY` (status, counts table,
  failed tests, visual regressions, known failures, flaky tests, policy,
  environment);
- prints `::error title=Argus test failed: <ID>::<message>` workflow commands
  for failed tests (at most `ci.reporting.max_annotations`, default 20, then
  one warning noting the truncation) plus one annotation per policy violation.

Both use GitHub's environment mechanisms only — no API calls, no token.
Richer integrations (Checks, PR comments) are extension points: providers
declare `supports_checks` / `supports_pr_comments` capabilities and reporters
are registered per provider name (`argus.ci.reporters.ReporterRegistry`).

### Complete workflow

```yaml
name: Argus

on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "0 3 * * *"

permissions:
  contents: read

jobs:
  argus:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Pick suite
        id: suite
        run: |
          case "${{ github.event_name }}" in
            pull_request) echo "name=pr" >> "$GITHUB_OUTPUT" ;;
            schedule)     echo "name=nightly" >> "$GITHUB_OUTPUT" ;;
            *)            echo "name=merge" >> "$GITHUB_OUTPUT" ;;
          esac
      - uses: kireol/argus@v1
        with:
          suite: ${{ steps.suite.outputs.name }}
          config: argus.yml
```

See [`examples/ci/github-workflow.yml`](../examples/ci/github-workflow.yml).
The repository's own workflow (`.github/workflows/argus-ci.yml`) exercises
the action end to end: a passing run, a failing run (exit code 1, evidence,
summary), and a configuration failure (exit code 2).

## Generic CI (CircleCI, Buildkite, TeamCity, Bitbucket, shell scripts …)

Nothing to install beyond Argus. Any environment with `CI=true` (or a known
vendor variable) is detected as `provider: generic`; branch/commit/build
numbers are picked up from the common variable names where present. Run:

```bash
pip install "argus[browser]"          # or whichever extras you need
argus ci run --suite pr
```

then archive `argus-results/` and feed `argus-results/junit.xml` to your CI's
test reporter. The exit code drives pass/fail.

## GitLab, Jenkins, Azure Pipelines

Detected automatically with normalized context (branch, commit, MR/PR number,
pipeline/build IDs, URLs). Publishing uses the generic reporter — JUnit, JSON
and HTML output work everywhere:

```yaml
# .gitlab-ci.yml
argus:
  script: argus ci run --suite pr
  artifacts:
    when: always
    paths: [argus-results/]
    reports:
      junit: argus-results/junit.xml
```

```groovy
// Jenkinsfile
sh 'argus ci run --suite pr'
junit 'argus-results/junit.xml'
archiveArtifacts artifacts: 'argus-results/**', allowEmptyArchive: true
```

```yaml
# azure-pipelines.yml
- script: argus ci run --suite pr
- task: PublishTestResults@2
  condition: always()
  inputs: { testResultsFiles: argus-results/junit.xml }
```

Adding a provider-specific reporter (e.g. GitLab MR notes) means implementing
`argus.ci.reporters.base.CIReporter` and registering it for the provider
name; the core never changes.

## Parallel execution

```bash
argus ci run --suite nightly --workers 2
```

- Each worker owns a **disjoint set of devices**; a test on platform *P* only
  runs on a worker holding a *P* device. With one device per platform, two
  workers run android and yocto side by side; with one device total, only one
  worker can do device work (the plan notes this and idles the others).
- `balanced` groups tests by feature (feature `setup`/`teardown` runs once per
  worker) and assigns groups to the least loaded eligible worker,
  deterministically. `sequential` is exactly `argus run`'s order.
- Pre-flight and configuration `setup` commands run **once** before workers
  start. `before_each` runs per test as usual.
- Per-test artifact directories are unique across workers; console lines are
  prefixed `w1`/`w2`; the run log records the worker.
- Each worker uses its own `RunSession` (device connections, backend client,
  asset cache). The **fake backend** is per-worker and isolated. A **real
  HTTP backend is shared state**: only parallelize tests that do not depend on
  the backend state other tests mutate.
- Device adapters are used by one worker at a time; the concurrency audit
  covers device state, artifacts, logs, report aggregation, and backend state.
  Adapters that talk to a single external service (e.g. one `adb` server)
  work because each device is bound to one worker.

## Cancellation

`SIGINT`/`SIGTERM` (a cancelled workflow, Ctrl-C) sets a cancellation token:
no further test starts, the in-flight test finishes and cleans up, devices
are released, reports are written with `status: cancelled` and the remaining
tests as `not_run`, and the process exits `8`. A second signal aborts the
in-flight test immediately.

## Observability

`report.json → run.timings` records `plan`, `preflight`, `setup`,
`execution`, `policy`, `reports`, `publish`, and `total` durations (monotonic
clock). Per-test durations and attempt durations are in `tests[]`. The run log
(`logs/argus/argus.log`, JSON lines, DEBUG) has every engine log line with
`run_id`, `test_id`, `platform` and `device` context.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Unknown suite 'x'` (exit 2) | Define it under `ci.suites` or check `--suite` spelling; the message lists the defined suites |
| `no tests match the given selection` (exit 2) | The suite and CLI selectors intersect to nothing; run with `--dry-run` to see the resolved filters |
| exit 3, `pre-flight checks failed` | See `metadata/preflight.json` and the console: missing assets, unreachable device/backend, missing OCR |
| exit 3, `setup failed: …` | A configuration `setup` command failed; its stdout/stderr is in the run log |
| `Unknown CI provider` | `ci.provider`/`--provider` must be `auto` or a registered name |
| Tests reported `not_run` | The run stopped early (fail-fast, cancellation, environment failure) — not a pass |
| Flaky tests fail the pipeline | `ci.policy.flaky.action: fail` is set; use `warn` to report only |
| No job summary on GitHub | `ci.reporting.summary` is false, or `GITHUB_STEP_SUMMARY` is unset (custom runners) |
| Too many annotations | Lower `ci.reporting.max_annotations` or disable `ci.reporting.annotations` |
| Only one worker runs with `--workers 4` | Not enough devices: each worker needs its own device per platform (see the plan notes) |
| Artifact directory refused | Do not point `ci.artifacts.directory` at the project root, `/`, or `$HOME` |
| `ci.retry.on` rejected | Only transient categories are retryable; see *Retry policy* |

## Design notes

- `argus run` and `argus ci run` share one engine. The CI layer only adds
  options the engine already exposes (`RunOptions.retry`, `RunOptions.cancel`,
  `RunOptions.results_dir`, `skip_setup`) and reads the engine's results.
- No provider logic in the engine or the policy engine: providers and
  reporters are registries (`argus.ci.providers`, `argus.ci.reporters`).
- Internal classes are not the API: `report.json`, JUnit, the configuration
  schema, the exit codes and `argus ci run` itself are the public contracts.
- Designed-for-later without redesign: changed-code selection (`--changed`)
  is another `TestFilter` source; distributed execution is another
  `Scheduler`; historical results consume `report.json`; device farms are
  device registries.
