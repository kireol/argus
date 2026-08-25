# ESP32

The ESP32 adapter tests firmware through a small **Argus agent** you link into
your application. Argus reads the board's serial log, asks the agent for its
display framebuffer (so an SSD1306/ST7789/any RAM-backed screen becomes a
screenshot), injects key presses, reboots the board, and reads simple
status/state values — over USB serial or inside the Wokwi simulator.

## Prerequisites

```bash
pip install "argus[esp32]"          # pyserial + esptool (serial transport)
curl -L https://wokwi.com/ci/install.sh | sh   # wokwi-cli (wokwi transport)
```

Copy `agents/esp32/arduino/ArgusAgent.h` (Arduino/PlatformIO) or
`agents/esp32/micropython/argus_agent.py` (MicroPython) into your project.

## Wiring the agent

Arduino, Adafruit_SSD1306 (page-layout buffer):

```cpp
#include "ArgusAgent.h"
Adafruit_SSD1306 display(128, 64, &Wire, -1);
ArgusAgent argus;

void onKey(const char* key) { /* "BTN_UP", "BTN_OK", ... — treat like a button */ }

void setup() {
  Serial.begin(115200);
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  argus.begin(Serial, display.getBuffer(), 128, 64, ARGUS_MONO_VLSB, "myapp", "1.0");
  argus.onKey(onKey);
  argus.setStatus("screen", "home");
}
void loop() { argus.poll(); /* your code */ }
```

| Library / buffer | Format |
| --- | --- |
| Adafruit_SSD1306 `getBuffer()`, u8g2 `getBufferPtr()` | `ARGUS_MONO_VLSB` |
| Adafruit `GFXcanvas1::getBuffer()` | `ARGUS_MONO_HLSB` |
| Adafruit `GFXcanvas8` | `ARGUS_GS8` |
| Adafruit `GFXcanvas16`, LVGL 16-bit draw buffer | `ARGUS_RGB565` (`ARGUS_RGB565_BE` after `setSwapBytes(true)` in TFT_eSPI sprites) |
| 24-bit buffers | `ARGUS_RGB888` |

MicroPython:

```python
from argus_agent import ArgusAgent
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
argus = ArgusAgent(uart, oled.buffer, 128, 64, "MONO_VLSB", name="myapp")
argus.on_key = handle_key
while True:
    argus.poll()
```

Everything you `print`/`Serial.println` stays a normal log line; the agent only
reacts to lines that start with `ESC[ARGUS]`.

## Configuration

```yaml
devices:
  board:
    type: esp32
    platform: esp32
    transport: serial               # or wokwi
    port: /dev/cu.usbserial-0001    # serial: required
    baud: 115200                    # raise to 921600 for TFT-sized framebuffers
    usb_cdc: false                  # true for C3/S3 boards on native USB
    firmware: build/firmware.bin    # optional: flashed with esptool on connect
    agent: true                     # false = logs only (no agent in the firmware)
    boot_timeout: 10                # seconds to wait for the agent after reset
    timeout: 5                      # per-request seconds
    mono_colors: ["#ffffff", "#000000"]
    instrumentation:
      type: device                  # status/state served by the agent
  sim:
    type: esp32
    platform: esp32
    transport: wokwi
    project_dir: firmware/          # folder with wokwi.toml + diagram.json
```

The wokwi transport needs `WOKWI_CLI_TOKEN` in the environment (from the Wokwi
CI dashboard); never put the token in the config file.

## What the adapter does

| Operation | Implementation |
| --- | --- |
| Connect | open port / start `wokwi-cli --interactive`; flash if `firmware`; reset; wait for the agent's `hello` |
| Screenshot | agent `screenshot` → framebuffer decoded to RGB (mono formats use `mono_colors`) |
| Start / reset app | reset the board (DTR/RTS) or restart the simulator; clears captured logs |
| Stop app | unsupported (a board cannot stop its firmware) |
| Key | agent `input <key>` → your `onKey` callback; key names are yours |
| Logs | every non-protocol serial line |
| Screen size | from `hello` |
| Instrumentation | agent `status` / `state` JSON via `instrumentation: {type: device}` |

Transfer time ≈ bytes ÷ (baud ÷ 10): a 128×64 mono buffer is 1 KB (instant);
a 240×320 RGB565 buffer is 150 KB — ~13 s at 115 200, ~1.6 s at 921 600.

## Example

`agents/esp32/examples/ssd1306_menu/` is a PlatformIO project with a Wokwi
diagram; `tests/integration/test_esp32_adapter_e2e.py` runs it when
`WOKWI_CLI_TOKEN` is set, or against a real board when `ARGUS_ESP32_PORT` is set.

## Limitations

- No DOM/widget inspection; verification is visual (`image_present`, `text_present`,
  `pixel_matches`), log-based (`log_contains`) or via `instrumentation_value`.
- One request at a time; the firmware must call `argus.poll()` regularly (a
  blocked main loop stalls screenshots).
- Store-bought firmware without the agent gives logs only (`agent: false`).
