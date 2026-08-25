# iOS

Argus drives iOS apps — in the Simulator or on a physical device — through
[WebDriverAgent](https://github.com/appium/WebDriverAgent) (WDA), Appium's
open-source iOS automation server. No Appium server is needed: Argus talks
to WDA's HTTP API directly.

| Operation | Implementation |
| --- | --- |
| Connect | `GET /status`, then `POST /session` for `bundle_id` |
| Screenshot | `GET /screenshot` (base64 PNG) |
| Screen size | `window/size` (points) × `wda/screen` scale = pixels |
| Start / stop app | `wda/apps/launch` / `wda/apps/terminate` |
| Reset app | terminate + launch (WDA cannot wipe app data) |
| App running? | `wda/apps/state == 4` |
| Tap, swipe, long press, drag, multi-touch, pinch | W3C Actions (`POST /session/<id>/actions`), one touch input source per finger |
| Keys | `HOME` → `wda/homescreen`; `VOLUME_UP` / `VOLUME_DOWN` / `LOCK` → `wda/pressButton`; anything else is typed with `wda/keys` (`ENTER` = newline, `DEL` = backspace) |
| Logs | optional `log_command` subprocess (see below) |

Coordinates in tests are **screenshot pixels**, exactly as on Android; the
adapter converts to WDA points using the device scale.

## Prerequisites

1. macOS with Xcode.
2. WebDriverAgent running against your target. Clone it, open
   `WebDriverAgent.xcodeproj`, and run the `WebDriverAgentRunner` scheme's
   tests on the simulator or device (or from a terminal):

   ```bash
   xcodebuild -project WebDriverAgent.xcodeproj \
     -scheme WebDriverAgentRunner \
     -destination 'id=<udid>' test
   ```

   Physical devices need a signing team and a unique bundle id for the
   runner (Xcode → Signing & Capabilities), plus Developer Mode enabled on
   the device.
3. Reachability: simulators listen on `http://127.0.0.1:8100`. For a device
   either forward the port (`brew install libimobiledevice && iproxy 8100 8100`)
   or use the device's Wi‑Fi IP in `url`.
4. The app under test installed on the target.

## Configuration

```yaml
devices:
  iphone:
    type: ios
    platform: ios
    bundle_id: com.example.app          # required
    url: http://127.0.0.1:8100          # optional, WebDriverAgent base URL
    timeout: 30                         # optional, seconds per request
    # Optional log source (enables log_contains / get_logs):
    log_command: xcrun simctl spawn booted log stream --style compact --predicate 'process == "Example"'
    # Physical device alternative:
    # log_command: idevicesyslog -u <udid>
```

## Gestures

All `device.*` gestures from [test-authoring.md](test-authoring.md) work on
iOS, including `device.pinch` and `device.multi_touch`; WebDriverAgent runs
every finger's sequence concurrently.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Cannot reach WebDriverAgent` | WDA is not running or the port is not forwarded. Re-run the `xcodebuild … test` command and keep it running. |
| `WebDriverAgent error 'invalid session id'` | WDA restarted mid-run; start the run again. |
| `did not return a session id` | The app is not installed for that `bundle_id`; check WDA's log. |
| Black or wrong-size screenshots | Unlock the device; verify `bundle_id`. |
| `log_command binary not found` | Install Xcode (`xcrun`) or libimobiledevice (`idevicesyslog`), or drop `log_command`. |
