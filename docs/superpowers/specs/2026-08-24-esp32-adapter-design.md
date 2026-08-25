# ESP32 Device Adapter — Design

**Date:** 2026-08-24
**Status:** approved in conversation; awaiting spec review

## Goal

Let Argus tests drive and verify firmware running on an ESP32: read its serial
logs, capture what it draws on an OLED/TFT (or any RAM framebuffer), inject
key/button input, reboot it, and read simple status/state values — on a real
board over USB serial and, with the same firmware, in the Wokwi simulator.

## The central constraint

Panels such as the SSD1306 are write-only over I²C/SPI; the host cannot read
pixels back. The framebuffer therefore comes from the firmware: a small
**Argus agent** linked into the application answers a `screenshot` request by
dumping its display buffer over the same UART it logs on. Argus ships the agent
source for Arduino/PlatformIO (C++) and MicroPython. Firmware without the agent
is still usable as a logs-only device.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Adapter shape | **One `esp32` type with `transport: serial \| wokwi`** | Capabilities are defined by the agent, not the transport; the same firmware behaves identically on a board and in Wokwi (`wokwi-cli --interactive` pipes the simulated UART to stdin/stdout). |
| Screenshots | **Agent protocol over UART, raw binary** | Only the firmware has the pixels. Raw bytes keep TFT dumps fast; `baud` is configurable and documented. `wokwi-cli`'s native `--screenshot-time` is batch-only and unusable mid-test. |
| Framebuffer vocabulary | **MicroPython `framebuf` names** (`MONO_HLSB`, `MONO_HMSB`, `MONO_VLSB`, `GS8`, `RGB565`, `RGB565_BE`, `RGB888`) | Both agents and the decoder share one canonical set; covers Adafruit GFXcanvas1, u8g2/SSD1306 page buffers, TFT_eSPI sprites, LVGL 16-bit buffers. |
| Reset | **Serial: DTR/RTS toggle (esptool's classic sequence); Wokwi: restart the process** | No firmware support needed; `stop_application` is unsupported (a bare board cannot "stop" its app) — raises `DeviceCapabilityError`. |
| Instrumentation | **`InstrumentationConfig.type: device`** → `Device.instrumentation_client()` | Lets `instrumentation_value` / `application_state` conditions read agent `status`/`state` over serial with a two-line engine hook instead of a second HTTP server on the board. |
| Firmware flashing | **Optional `firmware:` option, `esptool` CLI, serial transport only** | CI convenience; `esptool` is an optional dependency, invoked as a subprocess so its absence is a clear `DeviceConnectionError`. |
| Package layout | **`src/argus/adapters/esp32/` package** (`transport.py`, `protocol.py`, `framebuffer.py`, `instrumentation.py`, `adapter.py`) | Four independently testable concerns; a single file would exceed 700 lines. |

## 1. Agent protocol (UART, shared by both transports)

The UART carries application logs and Argus traffic at once. Framing:

| Direction | Format |
| --- | --- |
| Request (host → board) | one line: `\x1b[ARGUS] <cmd>[ <args>]\n` |
| Success response | `\x1b[ARGUS] <cmd> ok <len>\n` followed by exactly `<len>` raw bytes, then `\n` |
| Error response | `\x1b[ARGUS] <cmd> err <message>\n` |
| Everything else | application log output, one line per `\n` |

The `\x1b[ARGUS]` prefix (ESC + `[ARGUS]`) cannot be produced by ordinary
`printf` text and is ignored by terminals as a malformed CSI sequence, so
logging tools show it harmlessly.

Commands:

| Command | Response payload |
| --- | --- |
| `hello` | `name=<app> version=<v> fb=<FORMAT>,<w>,<h> caps=<comma list>`; `fb=none` when no framebuffer is registered. Caps subset of `screen,input,status,state`. |
| `screenshot` | the framebuffer bytes (length = `width*height*bpp/8`, rounded per format rules below) |
| `input <key>` | empty payload; firmware's key callback is invoked with `<key>` |
| `status` | JSON object (string/number/bool values) |
| `state` | JSON object |

Host-side rules: one request in flight at a time; a response header must
arrive within `timeout` seconds (default 5) or the request fails with
`DeviceConnectionError`; `err` responses raise `DeviceConnectionError` with the
message; log lines interleaved before the header are delivered to the log
buffer, never lost.

Framebuffer byte layouts (`framebuffer.py`, `decode(data, fmt, width, height,
*, mono_colors) -> PIL.Image` in RGB):

| Format | Bytes | Layout |
| --- | --- | --- |
| `MONO_HLSB` | `ceil(w/8)*h` | row-major, 1 bpp, bit 7 = leftmost pixel |
| `MONO_HMSB` | `ceil(w/8)*h` | row-major, 1 bpp, bit 0 = leftmost pixel |
| `MONO_VLSB` | `w*ceil(h/8)` | SSD1306/u8g2 page layout: byte per column per 8-row page, bit 0 = top row of the page |
| `GS8` | `w*h` | 8-bit grayscale |
| `RGB565` | `w*h*2` | little-endian 16-bit |
| `RGB565_BE` | `w*h*2` | big-endian 16-bit (TFT_eSPI `setSwapBytes(true)` sprites) |
| `RGB888` | `w*h*3` | 24-bit |

Length mismatch → `ScreenshotError` naming expected vs actual bytes. Mono
formats render set bits as `mono_colors[0]` and clear bits as `mono_colors[1]`
(default white on black).

### Shipped agents (`agents/esp32/`)

- `arduino/ArgusAgent.h` — header-only C++.
  `ArgusAgent argus; argus.begin(Serial, buffer, w, h, ARGUS_MONO_VLSB);`
  `argus.onKey(void (*fn)(const char*)); argus.setStatus("screen", "menu");`
  `argus.setState("count", 3); argus.poll();` (call from `loop()`). Non-blocking
  line reader; up to 16 status and 16 state entries (string values, numbers
  formatted as strings, bools as `true`/`false`). Application serial input that
  is not an Argus command is passed to an optional `onSerialLine` callback.
- `micropython/argus_agent.py` — `ArgusAgent(uart, fb, width, height, fmt)`
  with `.poll()`, `.on_key`, `.set_status()`, `.set_state()`; `fb` may be a
  `framebuf.FrameBuffer` (the underlying `bytearray` is passed in as well since
  `FrameBuffer` does not expose its buffer).
- `examples/ssd1306_menu/` — Arduino sketch (128×64 SSD1306, two-item menu,
  `BTN_UP`/`BTN_DOWN`/`BTN_OK` keys, logs `menu: selected=<item>`), a
  `wokwi.toml` + `diagram.json`, and a committed prebuilt `firmware.bin`/`.elf`
  so the Wokwi integration test needs no toolchain. Building it requires
  PlatformIO or `arduino-cli`; if neither is available when implementing, the
  example is committed as source plus a `BUILD.md`, and the Wokwi integration
  test additionally skips when `firmware.bin` is absent.

## 2. Transports (`transport.py`)

```python
class Transport(Protocol):
    def read(self, size: int, timeout: float) -> bytes: ...   # up to size bytes, may return fewer
    def write(self, data: bytes) -> None: ...
    def reset(self) -> None: ...
    def close(self) -> None: ...
    @property
    def description(self) -> str: ...
```

- `SerialTransport(port, baud=115200)` — pyserial (`argus[esp32]` extra);
  `reset()` = `dtr=False; rts=True; sleep(0.1); rts=False` (esptool's classic
  auto-reset; the ESP32-C3/S3 native USB-CDC needs `rts=True; dtr=True` first —
  both sequences are tried when `usb_cdc: true`). Missing pyserial →
  `DeviceConnectionError(remediation='pip install "argus[esp32]"')`; port open
  failure → `DeviceConnectionError` naming the port and suggesting `ls /dev/tty*`.
- `WokwiTransport(project_dir, timeout_ms=600000)` — spawns
  `wokwi-cli --interactive --timeout <ms> <project_dir>` with pipes;
  `read` uses a reader thread + queue so timeouts work on pipes; `reset()` =
  terminate + respawn; requires `WOKWI_CLI_TOKEN` in the environment (checked
  up front → `DeviceConnectionError` with remediation) and `wokwi-cli` on PATH.
  `wokwi-cli`'s own banner lines are treated as ordinary log lines.
- Tests use an in-memory `FakeTransport` (scripted byte stream + captured
  writes) defined in the test module.

## 3. Protocol link (`protocol.py`)

`AgentLink(transport, *, log_sink: deque[str], timeout=5.0)`:

- Reader thread pulls bytes, splits on `\n`; lines starting with the prefix are
  parsed as response headers — for `ok <len>` the thread then reads exactly
  `<len>` bytes + the trailing `\n` as the payload; all other lines are
  appended to `log_sink` (decoded UTF-8 with replacement, `\r` stripped).
- `request(cmd, *args) -> bytes` writes the request line and waits on a
  `queue.Queue` for the matching response; a mismatched command name in the
  header is treated as a stale response and skipped; timeout →
  `DeviceConnectionError`; `err` → `DeviceConnectionError(message)`.
- `hello() -> AgentInfo(name, version, fb_format: str | None, width, height, caps: frozenset[str])`.
- `close()` stops the thread (transport close unblocks the read).

## 4. Adapter (`adapter.py`, `Esp32Adapter(Device)`)

Config (`DeviceConfig.options`):

| Option | Default | Meaning |
| --- | --- | --- |
| `transport` | required | `serial` or `wokwi` |
| `port` | required for serial | e.g. `/dev/cu.usbserial-0001` |
| `baud` | `115200` | serial only |
| `usb_cdc` | `false` | serial only; try the native-USB reset sequence too |
| `project_dir` | required for wokwi | directory with `wokwi.toml` |
| `firmware` | none | serial only; `.bin` flashed with `esptool --port <port> --baud 460800 write_flash 0x0 <bin>` on connect (offset configurable via `firmware_offset`, default `0x0`) |
| `agent` | `true` | `false` = logs-only device (no `hello`, screenshot/keys/instrumentation unsupported) |
| `boot_timeout` | `10` | seconds to wait for `hello` after reset |
| `timeout` | `5` | per-request seconds |
| `mono_colors` | `["#ffffff", "#000000"]` | 1-bpp rendering colours |

Operations:

| Method | Implementation |
| --- | --- |
| `connect` | build transport; flash if `firmware`; `reset()`; if `agent`, poll `hello` until `boot_timeout` (retry every 0.5 s, log lines flow meanwhile) else start the link's reader only. Failure closes the transport and raises `DeviceConnectionError` (remediation mentions the agent + baud). |
| `disconnect` | close link + transport; idempotent. |
| `is_available` | serial: pyserial importable; wokwi: `wokwi-cli` on PATH. |
| `health_check` | transport open (+ last `hello` succeeded) → ok with `agent`, `fb`, `caps` details. |
| `start_application`, `reset_application` | `reset()` then re-`hello` (when `agent`); clears the log buffer. |
| `stop_application` | `DeviceCapabilityError`. |
| `is_application_running` | `True` after a successful `hello` since the last reset (`agent`), or transport open (`agent: false`). |
| `screenshot` | `request("screenshot")` → `decode(...)`; `DeviceCapabilityError` if `screen` not in caps. |
| `get_screen_info` | from `hello`. |
| `get_logs(lines)` | last N lines of the deque (5000), oldest first. |
| `press_key(key)` | `request("input", key)`; `DeviceCapabilityError` if `input` not in caps. Key names are passed through unchanged (firmware defines them). |
| `tap`, `swipe` | unsupported. |
| `instrumentation_client()` | `SerialInstrumentationClient(link)` when `status` or `state` in caps, else `None`. |

Capabilities: `supports_logs=True`; `supports_screenshot = "screen" in caps`;
`supports_keyboard = "input" in caps`; `supports_app_lifecycle=True`;
`supports_instrumentation = bool(caps & {"status","state"})`. Before `connect`
(no `hello` yet) the adapter reports the optimistic set for `agent: true` and
logs-only for `agent: false`. Platform label `esp32`.

## 5. Instrumentation hook (engine change)

- `Device.instrumentation_client(self) -> InstrumentationClient | None` in
  `base.py`, default `None`.
- `InstrumentationConfig.type` accepts `"device"` (docstring updated).
- `RunSession.instrumentation()` gains: `elif instr.type == "device": client =
  self.device(device_name).instrumentation_client()`; a `None` result raises
  `ConfigurationError` ("device does not provide instrumentation").
- `SerialInstrumentationClient(link)` (`instrumentation.py`): `status()` →
  `InstrumentationStatus.model_validate(json)`, `state()` → dict,
  `capabilities()` → the agent's caps list, `health_check()` → `hello` ok.
- `FakeDevice` is unchanged (its instrumentation stays `type: fake`).

## 6. Error handling summary

| Situation | Error |
| --- | --- |
| pyserial / `wokwi-cli` / `esptool` missing | `DeviceConnectionError` with install remediation |
| port cannot be opened, `WOKWI_CLI_TOKEN` unset | `DeviceConnectionError` |
| agent silent after `boot_timeout` | `DeviceConnectionError` ("no Argus agent responded; check `agent: false` for logs-only, baud, and that `argus.poll()` runs") |
| request timeout / `err` response | `DeviceConnectionError` |
| framebuffer length mismatch / unknown format | `ScreenshotError` |
| screenshot/keys/instrumentation on a firmware lacking the cap, `stop_application`, `tap`, `swipe` | `DeviceCapabilityError` |

## 7. Testing strategy

- `tests/unit/test_esp32_framebuffer.py` — every format from hand-built byte
  patterns (e.g. a 16×8 checkerboard) asserting exact pixels; length-mismatch
  and unknown-format errors.
- `tests/unit/test_esp32_protocol.py` — `FakeTransport` streams: log lines
  interleaved with a response; payload split across reads; `err`; timeout;
  stale header; `hello` parsing incl. `fb=none`.
- `tests/unit/test_esp32_adapter.py` — fake transport factory injected;
  connect/hello/capabilities (with and without `screen`/`input`), screenshot
  decode end-to-end, `press_key`, reset clears logs, logs-only mode,
  `firmware` produces the expected `esptool` argv (runner injected), missing
  pyserial / token / CLI remediations, `stop_application` unsupported.
- `tests/unit/test_esp32_instrumentation.py` + a `RunSession` test for
  `type: device` (using a fake device that returns a `FakeInstrumentation`).
- Integration `tests/integration/test_esp32_adapter_e2e.py`: Wokwi run of the
  example sketch when `WOKWI_CLI_TOKEN` is set and `wokwi-cli` is on PATH
  (asserts the menu screenshot's known pixels, `menu: selected=` log line, and a
  `BTN_DOWN` key changes the selection); real-board run when `ARGUS_ESP32_PORT`
  is set. Both skip cleanly otherwise.
- Gate: no new failures versus the branch baseline; ruff/mypy clean on touched
  files; suite passes with pyserial absent.

## 8. Documentation and packaging

- `docs/esp32.md`: wiring the Arduino/MicroPython agent (snippets for
  Adafruit_SSD1306, u8g2, TFT_eSPI, MicroPython `ssd1306`), format table, baud
  guidance (bytes ÷ baud/10 ≈ seconds), reset/USB-CDC notes, Wokwi setup with
  `${WOKWI_CLI_TOKEN}`, logs-only mode, `type: device` instrumentation example,
  config example with `platform: esp32`.
- Rows/links in `docs/adapters.md`, `docs/getting-started.md`,
  `docs/configuration.md`, README; `docs/instrumentation.md` documents
  `type: device`; CHANGELOG.
- `pyproject.toml`: `esp32 = ["pyserial>=3.5", "esptool>=4.7"]`; `all`
  updated; keywords `esp32`, `embedded-display`, `wokwi`; mypy override for
  `serial.*`, `esptool.*`. Agent sources under `agents/esp32/` are not part of
  the wheel (documented as copy-in files).

## Implementation order

1. Framebuffer decoder (pure, fully testable).
2. Transports + protocol link (fake-transport tests).
3. Adapter + registry + pyproject extra.
4. Instrumentation client + engine hook + session test.
5. Agents (Arduino header, MicroPython module, example sketch + Wokwi files + prebuilt binary).
6. Integration test + docs.
