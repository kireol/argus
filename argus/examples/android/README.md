# Argus Demo — Android example

A minimal single-activity Kotlin app (package `com.argus.demo`) implementing
the shared "Argus Demo" spec: a home screen with a counter and a
theme-sensitive colour swatch, and a settings screen with a dark-theme
toggle. It exposes the same logs and HTTP instrumentation contract used by
every Argus example so the same kinds of tests read the same way across
platforms.

## Prerequisites

- JDK 17+ (the build targets Java 17; JDK 21 works fine as the host JVM)
- `gradle` on PATH (this project does not commit a Gradle wrapper jar — see
  "Build" below)
- Android SDK (`platform-tools`, an API 34 platform, and a build-tools
  version) with `ANDROID_HOME`/`ANDROID_SDK_ROOT` set, and `adb` on PATH
- A running Android emulator or a connected device (`adb devices` must list
  it as `device`) with **API 26+**
- Argus installed in the repo's virtualenv (`.venv/bin/argus`) with OCR
  support for the title-text tests (see `docs/ocr.md`)

This example targets a **1080x1920 portrait** screen at xxhdpi
(density 3.0 / 480dpi, i.e. a 360x640dp layout surface). Configure your
emulator with a matching profile (e.g. a "Pixel"-class 1080x1920 xxhdpi
skin) so the documented tap coordinates land on the right controls.

## Build

Generate the Gradle wrapper once (this repo does not commit
`gradle/wrapper/gradle-wrapper.jar`):

```bash
cd examples/android
gradle wrapper --gradle-version 8.7
```

Then build with the wrapper (or plain `gradle`, which is already on PATH
here):

```bash
./gradlew assembleDebug
# or, without a wrapper:
gradle assembleDebug
```

The APK is written to `app/build/outputs/apk/debug/app-debug.apk`.

## Run the app

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.argus.demo/.MainActivity
```

The debug build starts an in-process HTTP instrumentation server on port
**8085** (gated on `BuildConfig.DEBUG`, so it never ships in a release
build). Forward the port once per emulator/device boot so the host can
reach it:

```bash
adb forward tcp:8085 tcp:8085
```

### Tap coordinates (1080x1920 device)

| Control | Screen | Coordinates |
| --- | --- | --- |
| `+` (increment counter) | Home | (540, 1000) |
| `Settings` | Home | (540, 1250) |
| Colour swatch | Home | (900, 300) |
| `Dark theme` switch | Settings | (540, 700) |
| `Back` | Settings | (540, 1250) |

The hardware `BACK` key also returns from Settings to Home
(`onBackPressed` is overridden); the counter is preserved across
navigation.

## Run the tests

From the repository root, with the target device selected:

```bash
export ANDROID_SERIAL=emulator-5554   # or your device's serial
.venv/bin/argus run --config examples/android/argus.yaml
```

## What the tests show

`tests/demo.yaml` defines `AND-001`..`AND-009` under the `Demo` feature:

| ID | Shows |
| --- | --- |
| AND-001 | App launches; the "Argus Demo" title is readable via OCR |
| AND-002 | Instrumentation reports `ready: true`, `screen: home` on launch |
| AND-003 | Tapping `+` once increments the counter (UI + `application_state`) |
| AND-004 | Three taps show `Count: 3` and log `Counter: 3` |
| AND-005 | Tapping `Settings` opens the settings screen |
| AND-006 | `BACK` returns to Home with the counter preserved |
| AND-007 | The `Dark theme` toggle turns the swatch purple (`pixel_matches`) |
| AND-008 | `device.reset` clears app data — counter returns to `Count: 0` |
| AND-009 | A documentation screenshot of the home screen |

## Troubleshooting

- **`adb: no devices/emulators found`** — start an emulator or connect a
  device and confirm `adb devices` lists it as `device`, then re-export
  `ANDROID_SERIAL`.
- **Instrumentation checks (`AND-002`) time out** — confirm
  `adb forward tcp:8085 tcp:8085` was run after the device booted (a
  reboot or emulator cold-start requires it again).
- **Tap coordinates miss their targets** — the layout is fixed-dp for a
  1080x1920 / 480dpi screen; a different resolution or density shifts the
  physical pixel positions. Use a matching emulator profile or scale the
  coordinates in `tests/demo.yaml` to your device's actual pixel geometry.
- **`text_present` assertions fail (`AND-001`)** — OCR must be installed
  and configured for Argus; see `docs/ocr.md`.
- **`gradle: command not found` / wrapper missing** — install Gradle (or
  run `gradle wrapper` as shown above once a system Gradle is available)
  before using `./gradlew`.
