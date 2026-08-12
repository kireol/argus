# Configuration

Configuration is layered; later layers override earlier ones:

1. Built-in defaults
2. Repository `config/default.yaml` (committed, no secrets)
3. Your user configuration (`utf init` creates it; path is platform-specific)
4. An explicit `--config file.yaml`

Values merge deeply, so a layer only needs the keys it changes.

## Secrets & environment variables

Any string may contain `${ENV_VAR}` (with optional default
`${ENV_VAR:-value}`). **Never** put credentials in files:

```yaml
backend:
  base_url: ${BACKEND_URL}
  token: ${BACKEND_TOKEN}
```

An unresolved `${...}` doesn't crash the framework — the component is
reported as *not configured* by `utf validate` and only fails if a selected
test actually needs it. Tokens and keys are redacted from logs and never
written to artifacts.

## Full reference

```yaml
backend:
  type: http                 # http (default) | fake (in-memory demo backend)
  base_url: ${BACKEND_URL}
  token: ${BACKEND_TOKEN}    # sent as "<auth_scheme> <token>" in <auth_header>
  auth_header: Authorization
  auth_scheme: Bearer
  headers: {}                # extra headers on every request
  timeout: 10s
  retries: 2                 # transport retries (502/503/504/timeouts)
  verify_tls: true           # never disable in real environments
  state_endpoint: /api/state # used by backend.set / backend_value
  health_endpoint: /health
  initial_state: {}          # fake backend only

devices:
  <name>:                    # the name used in requires.devices / reports
    type: android            # android | yocto | fake | plugin-provided
    platform: android        # filtering label; defaults to type
    instrumentation:         # optional, per device
      type: http             # http | fake
      base_url: http://127.0.0.1:8085
      timeout: 5s
      status_endpoint: /test/status
      health_endpoint: /test/health
      state_endpoint: /test/state
    # ... adapter-specific options, see android.md / yocto.md

verification:
  image:
    default_threshold: 0.90  # 0..1; per-condition `threshold:` overrides
    grayscale: false
    scale_tolerance: 0.0     # e.g. 0.1 tries 90%/100%/110% template sizes
    match_method: ccoeff_normed  # ccorr_normed | sqdiff_normed

ocr:
  provider: tesseract        # tesseract | fake | plugin-provided
  language: eng

regions:                     # named screen regions for tests
  movie_artwork:
    x: 100
    y: 100
    width: 500
    height: 400

wait:
  default_timeout: 10s       # wait_until defaults
  default_poll_interval: 250ms

results:
  dir: results
  retain_on_success: false   # true keeps artifacts for passing tests too
  save_screenshots_on_failure: true

logging:
  level: INFO                # DEBUG | INFO | WARNING | ERROR
  format: text               # text | json
  file: null                 # optional JSON log file

test_paths: [test_suites]    # where YAML tests live
asset_paths: [assets/images] # where reference images are resolved

variables: {}                # global ${variables} available to all tests
```

## Repository vs user configuration (spec §65.9)

**Commit:** test definitions, `config/default.yaml`, reference images,
`config/example.yaml`, `config/fake.yaml`.

**Never commit:** credentials, tokens, SSH keys, device addresses, emulator
serials, machine-specific paths. These belong in your user configuration
and environment variables. `.gitignore` already excludes `results/` and
`.env`.

## Checking your configuration

```bash
utf validate            # full environment, section by section
utf --dry-run           # everything a real run would touch, executes nothing
```
