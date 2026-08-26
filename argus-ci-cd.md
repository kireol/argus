# Argus 4 — First-Class CI/CD Integration
## Complete Implementation Specification for Claude Code

> **Purpose:** Implement first-class, CI/CD-native support in the Argus test automation framework.
>
> **Repository:** https://github.com/kireol/argus
>
> **Primary goal:** Make Argus a CI/CD-aware test execution platform without coupling the core test engine to any specific CI provider. The implementation must be performant, scalable, maintainable, testable, and easy to extend to additional CI systems, reporters, policies, and execution backends.

---

# 1. Role and Operating Instructions

You are an expert software architect and senior implementation engineer.

You are modifying an existing production-oriented open-source repository, not creating a greenfield application.

Before changing code:

1. Inspect the entire repository.
2. Understand the current architecture, package structure, CLI, service layer, test runner, result model, device abstractions, adapters, configuration system, reporting system, and MCP integration.
3. Identify existing functionality that overlaps with this specification.
4. Reuse existing abstractions whenever they are appropriate.
5. Do not create duplicate implementations of functionality Argus already provides.
6. Preserve all existing behavior unless this specification explicitly changes it.
7. Follow the repository's existing language, style, dependency, naming, and testing conventions.
8. Do not introduce a heavyweight dependency when a small internal implementation is sufficient.
9. Do not hard-code GitHub-specific behavior into the core execution engine.
10. Do not make CI support require GitHub Actions. GitHub should be the first native integration, not the architectural center of the system.

After inspecting the repository, produce a short implementation plan internally, then implement the feature completely.

Do not stop after creating scaffolding. The resulting implementation must be functional end-to-end.

---

# 2. Product Definition

Argus currently supports test execution through its normal CLI and existing execution architecture.

Add a first-class CI/CD execution layer.

The new primary command is:

```bash
argus ci run
```

Normal local execution remains:

```bash
argus run
```

These commands must share the same underlying test execution engine.

The CI layer is responsible for:

- CI environment detection
- CI metadata normalization
- CI-specific configuration
- suite selection
- test selection
- retry policy
- CI-oriented execution behavior
- artifact organization
- machine-readable result publication
- CI provider reporting
- quality gates
- normalized exit codes

The CI layer must NOT duplicate the actual test execution engine.

---

# 3. Architectural Principle

The architecture must be:

```text
                    Argus Test Engine
                           ▲
                           │
                 ┌─────────┴─────────┐
                 │                   │
            Local CLI             CI Engine
             argus run           argus ci run
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
                 Context          Policy          Reporting
                    │                │                │
             CI detection       Quality gates    Provider adapters
                                                     │
                                      ┌──────────────┼──────────────┐
                                      │              │              │
                                   GitHub         GitLab        Jenkins
```

The CI layer is an orchestration layer over existing Argus capabilities.

Do NOT implement:

```text
argus run
argus ci run
```

as two independent runners.

---

# 4. Required CLI

Implement:

```bash
argus ci run
```

The command should support, where compatible with the existing CLI:

```bash
argus ci run
argus ci run --suite smoke
argus ci run --suite pr
argus ci run --tag smoke
argus ci run --feature movies
argus ci run --platform android
argus ci run --dry-run
argus ci run --config argus.yml
```

Do not arbitrarily duplicate every existing `argus run` option.

Instead:

- identify the existing CLI argument system
- reuse existing selectors/options where practical
- add CI-specific options only where they have meaningful semantics

Recommended options:

```text
--suite <name>
--provider <provider>
--config <path>
--dry-run
--no-report
--no-artifacts
--fail-fast
--retry <count>
--workers <count>
--verbose
```

Only implement options that fit the existing architecture.

The command must produce useful `--help` documentation.

---

# 5. CI Provider Detection

Implement a provider-neutral CI context abstraction.

Conceptually:

```python
CIContext
```

or the equivalent in the repository's language.

It should contain normalized fields such as:

```text
provider
repository
repository_url
branch
commit
commit_sha
pull_request
workflow
job
run_id
run_number
actor
event
workspace
base_branch
head_branch
```

Not every CI system provides every field.

Missing values must be represented cleanly rather than fabricated.

Example:

```yaml
ci:
  provider: github
  repository: kireol/argus
  branch: main
  commit: abc123
  pull_request: 142
  workflow: CI
  job: visual-tests
  run_id: 123456
```

---

# 6. Provider Detection Rules

Implement provider detection through dedicated provider adapters/detectors.

At minimum support:

## GitHub Actions

Detect using:

```text
GITHUB_ACTIONS=true
```

and consume standard GitHub environment variables.

Relevant variables include, where available:

```text
GITHUB_REPOSITORY
GITHUB_REF
GITHUB_REF_NAME
GITHUB_SHA
GITHUB_WORKFLOW
GITHUB_JOB
GITHUB_RUN_ID
GITHUB_RUN_NUMBER
GITHUB_ACTOR
GITHUB_EVENT_NAME
GITHUB_HEAD_REF
GITHUB_BASE_REF
GITHUB_WORKSPACE
GITHUB_SERVER_URL
```

## GitLab CI

Detect:

```text
GITLAB_CI=true
```

Use standard GitLab variables where available.

## Jenkins

Detect:

```text
JENKINS_URL
BUILD_ID
BUILD_NUMBER
JOB_NAME
```

or other canonical Jenkins indicators.

## Azure DevOps

Detect standard Azure DevOps CI variables.

## Generic CI

If a recognized generic CI indicator exists, use:

```text
provider = generic
```

## Local

When no CI environment is detected:

```text
provider = local
```

Do not fail merely because the command is run outside CI.

---

# 7. Provider Abstraction

Create a provider interface/protocol.

Conceptually:

```python
class CIProvider(Protocol):
    name: str

    def detect(self, environment) -> bool:
        ...

    def collect_context(self, environment) -> CIContext:
        ...

    def publish_summary(self, result, context) -> None:
        ...

    def publish_annotations(self, result, context) -> None:
        ...
```

Adapt the exact design to the repository's architecture.

Do not force every provider to implement features it does not support.

Use capability-based behavior where appropriate.

For example:

```text
supports_summary
supports_annotations
supports_checks
supports_artifacts
supports_pr_comments
```

---

# 8. Generic CI Provider

A generic provider must always be available.

It should:

- collect normalized metadata when possible
- write local reports
- produce standard exit codes
- never require provider-specific APIs

This ensures Argus works with:

- CircleCI
- Buildkite
- TeamCity
- Bitbucket Pipelines
- custom internal CI
- shell-based CI systems

without requiring an Argus plugin immediately.

---

# 9. Configuration

Extend the existing Argus configuration system rather than creating an unrelated configuration mechanism.

Support a structure conceptually similar to:

```yaml
ci:
  enabled: true

  provider: auto

  suites:
    pr:
      tags:
        - smoke

    merge:
      tags:
        - smoke
        - critical

    nightly:
      tags:
        - regression
        - visual

  retry:
    enabled: true
    max_attempts: 2

    on:
      - device_timeout
      - connection_error
      - screenshot_capture_error

  execution:
    workers: 1
    strategy: balanced

  artifacts:
    enabled: true
    directory: argus-results

  policy:
    required:
      - smoke

    failures:
      action: fail

    visual_regression:
      action: fail

    known_failure:
      action: warn

    flaky:
      action: warn
```

The exact schema must fit the existing Argus configuration conventions.

Do not force users to create a CI configuration if sensible defaults exist.

---

# 10. Configuration Validation

Invalid CI configuration must fail early and clearly.

Examples:

```text
Unknown suite
Invalid retry count
Invalid policy action
Unknown provider
Invalid worker count
Malformed YAML
```

Error messages should identify:

- the configuration path
- the invalid value
- what values are allowed

Example:

```text
Invalid configuration: ci.retry.max_attempts

Expected an integer >= 0.
Received: "many"
```

Do not allow configuration errors to masquerade as test failures.

---

# 11. Test Suites

Add named CI suites.

Example:

```yaml
ci:
  suites:
    pr:
      tags:
        - smoke

    merge:
      tags:
        - smoke
        - critical

    nightly:
      tags:
        - regression
        - visual
```

A suite is a selection policy, not a second test-definition system.

It should ultimately resolve into the existing Argus test selection mechanism.

Support composition where appropriate.

For example:

```yaml
ci:
  suites:
    pr:
      tags:
        - smoke

    merge:
      extends: pr
      tags:
        - critical
```

Only implement inheritance if it fits naturally with the existing configuration system.

Do not over-engineer v1.

---

# 12. Test Selection Precedence

Define and document deterministic precedence.

Recommended order:

1. Explicit CLI selectors
2. Explicit suite selectors
3. CI defaults
4. Repository/default test selection

If the user specifies:

```bash
argus ci run --suite smoke --tag player
```

the behavior must be deterministic and documented.

Do not silently replace one selector with another.

Use the existing Argus selection semantics wherever possible.

---

# 13. Dry Run

`argus ci run --dry-run` must:

- detect CI context
- load configuration
- resolve suite
- resolve tests
- validate execution requirements
- display what would run
- display workers/retry configuration
- display output/artifact locations
- NOT execute tests
- NOT mutate baselines
- NOT publish CI success/failure

Example output:

```text
Argus CI Dry Run

Provider: GitHub Actions
Suite: pr
Commit: abc123
Tests: 18
Workers: 4
Retry attempts: 2

Selected tests:
  ✓ smoke/login
  ✓ smoke/navigation
  ✓ smoke/player
  ...

Artifacts:
  argus-results/

No tests were executed.
```

---

# 14. CI Exit Codes

Define a stable public exit-code contract.

Recommended:

```text
0  Success — all required tests passed
1  Test failure — one or more required tests failed
2  Configuration error
3  Environment/device unavailable
4  Invalid test definition
5  CI infrastructure/reporting failure
6  Quality-policy failure
7  Internal Argus error
```

If the repository already has exit codes, reconcile with them rather than blindly replacing them.

The final contract must be centralized in one place.

Do not scatter numeric literals throughout the codebase.

Example:

```python
class ExitCode:
    SUCCESS = 0
    TEST_FAILURE = 1
    CONFIGURATION_ERROR = 2
    ENVIRONMENT_ERROR = 3
    TEST_DEFINITION_ERROR = 4
    CI_ERROR = 5
    POLICY_FAILURE = 6
    INTERNAL_ERROR = 7
```

Adapt naming to the repository's language/style.

---

# 15. Failure Classification

Every failure should be classified where possible.

At minimum:

```text
test_failure
assertion_failure
visual_regression
device_error
connection_error
timeout
configuration_error
test_definition_error
infrastructure_error
policy_failure
internal_error
```

This classification is critical for retry and CI reporting.

Do not infer failure categories from arbitrary error-message string matching if the existing architecture can provide structured exceptions/results.

Prefer structured error types.

---

# 16. Retry Policy

Retries must be explicit and controlled.

Default behavior should NOT retry ordinary assertion failures.

Recommended retryable categories:

```text
device_timeout
connection_error
device_disconnected
screenshot_capture_error
transient_transport_error
```

Non-retryable by default:

```text
assertion_failure
visual_regression
invalid_test
configuration_error
```

Configuration:

```yaml
ci:
  retry:
    enabled: true
    max_attempts: 2
    on:
      - connection_error
      - device_timeout
```

Interpret:

```text
max_attempts: 2
```

as at most two total attempts or clearly define it as retries. Pick one semantic and document it. Prefer `max_attempts` = total attempts.

Retry behavior must:

- preserve the original failure
- record every attempt
- not overwrite evidence from previous attempts
- report the final classification
- indicate whether a test was flaky

Example result:

```json
{
  "test": "player-controls",
  "status": "passed",
  "attempts": 2,
  "flaky": true,
  "initial_failure": "device_timeout"
}
```

---

# 17. Artifact System

Standardize CI output.

Default directory:

```text
argus-results/
```

Structure:

```text
argus-results/
├── report.json
├── junit.xml
├── report.html
├── screenshots/
│   ├── expected/
│   ├── actual/
│   └── diff/
├── logs/
│   ├── device/
│   ├── application/
│   └── argus/
└── metadata/
    ├── ci.json
    ├── git.json
    └── environment.json
```

Do not require every subdirectory to exist if unused.

Artifacts must be deterministic and safe for CI.

Do not put credentials, tokens, secrets, or private environment values into metadata.

---

# 18. Artifact Lifecycle

Artifacts must be written even when tests fail, wherever technically possible.

If an individual test fails:

- capture its evidence
- continue according to execution policy
- include the evidence in final output

If the entire environment fails:

- preserve logs
- preserve metadata
- report the environment failure

If Argus itself crashes:

- attempt best-effort finalization
- do not hide the internal error

---

# 19. Result Model

Create or extend a structured CI run result.

Conceptually:

```text
CIRunResult
  run_id
  status
  provider
  suite
  started_at
  finished_at
  duration
  total
  passed
  failed
  skipped
  errored
  flaky
  policy_status
  tests[]
  artifacts
  context
```

Each test result should contain:

```text
test_id
name
status
duration
attempts
failure_category
failure_message
screenshots
logs
metadata
```

Do not duplicate the existing Argus test-result model if one already exists.

Extend it.

The result model should be serializable to JSON.

---

# 20. JUnit Output

Continue supporting JUnit XML.

Ensure CI execution produces valid JUnit.

Map:

```text
passed -> testcase
failed -> failure
error -> error
skipped -> skipped
```

Where possible include:

- test duration
- failure message
- failure category
- relevant stdout/stderr

Do not embed enormous binary artifacts into JUnit.

---

# 21. JSON Output

`report.json` should be the canonical machine-readable Argus report.

Include:

```json
{
  "schema_version": 1,
  "argus_version": "...",
  "run": {},
  "ci": {},
  "summary": {},
  "tests": [],
  "artifacts": [],
  "policy": {}
}
```

The schema must be versioned.

Never silently change the structure of `report.json` in a backward-incompatible way.

---

# 22. Schema Versioning

Introduce a result schema version.

Example:

```text
schema_version = 1
```

If breaking changes are ever required:

```text
schema_version = 2
```

Keep serialization code separate from internal domain objects.

Do not make internal Python/class structures become an accidental public API.

---

# 23. HTML Report

Reuse the existing HTML reporting functionality where possible.

The CI report should prominently display:

- overall status
- provider
- branch
- commit
- PR number
- suite
- duration
- pass/fail/skip counts
- flaky tests
- policy status
- failed tests
- screenshots
- visual diffs
- logs

Do not duplicate the existing HTML renderer unless necessary.

Extend it.

---

# 24. GitHub Actions Integration

GitHub Actions is the first-class reference implementation.

Provide an official action:

```yaml
- uses: kireol/argus-action@v1
  with:
    suite: pr
```

The action should be thin.

It must NOT contain the core Argus execution logic.

Its responsibilities should be limited to:

- installing/locating Argus
- invoking `argus ci run`
- passing relevant inputs
- exposing artifacts/results appropriately
- integrating with GitHub-specific features

The action should be versioned independently from the Argus CLI where practical.

---

# 25. GitHub Action Inputs

Support useful inputs such as:

```yaml
with:
  suite: pr
  config: argus.yml
  provider: github
  workers: 4
  retry: 2
  upload-artifacts: true
```

Do not expose every internal Argus setting as an action input.

Users should be able to put most configuration in `argus.yml`.

Action inputs should override configuration only where explicitly documented.

---

# 26. GitHub Job Summary

When running in GitHub Actions, generate a GitHub Actions job summary.

Example:

```text
# Argus Test Results

❌ 2 failed / 42 tests

| Status | Count |
|---|---:|
| Passed | 38 |
| Failed | 2 |
| Skipped | 2 |
| Flaky | 1 |

## Failed Tests

- player-controls
- movie-details

## Visual Regressions

- player-controls

## Environment

Android
Device: Pixel_8
Commit: abc123
```

Use GitHub's supported environment mechanisms.

Do not require the GitHub API for the basic job summary.

---

# 27. GitHub Annotations

Where appropriate, emit GitHub workflow annotations for failed tests.

Example concept:

```text
::error title=Argus Test Failed::player-controls failed
```

Do not generate hundreds of noisy annotations for the same failure.

Implement sensible limits.

Make annotation generation provider-specific.

---

# 28. GitHub Checks / PR Integration

Design the provider interface so richer GitHub integration can be added.

If implementing GitHub Checks in v1:

- use secure authentication
- never print tokens
- make permissions explicit
- fail gracefully when credentials are unavailable
- do not require API access for local execution

If Checks are deferred, ensure the architecture has a clean extension point.

---

# 29. GitHub Artifacts

Argus should make it easy for the workflow to upload:

```text
argus-results/
```

Do not make the Argus CLI depend on GitHub's artifact service.

The GitHub Action may use:

```text
actions/upload-artifact
```

or the current supported equivalent.

Keep provider-specific artifact upload outside the core Argus engine.

---

# 30. GitLab / Jenkins / Azure Support

Implement provider detection and normalized context.

At minimum, generic JUnit/JSON/HTML output must work.

Provider-specific publishing should use adapters.

Do not create a huge implementation for each provider in v1.

The architecture must make adding:

```text
GitLabReporter
JenkinsReporter
AzureReporter
```

straightforward.

---

# 31. Quality Policy Engine

Create a provider-neutral policy layer.

Conceptually:

```text
Test Results
     │
     ▼
Policy Engine
     │
     ├── required tests
     ├── failure policy
     ├── visual regression policy
     ├── known failures
     └── flaky policy
     │
     ▼
PolicyResult
```

Example:

```yaml
ci:
  policy:
    failures:
      action: fail

    visual_regression:
      action: fail

    known_failure:
      action: warn

    flaky:
      action: warn
```

The policy engine must not know anything about GitHub, GitLab, Jenkins, etc.

---

# 32. Required Tests

Support the concept of required tests/suites.

Example:

```yaml
ci:
  policy:
    required:
      - smoke
```

If a required suite cannot execute because of configuration/environment problems, the CI result must not be incorrectly reported as successful.

---

# 33. Known Failures

Design an extension point for known failures.

A known failure should be distinguishable from a new failure.

Do not build a complicated issue-management integration yet.

A simple configuration mechanism is sufficient for v1 if the existing Argus architecture supports it.

Example conceptual configuration:

```yaml
known_failures:
  - test: player-controls
    reason: "Known upstream issue"
```

Known failures must never silently disappear from reports.

They should be marked:

```text
KNOWN_FAILURE
```

and policy determines whether they fail the pipeline.

---

# 34. Flaky Test Detection

Retry history should allow Argus to identify:

```text
flaky = true
```

when:

```text
attempt 1 -> failed
attempt 2 -> passed
```

The report should distinguish:

```text
PASS
FLAKY PASS
FAIL
```

Do not automatically suppress flaky failures unless policy explicitly says so.

---

# 35. Parallel Execution Architecture

Design CI execution to support workers.

Conceptually:

```text
CIRunner
   │
   ▼
Test Scheduler
   │
   ├── Worker 1
   ├── Worker 2
   ├── Worker 3
   └── Worker 4
```

Do not introduce concurrency into code that assumes single-threaded execution without auditing it first.

Pay particular attention to:

- device state
- temporary files
- screenshots
- logs
- report aggregation
- backend state
- test isolation

Tests must not accidentally write to the same artifact paths.

---

# 36. Scheduling Strategy

Implement a scheduler abstraction.

At minimum:

```text
sequential
balanced
```

`sequential` preserves current behavior.

`balanced` attempts to distribute tests reasonably across workers.

Do not build a distributed cluster scheduler in v1.

However, design interfaces so distributed execution can be added later.

---

# 37. Device Isolation

If multiple workers use devices:

- each device must be exclusively assigned while a test is running
- device selection must be deterministic
- device failures must not corrupt other workers
- artifacts must identify the device
- cleanup must occur even after failure

Do not assume all existing adapters are concurrency-safe.

Audit them.

If a device adapter cannot safely run concurrently, document and enforce that limitation.

---

# 38. Performance Requirements

The implementation must not significantly slow down local test execution.

Requirements:

- CI context detection should be O(1)
- configuration loading should happen once per run
- provider objects should be initialized once
- result aggregation should be incremental
- avoid repeatedly parsing the same YAML/configuration
- avoid loading entire screenshot binaries into memory unnecessarily
- avoid retaining every log line indefinitely in memory
- use streaming/file-backed artifacts where appropriate

For large test suites:

```text
10,000 tests
```

must not require all raw logs/screenshots to remain resident in memory.

---

# 39. Scalability

Design for:

```text
10 tests
100 tests
1,000 tests
10,000+ tests
```

without architectural changes.

Avoid:

- global mutable state
- provider-specific logic in test execution
- hard-coded suite names
- hard-coded CI variables scattered across the project
- hard-coded report paths
- giant conditional chains

Prefer:

- interfaces
- registries
- dependency injection where appropriate
- structured domain models
- isolated provider adapters
- deterministic configuration

---

# 40. Thread Safety

Audit any shared services used by concurrent CI workers.

Do not assume existing classes are thread-safe.

Where necessary:

- isolate per-worker state
- use locks only where unavoidable
- prefer immutable run metadata
- avoid global caches containing mutable execution state

Do not use a global singleton for the current CI run.

---

# 41. Logging

CI logs must be useful but not excessively verbose.

Use structured log levels:

```text
DEBUG
INFO
WARNING
ERROR
```

At INFO level, show:

- CI provider
- suite
- number of tests
- workers
- retry configuration
- progress
- summary
- artifact location

At DEBUG level, provide deeper execution details.

Never log:

- access tokens
- secrets
- passwords
- authorization headers
- private credentials

---

# 42. CI Environment Metadata

Write:

```text
metadata/ci.json
```

and:

```text
metadata/git.json
metadata/environment.json
```

when available.

Do not dump the entire process environment.

Only whitelist safe variables.

This is important because CI environments commonly contain secrets.

---

# 43. Security

Security requirements:

1. Never log secrets.
2. Never serialize the entire environment.
3. Never expose CI tokens in reports.
4. Never echo GitHub/GitLab authentication headers.
5. Sanitize command output where needed.
6. Avoid shell injection when constructing commands.
7. Validate paths supplied through configuration.
8. Prevent artifact paths from escaping the configured output directory.
9. Do not execute arbitrary configuration values as shell commands unless the existing Argus architecture explicitly supports that feature.
10. Treat CI metadata as untrusted input.

---

# 44. Path Safety

Artifact paths must remain within the configured output directory.

Reject or normalize:

```text
../../outside
```

Do not allow a malicious test name to create arbitrary filesystem paths.

Sanitize:

```text
test names
device names
suite names
branch names
commit metadata
```

when converting them into filenames.

---

# 45. Backward Compatibility

Existing commands must continue to work.

At minimum verify:

```bash
argus run
argus run --tag smoke
argus run --feature movies
argus run --platform android
argus --help
```

Existing MCP functionality must continue working.

Existing adapters must continue working.

Existing reports must remain compatible unless there is a deliberate schema migration.

---

# 46. Testing Strategy

This feature requires extensive automated tests.

Add unit tests for:

## CI detection

```text
GitHub detected
GitLab detected
Jenkins detected
Azure detected
generic detected
local detected
unknown environment
```

## Context parsing

Verify correct normalization of:

- branch
- commit
- PR
- workflow
- job
- run ID

## Configuration

Test:

- valid configuration
- malformed YAML
- invalid provider
- invalid suite
- invalid retry configuration
- invalid policy
- defaults

## Exit codes

Test every failure category.

## Retry

Test:

```text
pass first attempt
fail first/pass second
fail all attempts
non-retryable failure
```

## Artifact generation

Verify:

```text
report.json
junit.xml
report.html
metadata
screenshots
logs
```

## Policy

Test:

```text
required suite passes
required suite fails
known failure
flaky test
visual regression
```

## Provider reporting

Mock provider-specific publishing.

Do not require actual GitHub API access in normal unit tests.

---

# 47. Integration Tests

Add integration tests for:

```bash
argus ci run --dry-run
```

and at least one realistic end-to-end CI execution path using a controlled/mock adapter.

Do not make the standard test suite dependent on physical devices or external CI services.

Device-specific tests should remain separately marked if the repository already supports such categorization.

---

# 48. GitHub Action Testing

Test the action with representative workflows.

At minimum verify:

```text
successful run
test failure
configuration failure
artifact generation
job summary
exit code propagation
```

If the repository has no existing action test infrastructure, add the smallest maintainable mechanism.

---

# 49. Documentation

Update the project documentation.

At minimum add:

```text
docs/ci-cd.md
```

or the repository's existing documentation equivalent.

Document:

1. What Argus CI is
2. `argus ci run`
3. CI configuration
4. Suites
5. Retry policies
6. Artifacts
7. Exit codes
8. GitHub Actions
9. Generic CI usage
10. GitLab/Jenkins/Azure support
11. Parallel execution
12. Troubleshooting

Add a complete GitHub Actions example.

Example conceptual workflow:

```yaml
name: Argus

on:
  pull_request:
  push:
    branches:
      - main

jobs:
  argus:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: kireol/argus-action@v1
        with:
          suite: pr
```

Adjust this to the actual repository's installation/runtime requirements.

---

# 50. Example Configuration

Add a realistic example configuration to the repository.

For example:

```text
examples/ci/argus-ci.yml
```

It should demonstrate:

- PR suite
- merge suite
- nightly suite
- retry
- artifacts
- policy

Keep it simple enough for a new user to understand.

---

# 51. CLI UX

The CI command should feel polished.

Example:

```text
$ argus ci run --suite pr

Argus CI
────────

Provider: GitHub Actions
Suite:    pr
Commit:   abc1234
PR:       #142

Tests:    24
Workers:  4
Retries:  2

Running tests...

✓ login
✓ navigation
✓ movies
✓ player
✗ subtitles
✓ settings

────────────────────────────
Argus CI Result

Passed:  23
Failed:   1
Skipped:  0
Flaky:    0

Result: FAILED

Artifacts:
  argus-results/
```

Avoid excessive ANSI decoration that makes logs difficult to read in CI.

Ensure output remains readable without a TTY.

---

# 52. Non-TTY Support

CI often runs without an interactive terminal.

Argus must:

- disable interactive prompts
- avoid cursor-control sequences
- avoid requiring user input
- produce plain logs
- respect existing color/no-color settings

Never hang waiting for input in CI.

---

# 53. Fail-Fast

If the existing test engine supports fail-fast, expose it consistently.

Do not make fail-fast the default for CI unless the current Argus philosophy already requires it.

Default should generally allow the full suite to produce useful diagnostic information.

---

# 54. Cancellation

Handle CI cancellation gracefully.

Examples:

```text
SIGTERM
SIGINT
```

On cancellation:

1. stop scheduling new tests
2. allow current cleanup where possible
3. release devices
4. flush reports
5. write partial result metadata
6. exit with a non-success status

Do not leave devices locked or processes orphaned.

---

# 55. Partial Results

If a CI run is interrupted, the report should indicate:

```text
status: cancelled
```

and distinguish:

```text
not_run
```

from:

```text
skipped
```

Do not report unexecuted tests as passed.

---

# 56. Time Handling

Use UTC internally.

Store timestamps in ISO 8601 format.

Example:

```text
2026-08-26T12:34:56.123Z
```

Duration should use a monotonic clock for runtime measurement.

Do not calculate durations using wall-clock timestamps.

---

# 57. IDs

Generate a unique run ID.

Example:

```text
20260826-123456-abc123
```

or an existing repository-compatible UUID strategy.

The run ID must not rely exclusively on timestamps.

Use a collision-safe mechanism.

---

# 58. Provider Registry

Use a provider registry/factory rather than a large `if/elif` block.

Conceptually:

```python
providers.register(GitHubProvider())
providers.register(GitLabProvider())
providers.register(JenkinsProvider())
providers.register(AzureProvider())
providers.register(GenericProvider())
```

Detection:

```text
ProviderRegistry.detect(environment)
```

This makes future providers easy to add.

---

# 59. Reporter Registry

Likewise:

```text
ReporterRegistry
```

should allow:

```text
GitHubReporter
GitLabReporter
JenkinsReporter
GenericReporter
```

without modifying core execution logic.

---

# 60. Dependency Injection

Where appropriate, inject:

```text
CIContext
Provider
Reporter
PolicyEngine
ArtifactManager
ResultStore
Clock
Environment
```

This dramatically improves testing.

Do not overuse dependency injection for trivial objects.

Follow the existing project's architectural style.

---

# 61. No Provider Leakage

Core Argus test execution must never contain logic such as:

```python
if github:
    ...
elif gitlab:
    ...
```

Provider-specific logic belongs in provider/reporting modules.

The core engine should know only about:

```text
CIContext
CIResult
PolicyResult
ArtifactSet
```

---

# 62. Error Handling

Errors must be categorized.

Do not:

```python
except Exception:
    return 1
```

throughout the code.

Instead:

- preserve the underlying exception
- classify it
- log useful information
- map it to the correct exit code
- finalize artifacts where possible

Do not swallow unexpected exceptions.

---

# 63. Observability

Add enough instrumentation to diagnose slow CI runs.

At minimum measure:

```text
total run duration
test duration
setup duration
teardown duration
retry duration
artifact generation duration
report generation duration
```

Avoid adding a telemetry service or external analytics system.

Local timing data is sufficient.

---

# 64. Performance Regression Testing

Where practical, add benchmarks for:

- loading large test suites
- result aggregation
- JSON serialization
- JUnit generation
- artifact generation

The CI layer should add negligible overhead when not used.

---

# 65. API Stability

Treat these as public-facing contracts:

```text
argus ci run
exit codes
report.json schema
JUnit output
configuration schema
```

Avoid unnecessary breaking changes.

Document versioned behavior.

---

# 66. Future Extension Points

The architecture should make the following possible later without redesign:

```text
argus ci run --changed
```

Changed-code-aware test selection.

```text
argus ci run --distributed
```

Distributed execution.

```text
Argus Cloud
```

Centralized historical results.

AI-assisted:

```text
test selection
failure diagnosis
flaky test detection
test generation
```

Device farms.

Remote workers.

Do NOT implement these now unless required by existing code dependencies.

Just ensure the architecture does not prevent them.

---

# 67. Important Non-Goals

Do NOT:

- rewrite the existing test engine
- replace the existing CLI architecture unnecessarily
- make GitHub the core architecture
- build a distributed test farm in v1
- build an external cloud service
- add AI functionality to CI v1
- add unnecessary dependencies
- require internet access for local CI execution
- require GitHub credentials for normal execution
- make all CI providers feature-identical
- duplicate reporting systems
- duplicate configuration systems

---

# 68. Suggested Package Structure

Adapt this to the repository rather than blindly creating it.

Conceptually:

```text
argus/
├── ci/
│   ├── __init__.py
│   ├── context.py
│   ├── runner.py
│   ├── policy.py
│   ├── retry.py
│   ├── scheduler.py
│   ├── artifacts.py
│   ├── exit_codes.py
│   ├── result.py
│   ├── providers/
│   │   ├── base.py
│   │   ├── github.py
│   │   ├── gitlab.py
│   │   ├── jenkins.py
│   │   ├── azure.py
│   │   └── generic.py
│   └── reporters/
│       ├── base.py
│       ├── github.py
│       ├── gitlab.py
│       ├── jenkins.py
│       ├── azure.py
│       └── generic.py
```

The exact location must follow the existing project structure.

---

# 69. Implementation Order

Implement in this order unless repository inspection reveals a better dependency order.

## Phase 1

1. Inspect architecture.
2. Identify existing result/config/CLI/report abstractions.
3. Add CI domain models.
4. Add exit codes.
5. Add CI context detection.
6. Add generic provider.
7. Add `argus ci run`.
8. Integrate with existing execution engine.

## Phase 2

9. Add CI configuration.
10. Add suites.
11. Add retry policy.
12. Add failure classification.
13. Add artifact manager.
14. Add JSON schema.
15. Extend JUnit/HTML output.

## Phase 3

16. Add provider registry.
17. Add GitHub provider.
18. Add GitHub job summary.
19. Add annotations.
20. Add GitHub Action.
21. Add GitHub workflow examples.

## Phase 4

22. Add policy engine.
23. Add flaky-test handling.
24. Add known-failure support.
25. Add quality gates.

## Phase 5

26. Add worker/scheduler abstraction.
27. Add safe parallel execution.
28. Add concurrency tests.
29. Optimize performance.

## Phase 6

30. Documentation.
31. Integration tests.
32. Full regression test suite.
33. Update examples.
34. Final architecture cleanup.

---

# 70. Acceptance Criteria

The implementation is complete only when all of the following are true.

## CLI

```bash
argus ci run
```

works.

```bash
argus ci run --help
```

provides useful documentation.

## Local

Running outside CI works.

```text
provider = local
```

## GitHub

Running under GitHub Actions detects GitHub automatically.

## GitLab

GitLab is detected automatically.

## Jenkins

Jenkins is detected automatically.

## Generic

Unknown CI systems can execute Argus successfully.

## Configuration

CI suites and retry policies work from configuration.

## Results

The run produces:

```text
report.json
junit.xml
report.html
```

where enabled.

## Artifacts

Screenshots/logs/evidence are preserved.

## Exit codes

Failures map to stable documented exit codes.

## Retry

Only configured transient failures retry.

## Flaky

Retry-then-pass is recorded as flaky.

## Policy

Quality gates can fail the pipeline independently of raw test execution.

## GitHub

GitHub Actions receives a useful job summary.

## Security

No secrets are written to reports or logs.

## Performance

CI mode does not introduce meaningful overhead outside CI execution.

## Compatibility

Existing Argus tests and CLI functionality continue to work.

## Tests

Automated tests cover the new functionality.

## Documentation

A new user can configure Argus CI from documentation without reading source code.

---

# 71. Definition of Done

Before declaring completion:

1. Run the existing full test suite.
2. Run all new CI unit tests.
3. Run integration tests.
4. Run CLI smoke tests.
5. Test local `argus ci run`.
6. Test `--dry-run`.
7. Test simulated GitHub environment variables.
8. Test simulated GitLab environment variables.
9. Test simulated Jenkins environment variables.
10. Test generic CI.
11. Test success exit code.
12. Test assertion failure.
13. Test configuration failure.
14. Test environment failure.
15. Test retry.
16. Test flaky detection.
17. Test artifact creation.
18. Validate JSON.
19. Validate JUnit XML.
20. Render/test HTML report.
21. Verify no secret leakage.
22. Test concurrent execution if workers are implemented.
23. Test cancellation/cleanup where feasible.
24. Run formatting/linting/type checking used by the repository.
25. Update documentation.
26. Update examples.
27. Review all new dependencies.
28. Review for provider-specific logic leaking into core code.
29. Review for global mutable state.
30. Review for filesystem/path traversal issues.
31. Review for backward compatibility.

Do not claim completion if major acceptance criteria are stubbed, mocked-only, or TODO placeholders.

---

# 72. Final Code Quality Requirements

The resulting implementation should be:

- idiomatic
- modular
- strongly typed where the project supports typing
- well tested
- documented
- deterministic
- concurrency-safe where concurrency is supported
- memory-conscious
- provider-neutral
- backward compatible
- secure
- easy to extend

Prefer simple abstractions over clever abstractions.

Do not create interfaces merely for theoretical purity.

Every abstraction should have a clear extension or testing purpose.

---

# 73. Final Deliverables

After implementation, ensure the repository contains:

1. Functional `argus ci run`.
2. CI configuration support.
3. CI context detection.
4. Stable exit codes.
5. Retry handling.
6. Artifact management.
7. JSON/JUnit/HTML reporting integration.
8. Quality policy engine.
9. GitHub provider integration.
10. GitHub Action.
11. Provider extension architecture.
12. Automated tests.
13. CI examples.
14. Documentation.

At the end, provide a concise implementation summary containing:

```text
Implemented
───────────
- ...
- ...
- ...

Files Added/Changed
───────────────────
- ...
- ...

CLI
───
argus ci run ...

Tests
─────
...

Known Limitations
──────────────────
...
```

Do not claim features were implemented if they were only designed.

---

# 74. Critical Instruction to Claude

**Do not treat this document as permission to redesign Argus unnecessarily.**

The existing Argus repository is the source of truth for:

- architecture
- naming
- configuration conventions
- CLI conventions
- test models
- adapter interfaces
- result models
- reporting
- dependency choices

This specification defines the desired CI/CD capability and architectural properties.

Where the existing repository already has a good abstraction, extend it.

Where this specification conflicts with an existing implementation detail, preserve the existing public behavior and adapt the design intelligently.

The most important architectural requirement is:

> **CI/CD must be a first-class orchestration layer over Argus's existing test execution engine, not a second test runner.**

The second most important requirement is:

> **No CI provider should be able to dictate the architecture of Argus.**

The third is:

> **The system must be designed so future features such as distributed execution, changed-code test selection, historical analytics, device farms, and AI-assisted CI can be added without rewriting the CI core.**

Implement the complete feature, not just a proof of concept.
