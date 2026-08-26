# Argus Test Creator

**Visual authoring of [Argus](https://github.com/kireol/argus) tests — record, observe, verify, export YAML.**

Argus is a cross-platform functional and visual testing engine driven by declarative YAML.
Argus Test Creator is a separate desktop application that makes it dramatically easier for a
QA engineer to *create* those tests without writing YAML by hand:

```text
Create Test → Choose target → Record / build → Observe → Add assertions
           → Review & edit steps → Validate → Save YAML + assets → Run with Argus
```

* **Argus is the engine.** The Creator never executes tests itself; it invokes the installed
  `argus` CLI.
* **The Creator is the authoring experience.** Recorder, wizard, assertion authoring, editor,
  import/export.
* **YAML is the portable contract.** Everything the Creator saves runs with Argus alone — the
  Creator does not need to be installed to run the tests it produced.

## Why a separate application?

Argus's architecture keeps the test engine platform-agnostic with device details behind
adapters. A GUI recorder has entirely different concerns (live screens, input capture, image
cropping, undo/redo, background OCR). Keeping it separate keeps Argus small and lets the Creator
evolve — including future AI-assisted authoring — without touching the engine. See
[docs/architecture.md](docs/architecture.md).

## Installation

Requires Python 3.12+ and an Argus installation (`argus --version` must work, or set
`ARGUS_EXECUTABLE`).

The Creator lives in the [kireol/argus](https://github.com/kireol/argus)
monorepo next to the engine; the root installer sets up both in one shared
`.venv/` at the repository root:

```bash
git clone https://github.com/kireol/argus
cd argus
./install.sh                                    # Windows: .\install.ps1 — installs argus + argus-test-creator
../.venv/bin/playwright install chromium        # optional, for browser recording (run from argus-test-creator/)
brew install tesseract                          # macOS; apt install tesseract-ocr on Debian/Ubuntu

# extra recorder dependencies (from argus-test-creator/):
uv pip install --python ../.venv/bin/python -e ".[ocr,browser,desktop]"

# end users (packaged builds): see docs/packaging.md
```

Optional extras: `ui` (PySide6, required for the GUI), `ocr` (pytesseract), `browser`
(Playwright), `desktop` (pynput + mss).

Check your environment at any time:

```bash
argus-test-creator doctor
```

## Quick start (no hardware needed)

```bash
argus-test-creator gui                  # launch, then File → New Project…
```

1. Choose **Movies Demo (fake target)** and press **Connect** — a live view of the built-in
   demo app appears.
2. Press **● Record**. Click in the live view (Movies → Search → click the input), type
   `Batman` in the remote's text box, press **Enter**.
3. The Creator captures a screenshot after every action, runs OCR in the background and
   *suggests* verifications ("New text detected: Batman Begins"). Press **Add** on one.
4. Press **Add Verification**, drag a rectangle around the *Batman Begins* row, choose
   **Image is visible**, name the asset, **Insert verification**.
5. Fill in **Test details** (ID, name, feature). Watch the **YAML** tab update.
6. **Save**, then **Run with Argus**. The Run tab shows Argus's output and a link to its HTML
   report.

The same flow scripted, without the GUI:

```bash
argus-test-creator demo ./movies-demo --run
```

## Recording a first real test (web)

```bash
python -m argus_test_creator.demo.web_server        # serves the demo at http://127.0.0.1:3210/
argus-test-creator gui ./my-project
```

Choose **Web browser (chromium)** → **Settings…** → enter the URL → **Connect**. A Chromium
window opens; press **● Record** and use the application normally. Clicks, typing, scrolling and
navigation are recorded with viewport coordinates plus DOM evidence (kept as metadata only).
See [docs/recording.md](docs/recording.md) for desktop and Android.

## Exporting to Argus and running

A Creator project *is* an Argus project:

```text
my-project/
├── argus.yaml            # devices, test_paths, asset_paths — generated, safe to edit
├── tests/MOV-001.yaml    # the generated Argus test
├── assets/images/*.png   # image assets you accepted
└── .argus-creator/       # Creator-only state (provenance, sessions); safe to delete
```

```bash
argus run --config my-project/argus.yaml --test MOV-001      # Argus alone
argus-test-creator validate my-project --argus               # Creator rules + Argus validation
argus-test-creator export my-project --out ./export          # YAML + referenced assets
```

## CLI

```text
argus-test-creator version            Creator and detected Argus versions
argus-test-creator new DIR            create an empty project
argus-test-creator gui [DIR]          launch the desktop app
argus-test-creator validate DIR       validate tests (add --argus to run `argus validate`)
argus-test-creator export DIR         regenerate YAML and copy assets
argus-test-creator doctor [DIR]       environment diagnostics
argus-test-creator demo DIR [--run]   scripted demo recording (fake target)
```

## Documentation

* [Getting started](docs/getting-started.md)
* [Architecture](docs/architecture.md)
* [Recording guide](docs/recording.md) — browser, desktop, Android, capabilities and limits
* [Android recording](docs/android-recording.md) — ADB setup, `getevent`-based touch/key
  recording, device differences
* [Assertion guide](docs/assertions.md) — visual, OCR, regions, suggestions, synchronization
* [Argus integration](docs/integrations.md)
* [Plugin / developer guide](docs/plugin-development.md)
* [Packaging](docs/packaging.md)
* [Troubleshooting](docs/troubleshooting.md)

## Development

```bash
../install.sh --dev                    # once, from the repository root: ./install.sh --dev
scripts/dev.sh                         # ruff + mypy + pytest (unit, integration, UI, performance)
../.venv/bin/python -m pytest tests/unit
```

Tests that need Argus, Playwright/Chromium or Tesseract skip themselves when those are missing.

## License

MIT — see [LICENSE](LICENSE).
