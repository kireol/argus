# Web Browser

The browser adapter drives a web application through **Playwright**. Argus
treats the page like any other screen: verification is visual (OpenCV / OCR),
taps are mouse clicks, swipes are drags, and the browser console is the
device log.

## Prerequisites

```bash
pip install "argus[browser]"
playwright install chromium        # or firefox / webkit
```

## Configuration

```yaml
devices:
  web:
    type: browser
    platform: web                   # so tests can filter with platforms: [web]
    url: http://localhost:3000/     # required — opened by start_application
    browser: chromium               # chromium (default) | firefox | webkit
    headless: true                  # false to watch the run
    viewport: [1280, 720]           # screenshot size in CSS pixels
    timeout: 30                     # seconds for navigation / actions
    instrumentation:                # optional, see instrumentation.md
      base_url: http://localhost:3000
```

Test filtering matches on `DeviceConfig.effective_platform` (the `platform`
option if set, else `type`), not `Device.platform`. Without the `platform:
web` line above, this device's filter label defaults to `browser` (its
`type`), so `platforms: [web]` would not select it.

## What the adapter does

| Operation | Implementation |
| --- | --- |
| Connect | launch browser, new context at `viewport`, open `url` |
| Screenshot | `page.screenshot()` → RGB image |
| Start app | `page.goto(url)` (also clears captured console logs) |
| Stop app | navigate to `about:blank` |
| Reset app | stop + start (fresh navigation) |
| Tap | `mouse.click(x, y)` |
| Swipe | mouse down → move (stepped over `duration_ms`) → up |
| Key | `keyboard.press`; Android names map (`DPAD_LEFT` → `ArrowLeft`, `BACK` → `Escape`, `ENTER` → `Enter`) |
| Logs | last N `console` events as `"<type>: <text>"` lines |
| Screen size | viewport size |

Coordinates are CSS pixels in the viewport, matching the screenshot.

## Asserting on console output

```yaml
- action: wait_until
  timeout: 5s
  condition:
    type: log_contains
    pattern: "^error: "
```

See `log_contains` in [test-authoring.md](test-authoring.md#conditions).

## Limitations

- Argus does not inspect the DOM; there are no CSS-selector actions. Use
  image/text verification like on Android. Selector support could be added as
  a plugin action if needed.
- One page per device. Pop-ups/new tabs are not tracked.
- On failure the artifacts folder contains `logs.txt` with the browser console.
