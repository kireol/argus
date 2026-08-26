# Argus — Baseline / Regression Management Implementation Specification

## Purpose

Implement a production-quality **Baseline / Regression Management subsystem** in Argus.

Repository:

- GitHub: https://github.com/kireol/argus
- Project name: Argus

This feature is intended to become a first-class part of Argus, not a one-off screenshot-diff utility.

The core product promise is:

> Argus remembers what “good” looked like and automatically identifies when a new run is meaningfully different.

The implementation must be:

- performant
- scalable
- modular
- testable
- backwards compatible
- easy to extend
- safe for CI/CD
- usable from CLI, the existing service/API layer, reports, and MCP
- designed so future comparison strategies can be added without rewriting the baseline system

Do not blindly implement the design below if the existing repository has a better established architectural pattern. First inspect the repository thoroughly and integrate with its existing conventions.

---

# 1. REQUIRED FIRST STEP — REPOSITORY ANALYSIS

Before writing code:

1. Inspect the entire Argus repository structure.
2. Identify:
   - test execution architecture
   - adapters
   - verification system
   - screenshot/image handling
   - OCR support
   - run/result models
   - artifact storage
   - reporting
   - CLI architecture
   - service/API layer
   - MCP implementation
   - configuration/YAML parsing
   - existing persistence/database abstractions
   - existing IDs and naming conventions
   - existing logging/error handling
   - existing plugin/adapter abstractions
3. Find the existing implementation for:
   - image verification
   - screenshot capture
   - expected/actual/diff artifacts
   - test result status
   - run IDs
   - device/platform information
   - HTML/JSON/JUnit reporting
4. Determine whether Argus already has persistence that should be reused.
5. Determine where this feature belongs architecturally.
6. Do not create duplicate abstractions when an existing Argus abstraction already solves the problem.
7. Preserve current public APIs unless a backward-compatible extension is necessary.

Before implementation, write a short internal architecture plan based on the actual repository structure.

---

# 2. FEATURE SCOPE

Implement the following capabilities:

1. Baseline storage
2. Baseline identity and lookup
3. Environment/device fingerprinting
4. Explicit user-defined regions
5. Automatically discovered visual-diff regions
6. Semantic regions from OCR/accessibility/UI hierarchy when available
7. Hierarchical regions
8. Dynamic/ignored regions
9. Pluggable comparison strategies
10. Baseline creation
11. Baseline comparison
12. Regression classification
13. Baseline approval/rejection
14. Baseline history/versioning
15. Baseline review in HTML reports
16. CLI commands
17. YAML configuration
18. CI-friendly exit codes
19. JSON/reporting integration
20. MCP integration
21. Baseline cleanup/pruning
22. Comprehensive automated tests

Do NOT require AI/LLM functionality for the initial implementation.

The architecture must allow AI-powered region detection later.

---

# 3. CORE ARCHITECTURAL MODEL

Create a first-class Baseline subsystem.

Conceptually:

Test Run
  ↓
Observations
  ↓
Baseline Resolver
  ↓
Comparison Engine
  ↓
Regression Result
  ↓
Report / CLI / CI / MCP

Do not couple baseline logic directly to screenshot files.

The system must support multiple baseline types:

- VisualBaseline
- ObservationBaseline
- MetricBaseline
- CompositeBaseline

The first implementation should prioritize visual baselines and generic observation/metric support where it naturally fits the existing Argus architecture.

Use interfaces/protocols/abstract classes where appropriate so new comparison types can be added without changing the orchestration layer.

---

# 4. BASELINE IDENTITY

A baseline must NOT be identified solely by test ID.

Baseline identity should include enough information to prevent accidental comparisons across incompatible environments.

At minimum consider:

- test ID
- platform
- device
- OS version
- resolution
- orientation
- app/package identifier
- relevant adapter
- environment/profile
- optionally browser/browser version where applicable

Example conceptual fingerprint:

```json
{
  "platform": "android",
  "device": "Pixel 8",
  "os_version": "16",
  "resolution": "1080x2400",
  "orientation": "portrait",
  "app_version": "5.4.1"
}
```

Do not make app version part of baseline identity unless repository analysis shows that this is appropriate.

A baseline should generally allow:

Current App Version → compare against Approved Baseline

The baseline should not become impossible to reuse simply because the application version changed.

Create a deterministic, canonical environment fingerprint.

Requirements:

- stable field ordering
- normalized values
- deterministic serialization
- deterministic hash
- no accidental identity changes caused by JSON key ordering

Example conceptual identity:

```text
BaselineKey =
  test_id +
  adapter/platform identity +
  environment fingerprint
```

Make this extensible.

---

# 5. BASELINE STORAGE

Use an abstraction such as:

```text
BaselineStore
```

It should support operations conceptually equivalent to:

```text
create()
get()
find()
list()
approve()
reject()
delete()
history()
prune()
```

Do not hard-code the rest of Argus to a filesystem implementation.

Provide a filesystem-backed implementation if that is the most appropriate initial backend.

Structure storage so future backends can be added, such as:

- local filesystem
- object storage
- database
- CI artifact store
- remote baseline service

A baseline record should contain metadata plus references to artifacts.

Do not duplicate large image binaries unnecessarily.

Prefer content-addressed or deterministic artifact storage when compatible with existing Argus storage.

---

# 6. BASELINE VERSIONING

Baselines must be immutable once approved.

Do NOT overwrite the previous baseline when accepting a new one.

Instead:

```text
Baseline
  version 1
  version 2
  version 3
```

One version is the active/approved version.

Maintain history.

Each baseline version should record:

- unique version ID
- baseline key
- created timestamp
- approved timestamp
- source run ID
- created/approved by
- application/build metadata if available
- environment fingerprint
- artifact references
- comparison configuration
- region definitions
- status
- parent baseline version if appropriate

Allow reverting to a previous approved baseline.

Example:

```text
baseline history
v1 approved
v2 approved
v3 rejected
v4 approved

active = v4
```

---

# 7. CRITICAL SAFETY RULE

A test run must NEVER automatically replace an approved baseline.

The workflow is:

```text
No Baseline
    ↓
Create Candidate
    ↓
Review
    ↓
Approve
    ↓
Approved Baseline
```

If a regression is detected:

```text
Approved Baseline
    ↓
New Run
    ↓
Regression
    ↓
Candidate Baseline
```

The candidate must remain separate until explicitly accepted.

---

# 8. REGIONS — FIRST CLASS MODEL

Regions are a core feature.

Do NOT model them as merely temporary bounding boxes generated by an image diff.

Create a reusable Region abstraction.

Conceptual model:

```text
Region
├── id
├── geometry
├── semantic_name
├── source
├── confidence
├── parent
├── children
├── comparison_strategy
├── threshold
├── dynamic_behavior
└── metadata
```

A region must support:

- rectangular geometry initially
- future geometry types
- semantic name
- source
- confidence
- hierarchy
- comparison configuration
- dynamic behavior
- metadata

Design geometry so polygons/masks can be added later without rewriting the Region API.

---

# 9. REGION SOURCES

Support these source types:

```text
explicit
accessibility
ocr
vision
diff
inferred
```

For the initial implementation:

### Explicit

Highest confidence.

### Diff

Automatically generated from image differences.

### OCR/accessibility

Create extension points and integrate existing Argus capabilities if they already expose enough information.

Do not invent unsupported adapter behavior.

### Vision

Create an interface for future computer-vision/AI region detection.

Do not require an LLM or AI model in this implementation.

Example:

```text
RegionDetector
  ├── ExplicitRegionDetector
  ├── DiffRegionDetector
  ├── OCRRegionDetector
  ├── AccessibilityRegionDetector
  └── VisionRegionDetector
```

Not every detector must be implemented immediately, but the architecture must permit them.

---

# 10. EXPLICIT REGIONS

Support YAML like:

```yaml
baseline:
  regions:
    - id: header
      x: 0
      y: 0
      width: 1920
      height: 120

    - id: movie_grid
      x: 0
      y: 120
      width: 1920
      height: 850

    - id: navigation
      x: 0
      y: 970
      width: 1920
      height: 110
```

Do not assume these exact field names if Argus has an established configuration convention, but preserve the same conceptual capability.

Validate:

- required fields
- non-negative coordinates
- positive dimensions
- bounds when image dimensions are known
- duplicate region IDs
- invalid parent references
- invalid comparison strategies
- invalid thresholds

Configuration errors must produce useful error messages.

---

# 11. RELATIVE REGIONS

Support resolution-independent definitions.

Example:

```yaml
regions:
  - id: content
    anchor: screen
    top: 120
    left: 0
    right: 0
    bottom: 110
```

Relative regions should resolve to actual pixel geometry at comparison time.

The design must allow future anchor types:

```text
screen
element
ocr
accessibility
region
```

Do not implement element anchoring unless the existing repository provides sufficient data.

Design for it.

---

# 12. HIERARCHICAL REGIONS

Regions must support parent/child relationships.

Example:

```text
Screen
├── Header
│   ├── Logo
│   ├── Search
│   └── Settings
├── Content
│   ├── MovieCard[0]
│   │   ├── Poster
│   │   └── Title
│   ├── MovieCard[1]
│   │   ├── Poster
│   │   └── Title
└── Navigation
```

A regression report should be capable of saying:

```text
Content
└── MovieCard[1]
    └── Poster
        REGRESSION
```

rather than merely:

```text
14,382 pixels changed
```

The data model should not assume a fixed depth.

---

# 13. DYNAMIC REGIONS

Support regions that are expected to change.

Examples:

```yaml
regions:
  - id: clock
    type: dynamic
    comparison: ignore

  - id: battery
    type: dynamic
    comparison: ignore
```

At minimum support:

```text
compare normally
ignore
```

Design for future modes such as:

```text
mask
perceptual
tolerant
dynamic
```

Common dynamic areas may eventually include:

- clocks
- battery percentage
- network indicators
- notifications
- video playback
- advertisements
- timestamps

Do not hard-code these as special cases.

---

# 14. COMPARISON ENGINE

Create a pluggable comparison architecture.

Conceptual interface:

```text
ComparisonStrategy
    compare(baseline, actual, context) -> ComparisonResult
```

Initial strategies should include whichever are already present in Argus plus new baseline-oriented strategies as appropriate.

Potential strategies:

```text
pixel
ssim
ocr
text
numeric
percentage
threshold
ignore
```

Do not duplicate existing verification logic.

If Argus already has image comparison functionality, refactor/reuse it behind the new comparison abstraction.

Each comparison result should contain enough structured information for reporting.

Conceptually:

```json
{
  "status": "regression",
  "similarity": 0.917,
  "changed_pixels": 14382,
  "changed_regions": [],
  "threshold": 0.97,
  "metrics": {}
}
```

---

# 15. PIXEL DIFF / AUTOMATIC REGION DETECTION

When no explicit semantic region exists, the comparison engine must be able to turn pixel differences into meaningful regions.

Do NOT report every changed pixel individually.

Pipeline:

```text
Baseline image
       +
Actual image
       ↓
Pixel difference
       ↓
Noise filtering
       ↓
Morphological processing
       ↓
Connected components
       ↓
Nearby-component merging
       ↓
Bounding boxes
       ↓
Candidate diff regions
```

The implementation should:

1. calculate a diff mask
2. apply configurable tolerance
3. remove insignificant noise
4. optionally perform morphological opening/closing
5. identify connected components
6. merge components that are close enough
7. produce bounding boxes
8. calculate region metrics
9. rank regions by significance

Avoid allocating huge numbers of objects per pixel.

Use efficient array/image operations where possible.

Do not implement O(width × height × regions) algorithms when an O(width × height) or near-linear approach is possible.

---

# 16. DIFF REGION METRICS

Each detected region should provide useful metrics.

At minimum:

```text
x
y
width
height
changed_pixels
total_pixels
changed_percentage
difference_score
confidence
```

Potential future metrics:

```text
mean_error
max_error
SSIM
perceptual_distance
color_shift
edge_difference
```

---

# 17. REGION MERGING

A single logical change may create multiple nearby connected components.

Implement configurable merging.

Conceptually:

```text
merge_distance
minimum_region_area
minimum_change_percentage
```

Example:

```text
Component A
Component B
Component C

      ↓

Merged Region
```

Avoid creating hundreds of tiny regions from anti-aliasing artifacts.

---

# 18. REGION SIGNIFICANCE

Rank detected regions.

A region with:

```text
20 changed pixels
```

should generally be less significant than:

```text
30,000 changed pixels
```

But raw changed-pixel count alone is insufficient.

Consider:

- changed percentage
- area
- difference magnitude
- comparison strategy
- region hierarchy
- confidence

Make the scoring algorithm isolated so it can be improved later.

---

# 19. BASELINE COMPARISON RESULT

Create a structured result model.

Conceptually:

```text
RegressionResult
├── status
├── baseline_id
├── baseline_version
├── test_id
├── environment
├── overall_score
├── regions[]
├── metrics[]
├── artifacts
├── summary
└── diagnostics
```

Statuses should distinguish at least:

```text
PASS
REGRESSION
NO_BASELINE
NEW
SKIPPED
ERROR
```

If a run has a candidate baseline but no approved baseline, make this explicit.

Do not classify "no baseline" as a regression.

---

# 20. REGRESSION SEVERITY

Support severity conceptually:

```text
info
warning
failure
```

For example:

```text
minor visual difference
major visual difference
critical required region changed
```

Do not over-engineer the first implementation.

Keep severity calculation in a dedicated component.

---

# 21. BASELINE APPROVAL

Provide explicit operations:

```bash
argus baseline create
argus baseline list
argus baseline show
argus baseline compare
argus baseline approve
argus baseline reject
argus baseline history
argus baseline revert
argus baseline prune
```

Adapt command syntax to the existing Argus CLI conventions.

Do not break existing CLI commands.

The approval operation must:

1. validate the candidate
2. verify its source run exists
3. verify artifacts exist
4. create an immutable baseline version
5. update the active pointer/index
6. preserve prior baseline history
7. write audit metadata
8. be atomic

If any step fails, do not leave the system in a partially approved state.

---

# 22. BASELINE CREATION

Support creation from a successful run.

Conceptually:

```bash
argus baseline create --run RUN_ID
```

If Argus has an existing "accept expected result" concept, integrate with it rather than creating duplicate workflows.

Do not automatically baseline failed tests unless explicitly requested.

---

# 23. BASELINE REVIEW

Enhance HTML reporting with a visual regression review.

Desired presentation:

```text
REGRESSION DETECTED

Test: MOV-001

BASELINE        CURRENT         DIFF

[image]         [image]         [diff]

Similarity: 91.7%
Changed regions: 3

Header
  ✓ unchanged

MovieGrid
  ⚠ regression

Navigation
  ✓ unchanged

[ Reject ]       [ Accept Baseline ]
```

The report must provide:

- baseline image
- actual image
- diff image
- region overlays
- region list
- metrics
- severity
- comparison strategy
- threshold
- baseline version
- candidate/source run

Use existing Argus reporting architecture.

Do not create a completely separate report system.

If the existing HTML report is static, do not require a backend server merely to view a report.

---

# 24. REGION VISUALIZATION

For visual reports:

- draw bounding boxes around regions
- use stable region IDs
- allow toggling region overlays if practical
- clearly distinguish baseline/current/diff
- show region metrics on selection if the current frontend architecture allows it

Do not make the report dependent on external services.

---

# 25. CLI OUTPUT

Human-readable CLI output should be concise but useful.

Example:

```text
Argus Regression Results

PASS       142
REGRESSION   3
NEW          1
ERROR        0

Regressions:

MOV-001
  Content/MovieCard[1]/Poster
  similarity: 0.917
  changed: 14,382 pixels
  severity: failure

MOV-008
  Navigation
  changed: 4.2%
  severity: warning

Exit code: 1
```

Support machine-readable output if Argus already supports JSON output.

---

# 26. CI/CD

Regression detection must work cleanly in CI.

Required behavior:

- no regressions → success
- regression → non-zero exit
- infrastructure/tool error → distinct failure
- no baseline → configurable behavior
- explicitly accepted baseline → success

Do not make "no baseline" automatically fail every project.

Support configuration such as:

```yaml
regression:
  missing_baseline: warn
```

Potential modes:

```text
ignore
warn
fail
```

---

# 27. YAML CONFIGURATION

Design a clear configuration model.

Example:

```yaml
regression:
  enabled: true

  missing_baseline: warn

  defaults:
    comparison: ssim
    threshold: 0.97

  regions:
    - id: clock
      type: dynamic
      comparison: ignore
```

Per-test configuration should be possible.

Example:

```yaml
tests:
  - id: MOV-001

    regression:
      enabled: true

      regions:
        - id: header
          x: 0
          y: 0
          width: 1920
          height: 120

        - id: navigation
          anchor: screen
          top: 970
          left: 0
          right: 0
          bottom: 0
          comparison: pixel
          threshold: 0.995
```

Adapt exact syntax to existing Argus YAML conventions.

---

# 28. DEFAULTS AND OVERRIDES

Support configuration precedence:

```text
global defaults
    ↓
environment/profile
    ↓
test
    ↓
region
```

The most specific configuration wins.

Avoid duplicating configuration in runtime code.

---

# 29. ARTIFACT MANAGEMENT

Regression artifacts may include:

```text
baseline.png
actual.png
diff.png
overlay.png
region metadata
comparison JSON
```

Reuse existing Argus artifact infrastructure.

Do not store multiple copies of identical images when content-addressed storage can safely avoid duplication.

Ensure artifacts remain available when a baseline is referenced by history.

Pruning must never delete artifacts still referenced by an active or historical baseline unless explicitly forced.

---

# 30. PERFORMANCE REQUIREMENTS

This subsystem must be designed for large test suites.

Assume:

- thousands of tests
- hundreds of thousands of screenshots
- multiple devices
- multiple environments
- large 4K screenshots
- CI execution
- parallel test runs

Requirements:

1. Do not load all baselines into memory.
2. Resolve only the baseline required for the current test.
3. Avoid repeatedly decoding the same image.
4. Cache safely where useful.
5. Stream large artifacts where possible.
6. Avoid O(n²) comparison of unrelated regions.
7. Keep baseline lookup indexed.
8. Make concurrent baseline reads safe.
9. Make baseline approval atomic.
10. Prevent concurrent approvals from corrupting state.
11. Avoid unnecessary image copies.
12. Release image memory promptly.
13. Use efficient image-processing primitives.
14. Do not introduce global mutable state.

---

# 31. CONCURRENCY

Multiple Argus processes may run simultaneously.

Design for:

```text
CI job A → reads baseline
CI job B → reads baseline
CI job C → reads baseline
```

simultaneously.

Approval must handle:

```text
Job A approves candidate
Job B approves candidate
```

without corrupting the active baseline.

Use atomic filesystem operations or the repository's existing transactional mechanism.

If the filesystem backend cannot guarantee safe concurrent writes on every supported platform, document the limitation and isolate the locking mechanism so another storage backend can replace it.

---

# 32. BASELINE LOCKING

Do not lock normal reads unnecessarily.

Approval/update operations may require a lock.

Prefer:

```text
read → no lock
write/update → atomic transaction/lock
```

Avoid global repository-wide locks.

---

# 33. MCP INTEGRATION

Expose useful baseline operations through the existing MCP architecture.

Potential tools:

```text
baseline_list
baseline_show
baseline_compare
baseline_create
baseline_approve
baseline_reject
baseline_history
baseline_revert
```

Use existing MCP conventions.

Do not duplicate baseline business logic inside MCP handlers.

MCP must call the same service layer used by CLI/API.

---

# 34. SERVICE/API ARCHITECTURE

Create a central service layer.

Conceptually:

```text
BaselineService
RegressionService
RegionService
ComparisonService
```

The CLI, MCP, and future UI should call these services.

Do not put core logic in CLI commands.

Avoid circular dependencies.

---

# 35. PLUGIN/STRATEGY ARCHITECTURE

Comparison strategies should be discoverable/configurable without modifying the regression orchestrator.

Conceptually:

```text
ComparisonRegistry
  register("pixel", PixelComparison)
  register("ssim", SSIMComparison)
  register("ignore", IgnoreComparison)
```

Future strategies can be added:

```text
perceptual
ocr
llm
custom
```

without changing:

```text
RegressionService
```

---

# 36. FUTURE AI COMPATIBILITY

The architecture must explicitly support future AI region detection.

Future pipeline:

```text
Screenshot
   ↓
AI Region Detector
   ↓
Semantic Regions
   ↓
Baseline
   ↓
Regression
```

Possible future output:

```json
{
  "id": "movie_card_2",
  "semantic_name": "Movie Card",
  "confidence": 0.98,
  "geometry": {
    "x": 510,
    "y": 180,
    "width": 180,
    "height": 260
  }
}
```

Do not implement this using hard-coded AI calls.

Define a detector interface.

---

# 37. REGION IDENTITY

Region IDs must be stable.

Do NOT generate IDs solely from random UUIDs when the region represents the same logical area across runs.

Explicit regions:

```text
navigation
movie_grid
header
```

are stable.

Automatically discovered regions should use deterministic identity where possible.

Potential inputs:

- parent region
- spatial ordering
- semantic label
- normalized geometry
- detector source

Do not require perfect identity matching in the first implementation, but structure the system so it can improve later.

---

# 38. RESOLUTION / SCALE HANDLING

Baseline comparisons must detect incompatible image dimensions.

Do not silently resize a baseline to the current image unless explicitly configured.

Default behavior:

```text
different dimensions → ERROR or INCOMPATIBLE
```

Support explicit scaling policies later.

If relative regions are used, resolve them independently against current image dimensions.

---

# 39. ORIENTATION HANDLING

Portrait and landscape should normally be separate environment identities.

Do not compare:

```text
1080x2400 portrait
```

against:

```text
2400x1080 landscape
```

unless explicitly configured.

---

# 40. BASELINE VALIDATION

Before accepting a baseline, validate:

- source run exists
- test succeeded
- artifacts exist
- image is readable
- dimensions are valid
- environment fingerprint exists
- configuration is valid
- regions are valid
- comparison strategy is available
- candidate is not corrupt

Return actionable errors.

---

# 41. ERROR HANDLING

Distinguish:

```text
Regression
No baseline
Configuration error
Artifact missing
Comparison error
Infrastructure error
```

Do not convert all failures into "regression."

Example:

```text
ERROR: Baseline image could not be decoded.
```

is not the same as:

```text
REGRESSION: Baseline and current screenshots differ.
```

---

# 42. LOGGING

Use existing Argus logging conventions.

Provide useful debug information:

```text
baseline key
baseline version
comparison strategy
threshold
region count
diff region count
comparison duration
image dimensions
```

Do not log huge pixel arrays or image contents.

---

# 43. METRICS / TIMING

Record comparison performance.

At minimum:

```text
baseline lookup time
image decode time
comparison time
region detection time
report generation time
```

If Argus already has metrics/tracing, integrate with it.

Do not add an entirely separate metrics system.

---

# 44. TESTING REQUIREMENTS

Create comprehensive tests.

## Unit tests

Test:

- baseline key generation
- environment fingerprinting
- canonical serialization
- baseline storage
- baseline lookup
- versioning
- approval
- rejection
- revert
- pruning
- region validation
- relative region resolution
- parent/child regions
- dynamic regions
- comparison strategies
- diff detection
- region merging
- severity
- configuration precedence
- error classification

## Image tests

Create deterministic fixtures for:

1. identical images
2. one-pixel difference
3. tiny noise
4. large changed area
5. multiple disconnected changes
6. nearby components that should merge
7. distant components that should remain separate
8. different dimensions
9. anti-aliasing differences
10. dynamic/ignored region
11. nested regions

## Integration tests

Test:

```text
run → baseline lookup → comparison → result → report
```

Test CLI commands.

Test MCP through the existing MCP test infrastructure.

Test concurrent reads.

Test concurrent approval.

---

# 45. GOLDEN/REFERENCE TESTS

Where useful, maintain deterministic reference screenshots and expected diff metadata.

Avoid tests that depend on:

- current time
- random IDs
- machine-specific paths
- GPU-specific floating-point behavior
- non-deterministic OCR
- external services

Normalize timestamps and IDs in test comparisons.

---

# 46. BACKWARD COMPATIBILITY

Existing Argus users must not be forced to configure baselines.

If regression management is disabled or unused:

- existing tests should behave as before
- existing reports should remain valid
- existing YAML should remain valid
- existing CLI commands should remain valid
- existing adapters should remain valid

Do not make baseline storage mandatory for normal test execution.

---

# 47. MIGRATION

If existing Argus artifacts can be reused as baselines, provide a migration/import path.

Do not require migration if it would create unnecessary complexity.

If no migration is needed, document why.

---

# 48. DOCUMENTATION

Add documentation for:

1. Baseline concepts
2. Quick start
3. CLI commands
4. YAML configuration
5. Explicit regions
6. Relative regions
7. Dynamic regions
8. Comparison strategies
9. Regression reports
10. CI usage
11. Baseline approval workflow
12. Baseline history
13. Troubleshooting
14. Architecture/extensibility

Include concrete examples.

---

# 49. EXAMPLE USER WORKFLOW

The final system should support a workflow similar to:

```bash
argus run tests/home.yaml
```

First successful run:

```text
No baseline found.
Candidate baseline available.
```

Then:

```bash
argus baseline create --run RUN_ID
```

Review:

```bash
argus baseline show ...
```

Approve:

```bash
argus baseline approve ...
```

Later:

```bash
argus run tests/home.yaml --regression
```

Output:

```text
PASS
```

After a UI change:

```text
REGRESSION

Test: home-screen
Region: Content/MovieCard[1]/Poster

Similarity: 0.917
Threshold: 0.970

Changed regions: 1
```

HTML report provides baseline/current/diff/overlay.

If the UI change is intentional:

```bash
argus baseline approve --run RUN_ID
```

Previous baseline remains in history.

---

# 50. DO NOT OVER-ENGINEER THE FIRST RELEASE

Implement the architecture for future extensibility, but keep the first implementation practical.

Required first-class capabilities:

- filesystem or existing Argus persistence backend
- immutable baseline versions
- environment fingerprinting
- explicit rectangular regions
- relative screen regions
- dynamic/ignored regions
- pixel/image comparison
- automatic diff-region detection
- region merging
- structured regression results
- CLI
- HTML report integration
- JSON integration
- CI exit behavior
- service-layer API
- MCP integration
- tests
- documentation

Future extension points:

- accessibility regions
- OCR regions
- DOM regions
- AI/vision regions
- semantic region matching
- advanced perceptual comparison
- remote baseline storage
- web-based approval UI
- automatic dynamic-region recognition

---

# 51. CODE QUALITY REQUIREMENTS

Follow the existing Argus coding style.

Requirements:

- strong typing where the project uses it
- small focused modules
- dependency injection where useful
- no giant classes
- no giant functions
- no hidden global state
- no duplicated business logic
- clear interfaces
- deterministic behavior
- actionable errors
- useful docstrings/comments where complexity warrants them

Do not add abstraction solely for abstraction's sake.

---

# 52. SECURITY / SAFETY

Baseline approval is a state-changing operation.

Ensure:

- paths cannot escape the configured baseline root
- user-controlled IDs cannot create arbitrary filesystem paths
- artifact references are validated
- malformed baseline metadata cannot crash the entire service
- corrupted baseline data fails safely
- candidate approval cannot overwrite unrelated files

If Argus is used in CI with untrusted test input, assume test configuration may contain malicious paths.

---

# 53. PERFORMANCE ACCEPTANCE CRITERIA

The implementation should be benchmarked using representative screenshots.

Measure:

- 1080p
- 1440p
- 4K

Measure:

- identical images
- small diff
- large diff
- many diff components

Record comparison timings.

Do not optimize prematurely, but avoid obviously inefficient algorithms.

The implementation should be capable of processing large test suites without keeping all screenshots in memory simultaneously.

---

# 54. IMPLEMENTATION ORDER

Implement in this order unless repository analysis strongly suggests another sequence:

### Phase 1
Architecture and domain models

### Phase 2
Baseline storage and versioning

### Phase 3
Environment fingerprinting

### Phase 4
Comparison abstraction

### Phase 5
Explicit and relative regions

### Phase 6
Diff-region detection

### Phase 7
Regression service

### Phase 8
CLI

### Phase 9
HTML/JSON/JUnit reporting integration

### Phase 10
MCP integration

### Phase 11
Concurrency, atomicity, and pruning

### Phase 12
Comprehensive testing

### Phase 13
Documentation and examples

---

# 55. IMPORTANT IMPLEMENTATION RULES

Do not:

- replace approved baselines automatically
- make baseline management required for existing tests
- couple everything directly to image files
- duplicate existing Argus image verification
- hard-code a single comparison algorithm
- hard-code regions into Python/code
- make AI mandatory
- use random IDs for stable semantic regions
- resize incompatible images silently
- classify infrastructure errors as regressions
- load the entire baseline repository into memory
- create a second independent reporting architecture
- put core business logic in CLI or MCP handlers
- break existing adapters

Do:

- reuse existing Argus abstractions
- preserve backwards compatibility
- keep services independent of presentation
- make comparison strategies pluggable
- make region detectors pluggable
- keep baseline versions immutable
- make approval explicit
- make writes atomic
- make reads efficient
- make results machine-readable
- make the HTML report useful for humans
- document extension points
- add tests before declaring the feature complete

---

# 56. DEFINITION OF DONE

Do not consider the feature complete until all of the following are true:

- [ ] Repository architecture was inspected before implementation.
- [ ] Existing image verification was reused/refactored rather than duplicated.
- [ ] Baseline domain model exists.
- [ ] Baseline storage exists.
- [ ] Baseline lookup exists.
- [ ] Deterministic environment fingerprinting exists.
- [ ] Immutable baseline versions exist.
- [ ] Explicit regions work.
- [ ] Relative screen regions work.
- [ ] Hierarchical regions work.
- [ ] Dynamic/ignored regions work.
- [ ] Pluggable comparison strategies exist.
- [ ] Pixel/image comparison works.
- [ ] Automatic diff-region detection works.
- [ ] Region merging works.
- [ ] Structured regression results exist.
- [ ] Regression severity exists.
- [ ] Baseline creation works.
- [ ] Baseline approval works.
- [ ] Baseline rejection works.
- [ ] Baseline history works.
- [ ] Baseline revert works.
- [ ] Baseline pruning works safely.
- [ ] CLI integration works.
- [ ] HTML report integration works.
- [ ] JSON reporting works.
- [ ] CI exit behavior works.
- [ ] MCP integration works.
- [ ] Concurrent reads are safe.
- [ ] Concurrent approval is safe.
- [ ] Path traversal protections exist.
- [ ] Unit tests exist.
- [ ] Integration tests exist.
- [ ] Image comparison tests exist.
- [ ] Documentation exists.
- [ ] Existing Argus tests still pass.
- [ ] Existing Argus functionality remains backward compatible.
- [ ] Performance has been measured on representative images.

---

# 57. FINAL DELIVERABLE

After implementation:

1. Run the full existing Argus test suite.
2. Run all new baseline/regression tests.
3. Run lint/type checks if the project uses them.
4. Run representative performance benchmarks.
5. Exercise the complete workflow manually or through integration tests:
   - create test
   - run
   - create baseline
   - approve baseline
   - modify screenshot
   - run regression
   - inspect diff
   - inspect region results
   - reject candidate
   - approve candidate
   - inspect history
   - revert baseline
6. Verify CLI behavior.
7. Verify MCP behavior.
8. Verify HTML report behavior.
9. Verify CI exit codes.
10. Update documentation and examples.
11. Provide a concise implementation summary containing:
    - files changed
    - architecture decisions
    - commands added
    - configuration added
    - tests added
    - performance observations
    - known limitations
    - future extension points

Most importantly:

**Build this as a durable Argus subsystem, not as a screenshot comparison feature bolted onto the side of the existing framework.**

The long-term goal is for Argus to be able to explain:

> “This build differs from the approved baseline.”

and, increasingly:

> “The Content region changed.”

> “MovieCard[1] changed.”

> “The poster changed, but the title and navigation did not.”

> “This is an intentional baseline change waiting for approval.”

The architecture should make that evolution possible without requiring a rewrite.
