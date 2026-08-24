# Argus — Universal Test Framework (`argus`)

A cross-platform functional and **visual** testing framework for applications
that have no UI automation hooks. Argus drives your backend into a known
state, observes the application from the outside (screenshots, image
recognition, OCR), reads its internal state through an optional
instrumentation protocol, and verifies that what's *actually on screen*
matches expectations.

Tests are plain, human-readable YAML:

```yaml
id: MOV-001
name: Movie artwork appears
feature: Movies
tags: [smoke, movies, visual]
platforms: [android, yocto]
steps:
  - action: backend.set
    data:
      movieId: 123
  - action: wait_until
    condition:
      type: image_present
      image: movie_123.png
      threshold: 0.90
    timeout: 10s
```

Supported today: **backend REST APIs**, **Android** (ADB), **web browsers** (Playwright), and **Yocto /
embedded Linux** (SSH with pluggable screenshot providers). The engine is
platform-agnostic — new device adapters plug in without touching the core.

## Quick Start

```text
Windows:
    .\install.ps1

macOS/Linux:
    ./install.sh

Then:
    argus validate
    argus run --tag smoke
```

No hardware yet? Run the example suite against the built-in fake devices:

```bash
argus run --config config/fake.yaml
```

## Everyday commands

```bash
argus init                        # create your user configuration
argus validate                    # full environment diagnosis
argus validate --framework-only   # installation check (no devices needed)
argus --dry-run                   # validate everything a run would use, execute nothing
argus list --feature movies       # browse tests
argus run --feature movies        # run a feature
argus run --platform android      # run one platform
argus run --tag smoke --continue-on-failure
argus run --max-failures 5
argus run --skip-to 68            # resume at console test number N
argus run --no-logs               # progress only (hide timestamped INFO lines)
argus run --save-comparisons      # keep actual/expected/diff for HTML report
argus version                     # print framework version
argus update                      # reinstall deps after git pull, then validate
```

Every run writes artifacts and reports under `results/<timestamp>/`:
console output, `report.json`, `junit.xml`, and `report.html` (tests grouped
by feature, with failure details and embedded `actual` / `expected` / `diff`
screenshots). The console prints the HTML report path when the run finishes.
Use `--save-comparisons` (or `results.save_comparison_images: true`) to keep
comparison images for passing image verifies too. Each failure also keeps
device logs and instrumentation state beside those images.

See `argus --help` / `argus run --help` for the full option list.

## Documentation

| Topic | Where |
| --- | --- |
| Installation (all platforms) | [docs/installation.md](docs/installation.md) |
| Getting started | [docs/getting-started.md](docs/getting-started.md) |
| Writing tests | [docs/test-authoring.md](docs/test-authoring.md) |
| Configuration reference | [docs/configuration.md](docs/configuration.md) |
| Architecture | [docs/architecture.md](docs/architecture.md) |
| Device adapters | [docs/adapters.md](docs/adapters.md) |
| Android setup | [docs/android.md](docs/android.md) |
| Yocto / embedded setup | [docs/yocto.md](docs/yocto.md) |
| Web browser setup | [docs/browser.md](docs/browser.md) |
| Image verification | [docs/image-verification.md](docs/image-verification.md) |
| App instrumentation protocol | [docs/instrumentation.md](docs/instrumentation.md) |
| CLI reference | [docs/cli.md](docs/cli.md) |
| Plugin development | [docs/plugin-development.md](docs/plugin-development.md) |
| Troubleshooting | [docs/troubleshooting.md](docs/troubleshooting.md) |

## Design in one diagram

```text
Test YAML ──> Test Engine ──> Backend / Device / Instrumentation adapters
                                  │
                                  ▼
                             Observation ──> Verifiers ──> Result
                                                             │
                                          Console / JSON / JUnit / HTML
```

The engine knows nothing about ADB, SSH, or HTTP specifics; those live behind
adapter interfaces. Instrumentation is diagnostic only — a test passes only
when the *externally observed* screen matches, never on the application's
say-so. The CLI is a thin client of the engine's service API
(`TestRunner`), so a future GUI can drive the same engine and subscribe to
the same event stream.

## Developing

```bash
./install.sh --dev
.venv/bin/python -m pytest      # framework self-tests (no hardware needed)
.venv/bin/ruff check src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Updating

```bash
git pull
argus update    # reinstalls dependencies, preserves your configuration, revalidates
```

## License

MIT — see [LICENSE](LICENSE).
