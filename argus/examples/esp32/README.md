# ESP32 example (`ESP-*`)

A PlatformIO/Arduino firmware for a 128x64 SSD1306 OLED that implements the
shared "Argus Demo" app (see `examples/README.md` for the cross-platform
spec) and links `ArgusAgent.h` so Argus can drive it over USB serial or
inside the [Wokwi](https://wokwi.com) simulator. It mirrors the wiring of
`agents/esp32/examples/ssd1306_menu/` (same board, same OLED, same I2C
pins, same `ArgusAgent.h` usage) but runs the Demo app instead of a menu.

All commands below are run **from the repository root**.

**This firmware was written to match `agents/esp32/examples/ssd1306_menu/`
and `agents/esp32/arduino/ArgusAgent.h` by inspection, but it was not
compiled or run here** — this environment has neither `pio` (PlatformIO)
nor `wokwi-cli` installed, and no `WOKWI_CLI_TOKEN`. Build it before relying
on it (see "Build" below), and see "Troubleshooting" for what to check if it
doesn't compile or behave as described.

## Prerequisites

- The repo's `.venv` set up per the top-level `README.md` (for `argus`
  itself; the firmware toolchain below is separate).
- [PlatformIO](https://platformio.org/) (`pio`) to build/flash real hardware:
  `pip install platformio` or the PlatformIO IDE extension.
- `pip install "argus[esp32]"` (adds `pyserial` + `esptool`) to talk to a
  real board over serial.
- To run in Wokwi instead of on hardware: `curl -L
  https://wokwi.com/ci/install.sh | sh` (installs `wokwi-cli`) and a
  `WOKWI_CLI_TOKEN` from the [Wokwi CI dashboard](https://wokwi.com/ci),
  exported in the environment (never in a config file).
- A real board only if you're using `argus.yaml` (serial transport): any
  ESP32 devkit wired to an SSD1306 as in `diagram.json` (SDA -> GPIO21, SCL
  -> GPIO22, 3V3, GND).

## Build

```bash
cd examples/esp32
pio run
```

This produces `.pio/build/esp32dev/firmware.bin` (and `.elf`). Unlike
`agents/esp32/examples/ssd1306_menu/`, this example does **not** commit a
prebuilt `firmware.bin`/`firmware.elf` — there's no toolchain in this
environment to build and verify them, so they're left for you to build.

Flash a real board:

```bash
pio run -t upload --upload-port /dev/cu.usbserial-0001
```

Or let `argus` flash it for you by adding `firmware:
.pio/build/esp32dev/firmware.bin` to the `board` device in `argus.yaml`.

For Wokwi, copy the build output next to `wokwi.toml` (which already points
at these two filenames):

```bash
cp .pio/build/esp32dev/firmware.bin .pio/build/esp32dev/firmware.elf .
```

## Run the app

**Real hardware** — flash it (above), then set `ESP32_PORT` to the board's
serial port:

```bash
export ESP32_PORT=/dev/cu.usbserial-0001   # match your board
```

**Wokwi** (no hardware) — after copying `firmware.bin`/`firmware.elf` next
to `wokwi.toml` as above:

```bash
export WOKWI_CLI_TOKEN=...   # from https://wokwi.com/ci
wokwi-cli examples/esp32     # sanity-check the simulation on its own
```

Either way, watch the serial log for `App ready` (`pio device monitor` for a
real board, or the Wokwi CI output) — that's the same line `ESP-001`
checks for.

## Run the tests

```bash
# Real board:
ESP32_PORT=/dev/cu.usbserial-0001 .venv/bin/argus --dry-run --config examples/esp32/argus.yaml
ESP32_PORT=/dev/cu.usbserial-0001 .venv/bin/argus run --config examples/esp32/argus.yaml

# Wokwi (no hardware), once firmware.bin/firmware.elf are built:
.venv/bin/argus --dry-run --config examples/esp32/argus.wokwi.yaml
.venv/bin/argus run --config examples/esp32/argus.wokwi.yaml
```

Expected: `Executed: 7`, `Passed: 7`, `Failed: 0`.

Two config files, not `requires.devices` on every test: `argus.yaml`
declares one `esp32`-platform device (`board`, serial) and
`argus.wokwi.yaml` declares another (`sim`, wokwi). They're kept separate
because Argus picks the device for a platform-only test by taking the first
configured device name for that platform (see
`RunSession.devices_for_platform` / `TestRunner._device_name_for` in
`src/argus/engine/{session,runner}.py`); with both devices in one config,
every test would silently run against `board` unless each test added
`requires.devices: [sim]`. Two single-device configs let `tests/demo.yaml`
stay identical for both entry points.

## What the tests show

`examples/esp32/tests/demo.yaml` defines `ESP-001`..`ESP-007` under the
`Demo` feature. Each test's `setup` resets the board (`device.reset`, a
firmware reboot) first, so tests are independent of run order — the
counter, screen, and theme are firmware globals reinitialised by `setup()`
on every boot.

Unlike the other examples, this suite has no OCR and no `text_present`: a
128x64 mono display at a 5x7 font is too small for reliable OCR or template
text matching per the design (`docs/superpowers/specs/2026-08-25-examples-
design.md`). Instead:

- `log_contains` for `App ready` and `Counter: N` (ESP-001, ESP-004).
- `instrumentation_value` / `application_state` against the firmware
  agent's serial `status`/`state` JSON (ESP-002, ESP-004, ESP-005, ESP-007)
  — this is the same `/test/status` and `/test/state` contract the other
  examples serve over HTTP, just carried over the serial protocol
  (`instrumentation: {type: device}`; see `docs/esp32.md`).
- `image_present: title.png` for the "ARGUS" title (ESP-003) — a small
  reference crop generated by `tools/render_gfx_text.py` instead of a
  captured screenshot (see "Generating images/title.png" below).
- `pixel_matches` at (112, 55) for the theme swatch (ESP-006) — a filled
  24x10 rect at (100, 50) in light theme, hollow (outline only) in dark
  theme, drawn on every screen so the pixel check works right after
  toggling the theme on the Settings screen.
- No `backend_value` test: this example has no backend connection (the
  design keeps ESP32/Roku/Yocto self-contained).

The `application`/`version`/`ready` fields are set via `argus.setStatus(...)`
in `setup()` to mirror the general `/test/status` contract, but
`capabilities` (an array) is intentionally left out: `ArgusAgent.h`'s
`Entry` table only stores scalar strings/numbers/booleans, so there's no
clean way to emit a JSON array from it without changing the shared header —
harmless here since no test in this suite reads `capabilities`.

### Generating `images/title.png`

`tools/render_gfx_text.py` renders "ARGUS" the way `src/main.cpp` draws it
(Adafruit_GFX's built-in 5x7 font, `setTextSize(2)`, starting at `(0, 0)`)
by embedding that font's glyph columns for just the letters `A`, `R`, `G`,
`U`, `S`:

```bash
.venv/bin/python examples/esp32/tools/render_gfx_text.py
```

**This PNG was generated from a from-memory transcription of the classic
Adafruit_GFX font table, not diffed against `glcdfont.c` or a real capture**
— there is no build toolchain in this environment to pull that header or
run the firmware. It is very likely correct (this is the same 5x7 font used
by every Adafruit_GFX/SSD1306 sketch), but treat `ESP-003` as unverified
until it has run against real hardware or Wokwi.

**On the first real run** (hardware or Wokwi), if `ESP-003` fails or you
just want a verified reference image, recapture it instead of trusting the
rendered one:

```bash
.venv/bin/argus run --config examples/esp32/argus.yaml --save-comparisons
```

This saves the actual screenshot Argus captured for the failing/passing
`image_present` check (`results/<run>/...`); crop or copy that over
`images/title.png`.

## Troubleshooting

- **`pio: command not found`** — install PlatformIO (`pip install
  platformio`); it was not available in the environment this example was
  written in, so the build was never exercised here.
- **`ESP32 device 'board' requires a 'port' for the serial transport`** —
  set `ESP32_PORT` to your board's serial device before running `argus`.
- **`no Argus agent responded within Ns`** — check the firmware actually
  links `ArgusAgent.h` and calls `argus.poll()` in `loop()`, the baud rate
  matches (115200 here), and (if using `firmware:` flashing) the
  `firmware_offset` matches how the binary was built (`0x10000` for a
  PlatformIO app image, the default).
- **`ESP-003` (`image_present: title.png`) fails** — see "Generating
  images/title.png" above; recapture the reference image with
  `--save-comparisons` rather than trying to hand-edit the font table.
- **Wokwi transport can't find the firmware** — `wokwi.toml` points at
  `firmware.bin`/`firmware.elf` in this directory; `pio run` only writes
  them under `.pio/build/esp32dev/`, so copy them here first (see "Build").
- **`esptool`/`pyserial` not found** — `pip install "argus[esp32]"`.
