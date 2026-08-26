# MCP Server Implementation Plan

**Goal:** First-class Model Context Protocol support so AI clients (Claude Code, IDEs, CI agents) can discover, inspect, run and debug Argus tests through the same service layer the CLI uses.

**Architecture:**

```text
MCP client ──> argus.mcp (thin: transport, tool/resource/prompt registration, schemas)
                   │
                   ▼
              argus.service.ArgusService   (shared facade — CLI/GUI/REST can use it too)
                   │
     ┌─────────────┼──────────────────┐
     ▼             ▼                  ▼
 TestRunner    RunSession         RunRegistry (background run execution + RunStore)
 (engine)      (devices/backend)  (EventBus subscriber → compact run events)
```

- `src/argus/service/` (new, MCP-independent): `catalog.py` (cached test loading with mtime invalidation), `runs.py` (`RunStore` protocol, `InMemoryRunStore`, `RunRegistry` executing `TestRunner.run` in a worker thread and recording EventBus events; enforces `max_concurrent_runs` and per-device exclusivity), `validation.py` (moved from `cli/validate.py`; CLI re-exports), `facade.py` (`ArgusService`: list/get tests, validate, preflight, devices, screenshot, runs, artifacts, diagnosis, redacted config).
- `src/argus/mcp/` (new, thin): `config.py` (`MCPConfig` on `AppConfig.mcp`), `server.py` (`create_server`, `run_server`), `auth.py` (bearer-token ASGI middleware), `errors.py` (UTFError → structured `is_error` results), `schemas.py` (Pydantic output models), `pagination.py`, `tools/*.py`, `resources/*.py`, `prompts/testing.py` each exposing `register_*(server, ctx)`.
- CLI: `argus mcp [--transport stdio|streamable-http] [--host] [--port] [--path]`.
- Packaging: optional extra `argus[mcp]` → `mcp>=2.1` (SDK v2: `mcp.server.MCPServer`). Sync tools run in the SDK's worker thread pool; long runs are started in the background and polled via `run_id` (SDK v2 has no task API).

**State model (documented in docs/mcp.md):** process-local = test catalog cache, `InMemoryRunStore`, run threads; run-local = `RunSession`, devices, artifacts dir; persistent = `results/<stamp>/` reports and artifacts. Replicas would need a shared `RunStore` and device-lease backend.

## Constraints

- No new pytest/ruff/mypy failures (baseline: 11 tests, 3 ruff, 2 mypy pre-existing).
- `argus` must import and all existing tests must pass with `mcp` **absent**; `argus mcp` without the SDK → `ConfigurationError` with `pip install "argus[mcp]"` remediation.
- STDIO: nothing but protocol on stdout. No `rich` console prints in the `mcp` command.
- No new global mutable state; every tool closes over an explicit `ServerContext`.
- Tests use fake devices only; MCP tests use the SDK's in-memory `Client(server)`.

## Tasks

- [x] 1. `pyproject.toml`: `mcp` extra; `config.models.MCPConfig`; logging context fields (`run_id`, `operation`, `tool`).
- [x] 2. `argus.service`: catalog, runs (store/registry), validation move, facade.
- [x] 3. `argus.mcp` core: errors, schemas, pagination, context, server, auth; CLI `mcp` command.
- [x] 4. Tools: discovery, validation/preflight, execution (+ run status/events), devices, screenshot, artifacts, diagnostics.
- [x] 5. Resources and prompts.
- [x] 6. Tests: service unit tests; MCP protocol tests (tools/resources/prompts, errors, security, concurrency, HTTP auth).
- [x] 7. Docs: `docs/mcp.md`, README section, `docs/cli.md`, `docs/configuration.md`, `config/example.yaml`.
- [x] 8. Final validation: full suite, ruff, mypy, live client smoke test over stdio and HTTP.
