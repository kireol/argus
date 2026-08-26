# Recording on Android

The Android recorder observes what you do on a real device — taps, swipes, long presses,
two-finger gestures and hardware/navigation keys — and turns them into ordinary Argus steps
(`device.tap`, `device.swipe`, `device.long_press`, `device.multi_touch`, `device.key`).

Under the hood it streams `adb shell getevent`. You never see that output: it is an
implementation detail of the recorder, not part of the Argus test language.

```text
Android device → ADB → getevent → parser → gesture recognizer → Creator authoring model → Argus YAML
```

## Step by step

1. **Install ADB.** Install Android platform-tools (`brew install android-platform-tools`,
   `apt install adb`, or the SDK) so `adb` is on `PATH` — or set the `adb_path` target setting.
2. **Enable Developer Options** on the device: Settings → About phone → tap *Build number*
   seven times.
3. **Enable USB debugging** in Settings → System → Developer options.
4. **Connect the device** with a USB cable (or `adb connect host:port` for Wi-Fi debugging).
5. **Verify** — unlock the phone, accept *Allow USB debugging?*, then:

   ```bash
   adb devices -l
   # List of devices attached
   # ABC123   device usb:1-1 product:shiba model:Pixel_8 ...
   ```

   The state must read `device`. `unauthorized` means the prompt is still pending; `offline`
   usually means a bad cable (`adb kill-server && adb devices`).
6. **Launch Argus Test Creator** (`argus-test-creator gui ./my-project`) and check the
   environment with **Run → Doctor** (or `argus-test-creator doctor`). The Android section
   walks the whole chain — ADB, devices, selected device, Android version, input devices,
   touchscreen, `getevent`, screenshot — with what to do next when something is missing.
7. **Select Android** in the Target / Device box. The *Android Recorder* panel appears and
   lists connected devices. When several are connected you **must** pick one — the Creator
   never silently uses the first. Press **Connect**; the panel shows the touchscreen it
   found and the resolution, and the live view starts.
8. **Start recording** with **● Record**.
9. **Interact with the device** — tap, swipe, long-press, use hardware BACK/volume keys. The
   panel shows semantic counters only (raw events, recognized actions, current action);
   the step list fills with actions as they finish. Screenshots are taken after each
   action and used for assertion suggestions, exactly as for other targets. You can still
   click the live view or use the remote panel; those inputs are sent through
   `adb shell input` and recorded exactly (injected input is not observed by `getevent`, so
   nothing is recorded twice).
10. **Stop recording** with **■ Stop**.
11. **Review the semantic actions** in the step list — rename, reorder, delete, add
    verifications. Every step keeps provenance back to the recording session.
12. **Export / run with Argus** — **Save**, then **Run with Argus** (or
    `argus run --config my-project/argus.yaml --test <ID>`). The generated YAML contains
    only `device.*` actions; the device serial is written to `argus.yaml`.

## Target settings

| Setting | Meaning |
|---|---|
| `serial` | Device to use (`adb -s`). Required when several devices are connected. |
| `adb_path` | Path to `adb` when it is not on `PATH`. |
| `input_device` | Force a touchscreen (`/dev/input/eventN` or its name) when discovery picks the wrong one. |
| `invert_x`, `invert_y`, `swap_axes` | Coordinate fixes for panels mounted flipped/sideways. |
| `tap_max_distance_px`, `long_press_min_ms`, `tap_max_duration_ms` | Gesture thresholds (defaults 20 px, 500 ms, 500 ms). |
| `app_package`, `app_activity` | Passed to Argus for `device.start`/`device.stop`. |

## What is recognized

| You do | Recorded as | Argus step |
|---|---|---|
| Tap (< 500 ms, < 20 px movement) | tap | `device.tap` |
| Hold in place ≥ 500 ms | long press | `device.long_press` with the observed duration |
| Move the finger further than 20 px | swipe (start → end, duration) | `device.swipe` |
| Two taps at the same spot within 350 ms (smart mode) | double tap | two `device.tap` |
| Two or more fingers at once | multi-touch with each finger's path | `device.multi_touch` |
| BACK, HOME, MENU, ENTER, DPAD_*, volume, power… | key | `device.key` with the Argus key name |
| A key without a mapping | key `KEY_<NAME>` | `device.key` (edit or delete it) |

Hundreds of raw input events become one step. A pinch is recorded as a two-finger
multi-touch, not as `device.pinch`.

## Disconnection

If the cable is pulled or ADB drops mid-recording, the recording **pauses** and everything
recorded so far is kept. The panel offers **Reconnect** (same serial) and the recording
resumes; **Stop Recording** keeps what was captured. Rotating the device during a recording
is picked up within about two seconds.

## Diagnostics

**Target → Android diagnostics…** (or the panel's *Diagnostics…* button) opens the developer
view: ADB state, selected device, input device path, touchscreen detection, screen size and
rotation, raw/recognized/ignored/unknown/malformed counters and the last actions. Raw
`getevent` lines are logged at DEBUG level only (`--diagnostic`).

## Known limitations and device differences

* **`getevent` is a Linux input dump.** It reports what the touch panel and physical keys
  send to the kernel. Software-generated input (the on-screen keyboard's characters, Bluetooth
  keyboards routed through the framework, gestures synthesized by an accessibility service,
  `adb shell input`) is not observed. Typing on the on-screen keyboard is recorded as the
  taps you made; use the remote panel's text box when you want `device.key` steps instead.
* **Gesture navigation.** On devices without hardware/on-screen buttons, "Back" is an
  edge swipe and is recorded as a swipe, not as a `BACK` key.
* **Coordinates.** Touch panels report in their own units (commonly 0–4095) which the recorder
  maps to screen pixels using the ranges from `getevent -lp`, the `wm size` output and the
  current display rotation. Panels that are mounted inverted or sideways (rare, some tablets
  and automotive units) need `invert_x`/`invert_y`/`swap_axes`. Devices with a display
  *override* size (`wm size` shows an override) are mapped to the override.
* **Several touch panels.** Foldables, devices with a stylus digitizer, or units with
  external touch panels expose more than one candidate. The recorder prefers direct
  (`INPUT_PROP_DIRECT`) multi-touch panels and shows the choice in the diagnostics; use
  `input_device` to override.
* **Single-touch (protocol A / `ABS_X`) panels** are supported; multi-touch is then not
  observable.
* **Permissions.** `getevent` needs no root on stock builds. Some OEM builds restrict
  `/dev/input` for the shell user — the doctor reports `getevent` as unavailable with the
  reason. Do not root a device for this; report the OEM.
* **Timestamps.** `getevent -t` prints kernel time; the recorder aligns it with wall-clock time
  when the stream starts, so durations are exact and absolute times are approximate.
* **Screenshots** come from `screencap` after each action (asynchronously) and never per raw
  event. Very long sessions can turn `recording.capture_after_actions` off.
* **Emulators** work (`emulator-5554`); the virtual touchscreen usually reports screen pixels
  directly.

## Manual real-device checklist

The automated suite uses a fake ADB. Before a release, walk this on hardware:

1. `argus-test-creator doctor` shows every Android line ✓.
2. Connect, then tap, swipe, long-press, press BACK — each appears once as the right step.
3. Rotate the device, tap — the step's coordinates are inside the rotated screenshot.
4. Pull the cable — the pause prompt appears; reconnect — recording resumes.
5. Stop, save, `argus run` the generated test on the same device.
