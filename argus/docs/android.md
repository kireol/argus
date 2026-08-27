# Android

The Android adapter drives emulators and physical devices through **ADB**
only — no Android Studio, no Appium.

## Prerequisites

- [Android platform-tools](https://developer.android.com/tools/releases/platform-tools)
  with `adb` on PATH (or set `adb_path` in the device config)
- A running emulator or a connected device with USB debugging enabled
  (`adb devices` must list it as `device`)

## Configuration

```yaml
devices:
  android:
    type: android
    serial: ${ANDROID_SERIAL}       # optional; see "Device selection" below
    app_package: com.example.app    # enables lifecycle operations
    app_activity: .MainActivity     # optional; monkey launcher is used otherwise
    adb_path: adb                   # optional
    command_timeout: 30             # seconds, optional
    input_device: /dev/input/event2 # optional: touchscreen for pinch/multi-touch
    instrumentation:                # optional, see instrumentation.md
      base_url: http://127.0.0.1:8085
```

Tip for emulator instrumentation: forward the app's instrumentation port to
the host once per boot: `adb forward tcp:8085 tcp:8085`.

## Device selection

When `serial` is not set, the adapter resolves the target at connect time
(which is when the pre-flight `Device:` check runs):

1. The `ANDROID_SERIAL` environment variable, if set.
2. If exactly one device/emulator is listed by `adb devices`, it is used.
3. If several are connected and Argus is running in a terminal, it prints a
   numbered list and asks which one to use:

   ```text
   Multiple Android devices detected:
     1. emulator-5554
     2. emulator-5556

   Warning: Tip: skip this prompt by setting the ANDROID_SERIAL environment
   variable, e.g.
       export ANDROID_SERIAL=emulator-5554
   Enter the number of the device to use [1-2]:
   ```

   Non-interactive runs (CI, the MCP server) fail the check instead, with the
   same `ANDROID_SERIAL` hint.

## What the adapter does

| Operation | Implementation |
| --- | --- |
| Detect devices | `adb devices` (used by `connect` and preflight) |
| Screenshot | `adb exec-out screencap -p` (binary-safe) |
| Start app | `am start -n <package>/<activity>` or monkey launcher |
| Stop app | `am force-stop <package>` |
| Reset app | `pm clear <package>` (wipes app data) + start |
| App running? | `pidof <package>` |
| Tap / swipe / key | `input tap` / `input swipe` / `input keyevent KEYCODE_*` |
| Long press | `input swipe x y x y <duration>` (zero-length swipe) |
| Drag (hold, then move) | `input draganddrop` on Android 11+ (API 30) — the platform picks the hold time, so `hold:` is ignored there; older builds use the multi-touch stream below, honouring `hold:` |
| Pinch / multi-touch | evdev multi-touch (protocol B) events via `sendevent`, see below |
| Screen size / DPI | `wm size` / `wm density` |
| Logs | `adb logcat -d -t <lines>` |
| Metrics | `/proc/loadavg`, `/proc/uptime`, `/proc/<pid>/stat` (app uptime from starttime), `/proc/<pid>/status`, `/proc/meminfo`, `dumpsys gfxinfo <package>` (jank, unique App FPS, frame percentiles), and `dumpsys SurfaceFlinger --latency` (display FPS from the vsync period). Sampled in the background during each test. |

Keys accept short names: `key: HOME` becomes `KEYCODE_HOME`.

### Multi-touch (pinch, rotate, drag on old builds)

`adb shell input` cannot press two fingers at once, so `device.pinch` and
`device.multi_touch` write raw Linux input events to the touchscreen with
`sendevent`, batched into a single `sh -c` script (one adb round-trip per
gesture). The adapter finds the touchscreen once from `getevent -p` — the
first device that reports `ABS_MT_POSITION_X/Y` — and scales screen pixels
to its axis ranges. Override the discovery with:

```yaml
devices:
  phone:
    type: android
    input_device: /dev/input/event2   # send screen pixels straight to this device
```

Writing to `/dev/input/event*` works on emulators and rooted devices
(`adb root`). On a locked-down production device the step fails with a
`DeviceCapabilityError` naming the device and the permission problem.

## Multiple devices

Give each device an entry and serial; tests pin devices with:

```yaml
requires:
  devices: [android-tv-1]
```

With several devices attached and no `serial` configured, `connect` fails
with the detected serial list rather than guessing.

## Verifying the setup

```bash
argus validate            # shows ADB, device, app, instrumentation, screenshot
argus --dry-run
```

Typical issues are listed in [troubleshooting.md](troubleshooting.md).
