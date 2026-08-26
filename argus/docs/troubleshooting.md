# Troubleshooting

First stop, always:

```bash
argus validate          # what exactly is broken, with remediation hints
argus --dry-run         # would a run work, without touching anything
```

Every framework error carries a `Remediation:` line — read it before
anything else. Add `--verbose` for DEBUG logs.

## Installation

**`INSTALLATION FAILED — Python 3.12 or newer is required`**
Install Python 3.12+ (or [uv](https://docs.astral.sh/uv/), which the
installer will use to provision Python itself) and re-run the installer.

**`argus: command not found` after installing**
Open a new terminal. Still missing → macOS/Linux: ensure `~/.local/bin` is
on PATH; Windows: the installer added `%LOCALAPPDATA%\argus\bin` to your user
PATH — a new terminal is required.

**Package downloads fail (corporate network)**
Point pip/uv at your internal mirror:
`export PIP_INDEX_URL=... UV_INDEX_URL=...`

## Pre-flight failures

**`✗ Test assets — Missing reference images`**
The listed PNGs aren't under any `asset_paths` directory. Check the
filename in the test (variables are expanded using the test's
`parameters`).

**`✗ Backend API`**
`backend.base_url` unset (`argus validate` shows *not configured*), backend
down, or TLS/auth problem. Try the health endpoint manually:
`curl -i "$BACKEND_URL/health"`.

**`✗ Device: android — No Android devices/emulators detected`**
`adb devices` must list the target as `device` (not `offline` /
`unauthorized`). Start the emulator, accept the USB-debugging prompt, or
set `devices.<name>.serial`.

**`✗ Screenshot: <yocto device> — Unable to capture display`**
The configured `screenshot.command` failed on the device. SSH in and run it
by hand; check the display server is up and the command exists in the
image. See [yocto.md](yocto.md) for per-display-stack commands.

**`✗ Device: <yocto> — SSH connection failed ... host key`**
Host-key verification is secure-by-default. Add the device to your
known_hosts (`ssh user@host` once) or set `host_key_policy: auto_add` for
lab devices.

**`⚠ OCR unavailable`**
Only matters for tests using `text_present`/`text_not_present`. Install
`pip install "argus[ocr]"` plus the `tesseract` binary
(`brew install tesseract` / `apt install tesseract-ocr`).

## Test failures

**Image not found but it *is* on screen**
Open the failure's `actual.png` and compare with `expected.png`:
- Resolution/scale differs from where the reference was captured →
  recapture on this device or set `scale_tolerance: 0.1`.
- The confidence in the message tells you how close it was; `0.85` against
  a `0.90` threshold usually means a slightly-off reference, `0.3` means
  it's genuinely not there.
- Background around the reference changed → crop the reference tighter,
  or search within a `region:`.

**Test flaky: passes on retry**
Don't widen retries. Find the missing synchronization — replace fixed
`wait` steps with `wait_until` on the actual condition, and add an
instrumentation readiness check if the app exposes one.

**`Unresolved variable ${...}`**
The step references a variable not defined in the test's `parameters`,
config `variables`, or (for config files) the environment.

**Instrumentation says ready but the verify fails**
That's the framework working as designed: internal state and the screen
disagree. The bug is in the application's rendering path; the
`instrumentation.json` artifact is your evidence.

**Everything times out on one device**
Device connections are reused per run; a device that hung mid-run fails
fast with `DeviceConnectionError`. Power-cycle it and check
`argus validate`.

## Where things are

| What | Where |
| --- | --- |
| Run reports | `results/<timestamp>/report.{json,html}`, `junit.xml` |
| Failure artifacts | `results/<timestamp>/<TEST-ID>_<platform>/` |
| Preflight report (on failure) | `results/<timestamp>/preflight.json` |
| User configuration | printed by `argus init` |

## Still stuck

Run with maximum context and file an issue with the output plus the
failure's artifact directory:

```bash
argus run --test THE-TEST --verbose --log-level DEBUG
```
