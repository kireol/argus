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
    serial: ${ANDROID_SERIAL}       # optional if exactly one device is connected
    app_package: com.example.app    # enables lifecycle operations
    app_activity: .MainActivity     # optional; monkey launcher is used otherwise
    adb_path: adb                   # optional
    command_timeout: 30             # seconds, optional
    instrumentation:                # optional, see instrumentation.md
      base_url: http://127.0.0.1:8085
```

Tip for emulator instrumentation: forward the app's instrumentation port to
the host once per boot: `adb forward tcp:8085 tcp:8085`.

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
| Screen size / DPI | `wm size` / `wm density` |
| Logs | `adb logcat -d -t <lines>` |

Keys accept short names: `key: HOME` becomes `KEYCODE_HOME`.

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
utf validate            # shows ADB, device, app, instrumentation, screenshot
utf --dry-run
```

Typical issues are listed in [troubleshooting.md](troubleshooting.md).
