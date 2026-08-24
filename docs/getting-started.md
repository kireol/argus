# Getting Started

This walkthrough goes from a fresh clone to your first passing visual test.

## 1. Install and verify

```bash
./install.sh            # Windows: .\install.ps1
argus validate --framework-only
```

You should see `Framework: READY`.

## 2. Run the demo suite (no hardware)

The repository ships fake devices that *render* the fake backend's state
into screenshots, so the whole pipeline — backend state change, screenshot
capture, OpenCV matching, OCR — runs for real:

```bash
argus run --config config/fake.yaml
```

Expected output (abridged):

```text
PRE-FLIGHT
────────────────────────────────────────
✓ Configuration
✓ Backend API
✓ Device: fake_android
...
Movies
✓ MOV-001    Movie artwork appears        193ms
✓ MOV-004    Changing movies shows new artwork
TEST RUN PASSED
```

## 3. Configure your real environment

```bash
argus init
```

Edit the created file (its path is printed) and set:

- `backend.base_url` — usually via `export BACKEND_URL=...`
- your devices — see [android.md](android.md), [yocto.md](yocto.md), and [browser.md](browser.md)

Then run the full diagnosis:

```bash
argus validate
```

Fix anything marked ✗. Items marked ⚠ or ○ are optional/not configured and
won't block runs that don't need them.

## 4. Dry run

Before touching application state, verify a real run would work:

```bash
argus --dry-run
```

This loads and validates every test, verifies assets, checks devices,
screenshots, instrumentation, and backend connectivity — and executes
nothing.

## 5. Write your first test

Create `test_suites/myfeature/first.yaml`:

```yaml
id: MY-001
name: Home screen shows logo
feature: MyFeature
tags: [smoke]
platforms: [android]
steps:
  - action: wait_until
    condition:
      type: image_present
      image: logo.png
      threshold: 0.90
    timeout: 10s
```

Capture the reference image (`logo.png`) from a real screenshot and place it
in `assets/images/`. A convenient way to grab a screenshot:

```yaml
# temporary capture test
id: CAP-001
name: Capture screen
feature: Tools
steps:
  - action: screenshot
    file: capture.png
```

Run it with artifact retention enabled (`results.retain_on_success: true` in
your config, or just look in `results/` after a failure), then crop the
region you care about with any image editor.

## 6. Run it

```bash
argus run --test MY-001
argus run --feature myfeature
argus run --tag smoke --continue-on-failure
```

## 7. Read a failure

When a visual verification fails you get everything needed to diagnose it:

```text
✗ MOV-004  Changing movies updates artwork   2.18s
    Failed step: wait_until
    Condition not met within 10.02s (38 checks): Image 'movie_456.png'
    not found (confidence 0.412, threshold 0.90)
    Instrumentation:
      movie_id: 456
      image_loaded: True
    Artifacts:
      results/2026-08-12_10-42-31/MOV-004_android/
```

The artifact directory contains `actual.png` (what the screen showed),
`expected.png` (your reference), `diff.png`, `logs.txt` (device logs),
`instrumentation.json`, and `metadata.json`. In the example above the app
*claimed* the image was loaded — instrumentation agreed with the backend —
but the screen disagreed, which is exactly the class of bug this framework
exists to catch.

## Next

- [test-authoring.md](test-authoring.md) — the full YAML reference
- [image-verification.md](image-verification.md) — thresholds, regions, tips
- [cli.md](cli.md) — every command and flag
