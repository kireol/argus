# Recording guide

## Modes

* **Smart** (default): deterministic normalization — pointer down/move*/up becomes one
  drag/swipe/long-press/tap; two quick taps at one spot become a double-tap; consecutive
  printable keys become one "Type 'Batman'" group (emitted as `device.key` steps, one per
  character, because Argus has no text action); pointer moves without a button are dropped.
* **Exact**: one action per discrete event. Use for reproducing bugs or unusual gestures.
  Switch with `recording.mode` or re-normalize a session after recording.

Idle gaps are *never* turned into fixed `wait` steps. Use **Add Verification → wait for it**.

## Capabilities

Each target reports what it can do; the UI adapts. From `targets/catalog.py` (derived from the
Argus adapters):

| Target | Input recorded | Input sent by Creator | Screenshots | OCR | Notes |
| --- | --- | --- | --- | --- | --- |
| Fake (Movies demo) | via Creator | tap, key, text, drag | yes | fake (deterministic) | basis for tests |
| Web browser | click, drag, keys, scroll, navigation | — | yes | tesseract | DOM evidence kept as metadata |
| Desktop | mouse, keys, scroll | — | yes (primary monitor) | tesseract | needs OS permissions |
| Android | **not observed** | tap, key, text, swipe via ADB | yes (`screencap`) | tesseract | every sent input is recorded exactly |
| iOS / Roku / tvOS / Apple TV / ESP32 / Yocto | no recorder yet | — | per Argus adapter | — | author with the wizard |

## Browser (Playwright)

Settings: `url`, `browser` (chromium/firefox/webkit), `viewport`, `headless` (recording is headed
by default so you can interact). A script injected into every page reports `pointerdown/up/move`,
`keydown`, `wheel` with viewport coordinates and light DOM evidence (tag, id, accessible name,
bounding box). Navigation events are recorded as `log` steps. Coordinates match Argus's browser
adapter (`device.tap` = `page.mouse.click`). Argus runs the exported test headless.

## Desktop (pynput + mss)

`pip install 'argus-test-creator[desktop]'`. Records global mouse/keyboard on the primary
monitor (setting `monitor`). Coordinates are screenshot pixels (HiDPI scale applied) like
Argus's desktop adapter. On macOS grant **Screen Recording** and **Input Monitoring** to the
terminal/app running the Creator. Keep the Creator window outside the area you are testing.
Set `command` in target settings so Argus can launch the application (`device.start`).

## Android (ADB)

Requires `adb` on `PATH` (or `adb_path`). Settings: `serial`, `app_package`, `app_activity`.
The Creator sends taps (click the live view), keys and text (remote panel), and swipes through
`adb shell input`; screenshots come from `adb exec-out screencap -p`. Touching the physical
screen is not observed in this version — the adapter says so in its limitations.

## Session storage and recovery

Every recording lives under `.argus-creator/sessions/<stamp>/` (`events.jsonl`,
`actions.jsonl`, `session.json`, `screenshots/`). Writes are append-only and fsync'd every 25
events; checkpoints are atomic. If the Creator crashes, **Target → Recover interrupted
recording…** rebuilds the actions from the journal.

## Performance settings

`recording.settle_ms` (delay before the after-action capture), `recording.live_preview_fps`,
`recording.capture_after_actions` (turn off for very long sessions), `workers`.
