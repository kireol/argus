# Argus

**Cross-platform functional & visual testing — an engine that runs declarative
YAML tests against real applications, and a desktop app that helps you write
them.**

This repository holds two projects:

| Directory | Command | What it does |
|-----------|---------|--------------|
| [`argus/`](argus/README.md) | `argus` | The test engine and CLI. Drives your backend into a known state, observes the app from the outside (screenshots, image recognition, OCR) and verifies what is actually on screen. Targets: backend REST APIs, Android, iOS, web browsers, desktop apps, Roku, Apple TV, ESP32, Yocto / embedded Linux. Includes an MCP server and first-class CI/CD support. |
| [`argus-test-creator/`](argus-test-creator/README.md) | `argus-test-creator` | The visual authoring companion. Record interactions (browser, desktop, Android, fake demo target), add assertions from what you see, edit steps with undo/redo, validate, and export YAML that runs with Argus alone. |

Argus is the engine; the Creator is the authoring experience; YAML is the
portable contract between them. The Creator never imports Argus — it drives
the installed `argus` CLI.

## Installation

One installer sets up both projects in a shared virtual environment
(`.venv/` at the repository root) and installs the `argus` and
`argus-test-creator` commands:

```text
Windows:
    .\install.ps1          # add -Dev for development tooling

macOS/Linux:
    ./install.sh           # add --dev for development tooling
```

Requires Python 3.12+ (or [uv](https://docs.astral.sh/uv/), which provisions
one). No administrator rights are needed and no user configuration is
touched. Details: [argus/docs/installation.md](argus/docs/installation.md).

## Quick start

```bash
cd argus
argus validate                          # check your environment
argus run --config config/fake.yaml     # demo run against the fake devices
argus-test-creator demo                 # open the Creator on the built-in demo target
```

Run `argus` from `argus/` (or pass `--config`): the default `test_paths`
and `results/` directory are relative to the working directory.

## Layout

```text
.
├── install.sh / install.ps1   installers (both projects)
├── action.yml                 GitHub Action — `uses: kireol/argus@v1`
├── pyproject.toml             uv workspace root (not a package)
├── CHANGELOG.md · LICENSE · CONTRIBUTING.md
├── argus/                     package `argus`  — src/, tests/, docs/, examples/, config/
└── argus-test-creator/        package `argus-test-creator` — src/, tests/, docs/, examples/
```

## Documentation

- Engine: [argus/README.md](argus/README.md) and [argus/docs/](argus/docs/)
  (getting started, configuration, test authoring, each device adapter,
  CI/CD, MCP, plugin development, troubleshooting).
- Creator: [argus-test-creator/README.md](argus-test-creator/README.md) and
  [argus-test-creator/docs/](argus-test-creator/docs/) (recording, assertions,
  Android recording, packaging).
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Changes: [CHANGELOG.md](CHANGELOG.md).

## CI

```yaml
- uses: kireol/argus@v1
  with:
    suite: pr
```

See [argus/docs/ci-cd.md](argus/docs/ci-cd.md).

## License

MIT — see [LICENSE](LICENSE).
