# Roku example: Argus Demo

A minimal BrightScript SceneGraph channel implementing the shared "Argus
Demo" behaviour, plus an Argus test suite that drives it over Roku's External
Control Protocol (ECP) and reads the BrightScript debug console.

## Navigation

Roku has no pointer input, so this example picks one key mapping (documented
here, not guessable from the UI):

| Screen   | Key           | Effect                              |
| -------- | ------------- | ------------------------------------ |
| Home     | `ENTER`       | Increment the counter                |
| Home     | `DPAD_RIGHT`  | Open Settings                        |
| Settings | `ENTER`       | Toggle the theme (light/dark)        |
| Settings | `BACK`        | Return to Home                       |

The counter is preserved when navigating between Home and Settings; only
relaunching the channel (`device.reset` in Argus, or reopening it on the
Roku) clears it back to 0.

## Prerequisites

- A Roku device on the same network, in **developer mode**: on the Roku
  remote press Home ×3, Up ×2, Right, Left, Right, Left, Right, then set (and
  note) a developer password.
- `zip` on `PATH` (used by `make zip`; ships with macOS/Linux).
- This repo's Python environment (`.venv`) with Argus installed — see the
  top-level `README.md` / `docs/test-authoring.md`.

## Build

Package the channel as a sideloadable zip:

```bash
cd examples/roku
make zip
```

This produces `examples/roku/build/channel.zip` with `manifest` at the zip
root (`unzip -l examples/roku/build/channel.zip`), as Roku's installer
requires.

## Run the app

Sideloading and launching are handled automatically by the Roku adapter when
`channel_zip` is set in `argus.yaml` — running the tests (below) builds the
connection, sideloads `build/channel.zip`, and launches it. To sideload by
hand instead, visit `http://<roku-ip>/` in a browser, log in with user
`rokudev` and your developer password, and upload `build/channel.zip`.

## Run the tests

From the repository root, with `ROKU_HOST` and `ROKU_DEV_PASSWORD` set to
your device's IP and developer password:

```bash
ROKU_HOST=192.168.1.42 ROKU_DEV_PASSWORD=<devpw> \
  .venv/bin/argus run --config examples/roku/argus.yaml
```

`examples/roku/argus.yaml` points `channel_zip` at
`examples/roku/build/channel.zip`, so run `make zip` first.

## What the tests show

`examples/roku/tests/demo.yaml` (feature `Demo`, IDs `ROKU-001`…`ROKU-008`):

1. **ROKU-001** — the channel launches and prints `App ready` to the debug
   console.
2. **ROKU-002** — the `Argus Demo` title is visible on Home (OCR).
3. **ROKU-003** — `ENTER` increments the counter to `Count: 1`.
4. **ROKU-004** — three `ENTER` presses leave the counter at 3, checked via
   `log_contains "Counter: 3"` instead of OCR.
5. **ROKU-005** — `DPAD_RIGHT` opens Settings (`text_present "Settings"`).
6. **ROKU-006** — `BACK` returns to Home without losing the counter.
7. **ROKU-007** — toggling the theme on Settings turns the swatch purple
   (`pixel_matches` at the swatch centre) and logs `Theme: dark`.
8. **ROKU-008** — `device.reset` relaunches the channel and the counter goes
   back to `Count: 0`.

Screenshots (and therefore the OCR/pixel checks) only work because
`dev_password` is set — without it the adapter reports
`supports_screenshot: false` and those conditions would raise a capability
error; the log-based checks (`ROKU-001`, `ROKU-004`) work either way.

## Troubleshooting

- **`DeviceConnectionError` on connect** — check the Roku is powered on,
  reachable, and `ROKU_HOST` is correct (`GET /query/device-info` on port
  8060 must succeed).
- **401 / "developer password rejected"** — re-check `ROKU_DEV_PASSWORD`;
  the installer user is always `rokudev`.
- **Sideload fails / channel won't launch** — confirm `make zip` was run
  after any source change, and that `unzip -l build/channel.zip` shows
  `manifest` at the zip root, not nested in a subdirectory.
- **OCR/pixel steps fail with a capability error** — `dev_password` is
  missing or empty; screenshots (and therefore `text_present`/
  `pixel_matches`) need it.
- **Debug console assertions time out** — only one client can hold the
  BrightScript console (port 8085) at a time; close any other telnet/IDE
  session connected to the Roku.
