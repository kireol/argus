# Argus Stress / Monkey / Chaos Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A first-class, deterministic, extensible `argus stress` subsystem that randomizes UI actions (monkey), pushes operating conditions (stress) and mutates backend state / injects faults underneath the application (chaos), detecting failures through Argus's existing observation facilities, with seed + trace reproduction, replay and delta-debugging minimization.

**Architecture:** New package `argus.stress`. A `StressContext` owns a `DeterministicRNG`, a `Clock`, the `Device`, a `MutationBackend`, bounded histories and an append-only JSONL `Trace`. Pluggable registries (entry-point groups `argus.stress.actions`, `argus.stress.mutations`, `argus.stress.detectors`, `argus.stress.faults`, `argus.stress.extractors`) supply action types, mutation types, data-mutation strategies, failure detectors, fault injectors and context extractors. The `StressEngine` loop = policy limits → pick action (weighted, capability-filtered, target-aware) → schedule mutations (probability/timing/context) → execute → observe (sampled) → detectors → evidence via `ArtifactManager`. `Replay` re-executes a recorded trace; `Minimizer` runs ddmin over it with a reproduction predicate.

**Tech Stack:** Python 3.12, pydantic v2, Typer/Rich, existing Argus adapters (`Device`, `BackendAdapter`, `FakeDevice`, `FakeBackend`), `OCRProvider`, `ArtifactManager`, `EventBus`.

**Spec:** `../../argus_stress_monkey_chaos_testing_claude_prompt.md` (monorepo root).

## Global Constraints

- No global mutable state; every random decision comes from `context.rng`; durations use `context.clock.monotonic()`.
- Actions/mutations/detectors/faults/extractors are registered, never a giant `if`.
- Never perform a destructive mutation unless `safety.allow_destructive_mutations: true` **and** the entity is allow-listed **and** `safety.environment` is an allowed environment; dry-run blocks all mutations.
- Bounded memory: histories are `deque(maxlen)`, observations keep at most N recent images, trace streams to disk.
- Reuse `ArtifactManager`/`TestArtifacts`, `OCRProvider`, verifiers/conditions; no second assertion or artifact system.
- Existing suite: 687 tests, 11 pre-existing failures on `main` (console-reporter ANSI, `_FixedOCR` fixture) — must not grow.
- `ruff check src tests`, `mypy src` clean for new code.

## File map (`src/argus/stress/`)

| File | Responsibility |
|---|---|
| `rng.py` | `DeterministicRNG` (seeded, forkable, weighted choice), `new_seed()` |
| `clock.py` | `Clock` protocol, `MonotonicClock`, `FakeClock` |
| `models.py` | `StressAction`, `Mutation`, `TraceEvent`, `Failure`, `FailureSeverity`, `FailureCategory`, `Target`, `EntityRef`, `StressRunRecord`, `StressSummary` |
| `config.py` | `StressConfig` (+ sub-models) and scenario file loading; `AppConfig.stress` |
| `context.py` | `StressContext` (run_id, seed, rng, clock, device, backend, histories, trace, failures, artifacts, cancel, entity context) |
| `trace.py` | `Trace` (append-only JSONL writer + bounded tail), `read_trace()` |
| `capabilities.py` | `DeviceProbe` — capability + optional-method checks for actions |
| `actions/base.py`, `actions/builtin.py` | `StressActionType`, `StressActionRegistry`, builtin action types |
| `targets.py` | `Target`, `TargetProvider`s (configured, OCR-derived, coordinate fallback), `TargetSelector` |
| `generator.py` | `ActionGenerator` — weighted, enabled, capability-filtered, delays |
| `mutations/backend.py` | `MutationBackend` protocol, `BackendSchema`/`EntitySchema`, `StateMutationBackend` (over `BackendAdapter.get_state/set_state`), `RestMutationBackend`, `FakeMutationBackend` |
| `mutations/types.py` | `MutationType` registry: create/update/delete/duplicate/disable/archive |
| `mutations/data.py` | `DataMutationStrategy` registry (null, empty, missing, duplicate, very_long_string, special_characters, unicode, zero, negative, minimum, maximum, out_of_range, invalid_enum, past_date, future_date) |
| `mutations/scheduler.py` | `MutationScheduler` — probability / before / after / delayed / on-context timing |
| `extractors.py` | `ContextExtractor` protocol; OCR-, state- and configured extractors; composite |
| `safety.py` | `SafetyPolicy.check(mutation) -> Verdict`, dry-run |
| `faults.py` | `FaultInjector` protocol, `FaultRegistry`, `BackendFaultInjector` (wraps Argus's backend client), `FakeFaultInjector` |
| `detectors.py` | `FailureDetector` protocol/registry; crash, blank screen, frozen UI, error screen (OCR), stale entity, action error, infrastructure |
| `evidence.py` | `EvidenceCollector` — per-failure artifacts (before/after PNG, history, logs, metadata) |
| `engine.py` | `StressEngine.run()`, graceful shutdown, `ReplayEngine` |
| `minimize.py` | `Minimizer` (ddmin) with reproduction predicate + cache |
| `runs.py` | `StressRunStore` — persist/load run records under `results/stress/<run_id>/` |
| `report.py` | console summary / failure report / JSON |
| `demo.py` | `DemoStoreWorld`: deterministic fake product/checkout app (device + mutation backend) with an opt-in stale-checkout bug — the e2e fixture |
| `cli/stress.py` | `argus stress [run] / replay / minimize / list` |
| `examples/stress/checkout-chaos.yaml` | the realistic example scenario |
| `docs/stress-testing.md` | user + extension docs |
| `tests/stress/*` | unit/integration/e2e |

## Tasks (each: failing tests → implement → suite green → commit)

1. Core: rng, clock, models, config (+`AppConfig.stress`), trace, context.
2. Actions: registry, builtin action types, capability probe, targets, generator.
3. Mutations: backends (state/rest/fake), schema, mutation types, data strategies, safety, scheduler, extractors.
4. Faults + detectors + evidence.
5. Engine (run, shutdown), run store, report.
6. Replay + minimizer.
7. Demo world + example scenario + CLI.
8. Tests (unit/integration/e2e), docs, lint/type, full suite.
