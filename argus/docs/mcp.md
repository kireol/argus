# MCP Server (AI clients)

Argus speaks the [Model Context Protocol](https://modelcontextprotocol.io) so
AI clients — Claude Code, desktop assistants, IDE integrations, CI agents —
can discover, inspect, run and debug tests through the same service layer the
CLI uses. Nothing about the engine changes: MCP is one more client of
`ArgusService`, exactly as a future GUI would be.

```text
MCP client (Claude Code, IDE, CI agent)
        │  stdio  or  Streamable HTTP
        ▼
argus.mcp        thin: transport, tool/resource/prompt registration, schemas
        │
        ▼
argus.service    ArgusService — shared by CLI / MCP / future GUI
        │
        ├── TestRunner / RunSession   (engine, devices, backend, instrumentation)
        ├── RunRegistry               (background runs, EventBus capture, device leases)
        └── artifacts / reporting     (results/<timestamp>/…)
```

**MCP API version: 1.0** (see [Versioning](#versioning)).

## Installation

MCP support is optional and adds the official Python SDK (`mcp>=2.1`):

```bash
pip install -e ".[mcp]"          # or: ./install.sh then pip install "argus[mcp]"
argus mcp --help
```

Without the extra, every other Argus command keeps working; `argus mcp` exits
with code 2 and the remediation `pip install "argus[mcp]"`.

## Starting the server

```bash
argus mcp                                              # stdio (default)
argus mcp --config config/fake.yaml                    # stdio, demo suite on fake devices
argus mcp --transport streamable-http                  # http://127.0.0.1:8000/mcp
argus mcp --transport streamable-http --host 0.0.0.0 --port 8765 --path /argus/mcp
```

Flags override the `mcp:` section of the configuration (see
[Configuration](#configuration)); `--config` selects the Argus configuration
exactly as for `argus run`. Logging follows `logging.level` / `--log-level`.

### STDIO

`stdout` carries the protocol; **all** Argus and SDK logging goes to `stderr`.
The `mcp` command never prints to stdout — not even configuration errors.

Trust model: whoever can launch `argus mcp` can run any configured test, which
means real backend changes and real device input. Treat the process like an
`argus run` started by that user; do not expose it to untrusted clients.

### Streamable HTTP

- Endpoint: `http://<host>:<port><path>` (default `/mcp`).
- Runs **stateless** by default (`mcp.stateless_http: true`): every request is
  self-contained, so replicas can sit behind a load balancer without session
  affinity. Set `json_response: true` for clients that prefer plain JSON over
  SSE responses.
- Localhost is **not** trusted automatically: binding to a non-loopback host
  without authentication is refused at startup.
- DNS-rebinding protection is on: the `Host` header must match the bound
  loopback address unless `mcp.allowed_hosts` / `mcp.allowed_origins` say
  otherwise (a wrong host gets `421 Misdirected Request`).

## Authentication / authorization

Two options:

1. **Static bearer tokens** (built in): list them under `mcp.auth.tokens`,
   referencing environment variables so they never live in a file:

   ```yaml
   mcp:
     transport: streamable-http
     host: 0.0.0.0
     auth:
       tokens: ["${ARGUS_MCP_TOKEN}"]
   ```

   Requests must send `Authorization: Bearer <token>`; anything else gets
   `401` with `WWW-Authenticate: Bearer realm="argus-mcp"`. Tokens are compared
   in constant time; unresolved `${...}` entries are ignored (and count as "no
   token configured").
2. **OAuth / JWT** (SDK resource-server model): call
   `argus.mcp.server.create_server(config, token_verifier=…, auth=AuthSettings(…))`
   from your own launcher; the SDK then serves protected-resource metadata and
   validates tokens. The bearer middleware is skipped when SDK auth is active.

Authorization is coarse: every authenticated client may use every tool. Run
separate servers (separate configurations) to scope what a client can reach.

## Client configuration

### Claude Code

```bash
claude mcp add argus -- argus mcp --config /path/to/config.yaml
# remote / CI server:
claude mcp add --transport http argus http://ci-host:8000/mcp --header "Authorization: Bearer $ARGUS_MCP_TOKEN"
```

or in `.mcp.json` at the project root:

```json
{
  "mcpServers": {
    "argus": {
      "command": "argus",
      "args": ["mcp", "--config", "config/fake.yaml"]
    }
  }
}
```

### Python (SDK v2)

```python
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

async with httpx2.AsyncClient(headers={"Authorization": "Bearer …"}) as http:
    async with Client(streamable_http_client("http://ci-host:8000/mcp", http_client=http)) as c:
        outcome = await c.call_tool("argus_run_test", {"test_id": "MOV-001"})
        print(outcome.structured_content["run"]["status"])
```

For stdio, `Client(StdioServerParameters(command="argus", args=["mcp", "--config", "…"]))`.

### Other clients (Claude Desktop, IDEs)

Any client that launches stdio servers works with `command: argus`,
`args: ["mcp", "--config", "<file>"]`. Use the absolute path to the `argus`
executable inside the virtualenv (`<repo>/.venv/bin/argus`) when the client does not
inherit your shell `PATH`. For HTTP clients point them at the endpoint URL and
add the bearer header.

## Tools

Names are stable and self-describing; annotations tell clients which tools
are read-only. Every tool returns **structured content** (JSON matching a
published output schema) plus readable text; errors come back as
`is_error` results with a structured `error` object (see [Errors](#errors)).

| Tool | Side effects | Purpose |
| --- | --- | --- |
| `argus_list_tests` | none | Find tests by feature / tags (or tag expression) / platform / ids / text. Paginated (`limit`, `cursor`). |
| `argus_get_test` | none | Full definition: metadata, requirements, parameters, retry, setup/steps/teardown. |
| `argus_validate` | connects (probe) | `argus validate` as data: checks, failures, warnings, remediation. `framework_only=true` touches no device. |
| `argus_preflight` | connects (probe) | The pre-flight a run would perform for a selection, without executing: requirements + every check. |
| `argus_run_test` | **runs a test** | One test, optionally on one platform/device. Waits up to `wait_seconds` with progress, else returns a `run_id`. |
| `argus_run_tests` | **runs tests** | A selection with the CLI's failure policy (`continue_on_failure`, `max_failures`). |
| `argus_get_run` | none | Run state/status/counts/current test/results dir. Cheap; poll it. |
| `argus_get_run_events` | none | EventBus events for a run, paged with `after`; bounded with a dropped counter. |
| `argus_list_runs` | none | Recent runs known to this server. |
| `argus_list_devices` | none | Inventory from configuration: adapter, platform, capabilities, lease state. Never connects. |
| `argus_get_device` | connects if `probe=true` | Details; with `probe` also health, screen size, app running. |
| `argus_capture_screenshot` | connects | Current screen as MCP **image content**; optional crop, png/jpeg, bounded size. |
| `argus_list_artifacts` | none | Metadata for a run's files (kind, MIME, size, owning test). Paginated. |
| `argus_get_artifact` | none | One artifact: images as image content, text/JSON as text (truncated with a flag), others described only. |
| `argus_diagnose_run` | none | Facts about failures: failing step, expected vs observed, instrumentation state, artifacts, hints. |

`argus --dry-run` has no separate tool: `argus_preflight` *is* the dry run
for a selection.

### Run execution model

MCP SDK v2 has no background-task primitive, so runs are Argus-native:

```text
argus_run_test(s) ──> run_id ──┬── argus_get_run          (state, counts, current test)
                               ├── argus_get_run_events   (EventBus projection)
                               ├── argus://runs/{run_id}  (per-test outcomes)
                               ├── argus_diagnose_run
                               └── argus_list_artifacts / argus_get_artifact
```

- A run executes `TestRunner.run` on a worker thread inside the server
  process; the request thread only waits (bounded by `wait_seconds` and
  `mcp.limits.max_wait`) and forwards MCP progress notifications.
- The run's `EventBus` events are projected into compact dictionaries
  (`test_started`, `action_completed`, `test_failed`, …) and kept in a bounded
  buffer (`mcp.limits.max_run_events`).
- Reports (`report.json`, `junit.xml`, `report.html`, or `preflight.json`)
  are written exactly as `argus run` writes them.
- Argus timeouts are untouched: an MCP wait expiring never cancels or orphans
  the run — it keeps going and stays pollable.

### Concurrency rules

Argus runs tests sequentially and never lets two tests fight over one device.
The server enforces the same rule *across requests*:

- Read-only tools are safe to call concurrently.
- A run claims the devices it needs for its whole duration; screenshots,
  probes, preflight and `argus_validate` take short leases. A conflicting
  request is **rejected** (`category: busy`, `retryable: true`, naming the
  active run) — never silently queued.
- `mcp.limits.max_concurrent_runs` (default 1) caps runs per server.

## Resources

| URI | Content |
| --- | --- |
| `argus://tests` | Test index (bounded to `max_results`, with `truncated`). |
| `argus://tests/{test_id}` | Full test definition. |
| `argus://runs` | Recent runs, newest first. |
| `argus://runs/{run_id}` | Run status + one compact entry per executed test. |
| `argus://runs/{run_id}/report` | The run's `report.json` (schema_version 1). |
| `argus://runs/{run_id}/test/{test_id}` | Every execution of a test in a run, with step results. |
| `argus://devices` | Device inventory (never connects). |
| `argus://devices/{device_name}` | One device, adapter options redacted. |
| `argus://configuration` | Effective configuration with secrets redacted. |

All resources are `application/json` and deterministic for a given state.

## Prompts

| Prompt | Arguments | Guides the model to… |
| --- | --- | --- |
| `argus_debug_failed_test` | `run_id`, `test_id?` | inspect run → diagnosis → images → logs → device → conclude with evidence. |
| `argus_create_test` | `feature`, `goal`, `platform?` | write valid YAML: schema, actions, conditions, verification philosophy, common mistakes; points to `docs/test-authoring.md`. |
| `argus_investigate_failure` | `run_id?` | classify (environment / product / infrastructure) and pick the next step. |

Prompts never assert an answer; they route the model through the tools.

## Errors

Anticipated failures return an `is_error` tool result:

```json
{
  "error": {
    "type": "DeviceConnectionError",
    "category": "device_connection",
    "message": "…",
    "remediation": "…",
    "retryable": true,
    "operation": "argus_capture_screenshot"
  }
}
```

Categories map one-to-one onto `argus.exceptions` (`configuration`,
`preflight`, `device_connection`, `device_capability`, `backend`,
`instrumentation`, `screenshot`, `verification`, `test_definition`,
`test_execution`, `timeout`, `asset`) plus `busy` (device/run conflict) and
`invalid_argument`. Unexpected exceptions are logged server-side with their
traceback; the client sees only `Error executing tool <name>` — no internals,
no stack traces.

## Security model

- **Secrets never leave the process.** Configuration and device options are
  redacted by key (`token`, `password`, `secret`, `*key*`, `credential`,
  `authorization`, `cookie`, …) and by the same pattern-based redaction the
  logging layer uses. `mcp.auth` is omitted entirely from
  `argus://configuration`.
- **Artifact access is confined** to the run's own `results/<stamp>/`
  directory; ids are validated (no absolute paths, `..`, backslashes, NULs) and
  resolved paths must stay inside the directory (symlinks included).
- **Side effects are declared**: run/screenshot/probe tools carry
  `readOnlyHint=false` or `openWorldHint=true` annotations and say so in their
  descriptions, so clients can ask for approval.
- **No arbitrary execution**: there is no shell, no Python, no raw device
  input tool. Tests can only do what their YAML declares (which may include the
  existing `shell.run` action — configure suites accordingly).
- **Bounded responses**: every list is paginated, text is truncated with an
  explicit flag, images are downscaled to `max_screenshot_dimension`, artifact
  bytes are capped.
- **HTTP**: bearer tokens or SDK OAuth; refuses non-loopback binds without
  auth; DNS-rebinding protection.
- **STDIO**: inherits the launching user's trust.

## Configuration

```yaml
mcp:
  transport: stdio          # stdio | streamable-http
  host: 127.0.0.1
  port: 8000
  path: /mcp
  stateless_http: true      # no session affinity needed between replicas
  json_response: false      # true: plain JSON responses instead of SSE
  allowed_hosts: []         # extra Host values (DNS-rebinding protection)
  allowed_origins: []
  auth:
    tokens: []              # ["${ARGUS_MCP_TOKEN}"]
  limits:
    max_results: 50         # page size cap for every list
    max_artifact_bytes: 1000000
    max_log_bytes: 32768    # default text cap for argus_get_artifact
    max_screenshot_dimension: 1280
    max_concurrent_runs: 1
    max_run_events: 2000    # per run; oldest are dropped and counted
    max_retained_runs: 100  # finished runs kept in memory
    max_wait: 10m           # cap on run_test/run_tests wait_seconds
```

Everything is validated (`extra: forbid`); `${ENV}` references work as
everywhere else in Argus.

## CI usage

Run a shared server next to the devices and let agents reach it over HTTP:

```bash
export ARGUS_MCP_TOKEN=$(openssl rand -hex 32)
argus mcp --config config/lab.yaml --transport streamable-http --host 0.0.0.0 --port 8000
```

with `mcp.auth.tokens: ["${ARGUS_MCP_TOKEN}"]` in `config/lab.yaml`. Each
agent adds `Authorization: Bearer …`. Because runs claim devices, several
agents can share one lab without colliding: the second caller is told which
run holds the device and retries later.

For scripted checks the CLI remains the simplest client (`argus run` exit
codes); MCP adds value when an agent needs to *reason* about results.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `argus mcp` exits 2: "requires the optional 'mcp' package" | `pip install "argus[mcp]"`. |
| Client reports a JSON parse error over stdio | Something printed to stdout. Argus never does; check wrappers/shell profiles that echo. Run `argus mcp 2>/dev/null` manually: stdout must stay silent until the client speaks. |
| `421 Misdirected Request` | `Host` header not allowed (DNS-rebinding guard). Use the bound address or set `mcp.allowed_hosts`. |
| `401 unauthorized` | Missing/incorrect bearer token; unresolved `${VAR}` tokens do not count. |
| "Refusing to serve MCP over HTTP on '0.0.0.0' without authentication" | Set `mcp.auth.tokens` or bind to `127.0.0.1`. |
| `category: busy` | A run or probe holds the device; poll `argus_get_run` on the named run. |
| `completed: false` from `argus_run_test` | The run outlived `wait_seconds`; poll with `argus_get_run`. |
| Tests edited on disk are not visible | The catalog reloads when a suite file's size/mtime changes; an invalid suite raises `test_definition` until fixed. |

## Performance

- Test discovery is cached per server and invalidated by file signature; list
  requests never re-parse YAML unless a suite changed.
- `argus_list_devices` and the `argus://devices` resources are
  configuration-derived — no connections.
- One `RunSession` per run/operation (devices stay connected for the whole
  run, HTTP backend clients are pooled, reference images cached) — the same
  lifetimes as `argus run`.
- Synchronous Argus calls execute on the SDK's worker-thread pool, so a slow
  device never blocks the protocol loop; run waits poll a `threading.Event`.
- Screenshots are re-encoded once (optional downscale) and sent as image
  content, never as text.

## State and scaling

| State | Scope | Lives in |
| --- | --- | --- |
| Test catalog cache | process-local | `TestCatalog` (auto-invalidating) |
| Run records + event buffers | process-local | `InMemoryRunStore` |
| Device leases | process-local | `RunRegistry` |
| Devices, backend client, asset cache | run-local | `RunSession` (per run / per operation) |
| Reports and artifacts | persistent | `results/<timestamp>/` |
| Configuration | persistent, read-only | YAML + environment |

The HTTP transport is already stateless. To run several replicas you would:

1. implement `argus.service.runs.RunStore` over a shared backend (SQLite,
   PostgreSQL, Redis, …) and pass it to `ArgusService(run_store=…)`;
2. back device leases with the same store (the `claim`/`start` checks in
   `RunRegistry` are the only two call sites);
3. share the results directory (or serve artifacts from the replica that
   produced them).

Tool and resource contracts do not change for any of these.

## Extending

Everything is registered from small modules; nothing central needs editing.

### Add a tool

1. Create `src/argus/mcp/tools/<area>.py` with
   `register_<area>_tools(server, ctx: ServerContext)`.
2. Define the output model in `argus/mcp/schemas.py` (a `from_*` constructor
   from the Argus model keeps the contract in one place).
3. Write the tool as a typed function (inputs are the signature; use
   `Annotated[..., Field(description=...)]`), decorate with
   `@server.tool(name="argus_…", annotations=…, description=…)` and
   `@guarded("argus_…")`, and call `ctx.service` — never the engine directly.
   If the service lacks the operation, add it to `ArgusService` first.
4. Append the registrar to `REGISTRARS` in `argus/mcp/tools/__init__.py`.
5. Add tests under `tests/mcp_server/` using `Client(create_server(config))`
   and the fake-device `project` fixture.

### Add a resource

Same pattern in `argus/mcp/resources/<area>.py`: `@server.resource("argus://…",
mime_type=JSON)` returning `dump(...)`; raise `ResourceNotFoundError` for
unknown ids. Register in `argus/mcp/resources/__init__.py`.

### Add a prompt

Add a function under `argus/mcp/prompts/testing.py` (or a new module) with
`@server.prompt(name="argus_…")` returning the prompt text; register in
`argus/mcp/prompts/__init__.py`. Keep prompts short and point at docs.

## Versioning

The MCP surface is a public API versioned independently of Argus releases:
`argus.mcp.MCP_API_VERSION` (exposed in `argus://configuration`).

- Additive changes (new tools, new optional arguments, new output fields)
  bump the minor version.
- Renaming/removing tools or resources, changing argument semantics, or
  removing output fields bump the major version and are listed here.

**1.0** — initial release: 15 tools, 9 resources, 3 prompts.

## Conformance notes

Implemented with the official `mcp` Python SDK (v2, `MCPServer`), which
negotiates the current protocol version with each client. Supported: tools
with input/output schemas and annotations, structured content, image content,
progress notifications, resources and resource templates, prompts, stdio and
Streamable HTTP (stateless or session-based), bearer/OAuth authentication.
Not offered: resource subscriptions/change notifications, sampling,
elicitation, roots, and completions — none are needed by the current tools.
