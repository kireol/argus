# Installation

The goal: **clone → install → validate → run tests**, with no Python
knowledge required.

## Prerequisites

- Git
- Python 3.12+ **or** [uv](https://docs.astral.sh/uv/) (the installer prefers
  uv and will use it to provision Python automatically)
- Optional, per platform:
  - **Android testing:** Android platform-tools (`adb` on PATH)
  - **OCR (text verification):** the `tesseract` binary
  - **Yocto testing:** nothing extra — SSH support is pure Python (paramiko)

## Windows

```powershell
git clone https://github.com/kireol/argus
cd argus
.\install.ps1
```

The installer lives at the repository root and installs both the engine
(`argus/`) and the Argus Test Creator (`argus-test-creator/`).

Open a **new terminal** afterwards (the installer adds a user-level launcher
to your PATH), then:

```powershell
argus --version
argus validate --framework-only
```

## macOS / Linux

```bash
git clone https://github.com/kireol/argus
cd argus
./install.sh
```

```bash
argus --version
argus validate --framework-only
```

If `argus` is not found, add `~/.local/bin` to your PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"   # add to ~/.zshrc or ~/.bashrc
```

## What the installer does

1. Detects your OS, architecture, and a compatible Python (or uv).
2. Creates one virtual environment in `.venv/` at the repository root,
   shared by both projects.
3. Installs the engine (`argus/`, with the `yocto` and `ocr` extras) and the
   Test Creator (`argus-test-creator/`, with the `ui` extra) from their
   `pyproject.toml` files.
4. Installs user-level `argus` and `argus-test-creator` launchers — no admin
   rights, no system PATH changes on macOS/Linux; user PATH only on Windows.
5. Creates the `argus/results/` directory.
6. Runs a health check (`argus version`, `argus --help`,
   `argus validate --framework-only`, `argus-test-creator --help`).

Running the installer again is safe: it upgrades the existing installation
and never touches your configuration.

`./install.sh --dev` / `.\install.ps1 -Dev` add the development tooling of
both projects (pytest, ruff, mypy, pytest-qt).

Run `argus` from the `argus/` directory (or pass `--config`): the default
`test_paths: ["test_suites"]` and the `results/` directory are relative to
the working directory.

## Optional dependency groups

| Extra | Installs | Needed for |
| --- | --- | --- |
| *(core)* | pydantic, PyYAML, httpx, OpenCV, Pillow, typer, rich | everything else |
| `argus[yocto]` | paramiko | SSH/Yocto devices |
| `argus[ocr]` | pytesseract | `text_present` / `text_not_present` (plus the `tesseract` binary) |
| `argus[dev]` | pytest, ruff, mypy, pytest-httpserver | developing the framework |

The installer includes `yocto` and `ocr` by default because they are small;
the external binaries (`adb`, `tesseract`) remain optional and are only
checked when tests actually need them.

## First-run configuration

```bash
argus init
```

creates a commented user configuration (location is platform-appropriate and
printed by the command). Fill in your backend URL and devices — secrets stay
in environment variables:

```bash
export BACKEND_URL=https://backend.test.internal
export BACKEND_TOKEN=...       # never committed, never logged
```

Then check everything:

```bash
argus validate
```

## Restricted / offline networks

Dependency installation uses standard Python package-index configuration, so
an internal PyPI mirror works out of the box:

```bash
export PIP_INDEX_URL=https://pypi.internal.example/simple      # pip
export UV_INDEX_URL=https://pypi.internal.example/simple       # uv
```

The framework itself never calls external cloud services.

## Updating

```bash
git pull
argus update
```

`argus update` reinstalls dependencies if they changed, re-runs framework
validation, and never modifies your user configuration.

## Uninstalling

Nothing is installed system-wide. To remove the framework:

1. Delete the repository clone (this removes `.venv/` too).
   **Careful:** `argus/results/` lives inside the repository — move it first
   if you want to keep test results.
2. Delete the launchers: `~/.local/bin/argus` and
   `~/.local/bin/argus-test-creator` (macOS/Linux) or
   `%LOCALAPPDATA%\argus\bin` (Windows).
3. Optionally delete your user configuration directory (printed by
   `argus init`) — keep it if you may reinstall later.

## Troubleshooting installation

**"Python 3.12 or newer is required"** — install a newer Python (or uv) and
re-run the installer; it prints the exact command for your platform.

**Corporate TLS interception breaks downloads** — point pip/uv at your
internal mirror (above), or ask IT for the corporate CA bundle and set
`REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE`.

**`argus` not found after install** — open a new terminal; if it persists,
check the PATH note the installer printed.

More in [troubleshooting.md](troubleshooting.md).
