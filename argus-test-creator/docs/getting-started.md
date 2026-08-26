# Getting started

## 1. Install

```bash
./install.sh                                   # repository root; installs argus + argus-test-creator into .venv/
cd argus-test-creator
uv pip install --python ../.venv/bin/python -e ".[ocr,browser]"   # optional recorder extras
../.venv/bin/playwright install chromium
argus-test-creator doctor            # confirms Argus, Playwright, Tesseract, ADB, permissions
```

If `doctor` cannot find Argus: install it (see the Argus README) and either add `argus` to `PATH`,
set `ARGUS_EXECUTABLE=/path/to/argus`, or put `argus: {executable: /path/to/argus}` in your user
config (`argus-test-creator doctor` prints where that file lives on your OS).

## 2. Create a project

```bash
argus-test-creator new ./my-project
argus-test-creator gui ./my-project
```

## 3. Record

* Pick a target in the header. Press **Settings…** to enter a URL/serial/command.
* **Connect** — the live view shows the target's screen (configurable FPS).
* **● Record**. Interact with the real application (browser/desktop) or, for controlled
  targets (demo, Android), click in the live view and use the remote.
* **Pause** to do something you don't want recorded; **■ Stop** to convert the recording into
  steps.

## 4. Verify

* Watch the **Suggested verifications** panel; **Add** the useful ones.
* **Add Verification** opens the current screenshot: drag a region (image assertion), pick
  detected text (OCR assertion), choose *wait for it* (recommended) or *check once*.

## 5. Edit

Double-click a step to edit; right-click for rename/duplicate/disable/convert/provenance;
drag to reorder; **Undo/Redo** everywhere. **Test details** holds ID, name, feature, tags,
platforms, priority, timeout, devices, parameters, retry.

## 6. Validate, save, run

**Validate** lists actionable issues; **Run → Validate with Argus** additionally asks Argus.
**Save** writes `tests/<ID>.yaml`, assets and `argus.yaml`. **Run with Argus** saves, validates,
runs and shows the result and HTML report.

## Configuration

Sources, in increasing priority: built-in defaults → user config (`config.yaml` in the
platform config dir) → project `.argus-creator/config.yaml` → environment
(`ARGUS_CREATOR_<SECTION>__<KEY>`, e.g. `ARGUS_CREATOR_RECORDING__MODE=exact`,
`ARGUS_EXECUTABLE`) → explicit overrides.

```yaml
recording:
  mode: smart            # smart | exact
  settle_ms: 150         # wait before the after-action screenshot
  capture_after_actions: true
  live_preview_fps: 4
  suggest_assertions: true
ocr:
  provider: tesseract    # tesseract | fake
  language: eng
argus:
  executable: null       # path, or leave null for discovery
  run_timeout: 600
diagnostic: false        # show technical details for every failure
workers: 4
targets:                 # extra target profiles (TargetProfile fields)
  my-phone:
    name: Pixel 8
    adapter: android
    platform: android
    argus_device_type: android
    settings: {serial: 1A2B3C}
```
