# Device Adapters

A device adapter teaches the engine how to talk to one kind of target. The
engine only ever sees the `Device` interface; everything platform-specific
stays inside the adapter.

## The Device interface

```python
from argus.adapters import Device, DeviceCapabilities

class Device(ABC):
    # identity
    capabilities: DeviceCapabilities      # discoverable, see below
    platform: str                         # filtering label ("android", "yocto")

    # connection
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def is_available(self) -> bool: ...
    def health_check(self) -> HealthCheckResult: ...

    # application lifecycle (optional)
    def start_application(self) -> None: ...
    def stop_application(self) -> None: ...
    def restart_application(self) -> None: ...
    def reset_application(self) -> None: ...
    def is_application_running(self) -> bool: ...

    # observation (optional)
    def screenshot(self) -> PIL.Image.Image: ...
    def get_screen_info(self) -> ScreenInfo: ...     # width/height/dpi/orientation
    def get_logs(self, lines: int = 200) -> str: ...
    def get_playback_state(self) -> PlaybackState: ...  # media state/title/position

    # input (optional)
    def tap(self, x: int, y: int) -> None: ...
    def swipe(self, x1, y1, x2, y2, duration_ms=300) -> None: ...
    def long_press(self, x, y, duration_ms=1000) -> None: ...
    def drag(self, x1, y1, x2, y2, hold_ms=500, duration_ms=500) -> None: ...
    def multi_touch(self, fingers, duration_ms=500) -> None: ...  # [[(x, y), ...], ...]
    def pinch(self, cx, cy, start_distance, end_distance, duration_ms=500) -> None: ...
    def press_key(self, key: str) -> None: ...
```

Not every device supports every operation. Capabilities are discoverable:

```python
if device.capabilities.supports_tap:
    device.tap(100, 200)
```

Unsupported operations raise `DeviceCapabilityError` with a clear message —
never a silent no-op. Capability flags: `supports_tap`, `supports_swipe`,
`supports_long_press`, `supports_drag`, `supports_multi_touch`, and so on.

`pinch` has a default implementation in the base class built on
`multi_touch` (two fingers moving along the horizontal axis through the
centre), so an adapter only needs to implement `multi_touch` to get pinch
and zoom. `argus.adapters.base.interpolate_path` helps adapters step a
finger along a polyline.

## Built-in adapters

| Type | Transport | Notes |
| --- | --- | --- |
| `android` | ADB subprocess | see [android.md](android.md) |
| `yocto` | SSH (paramiko) | pluggable screenshots, see [yocto.md](yocto.md) |
| `browser` | Playwright | web apps in chromium/firefox/webkit, see [browser.md](browser.md) |
| `desktop` | pyautogui + subprocess | native apps on Windows / Linux / macOS (mouse, keyboard, process logs), see [desktop.md](desktop.md) |
| `roku` | ECP + dev installer | developer-mode Roku with a sideloaded channel, see [roku.md](roku.md) |
| `tvos_sim` | `xcrun simctl` + `osascript` | tvOS app in the Xcode Simulator, see [tvos.md](tvos.md) |
| `appletv` | pyatv | physical Apple TV (remote + playback state, no screenshots), see [tvos.md](tvos.md) |
| `esp32` | serial agent / Wokwi | ESP32 firmware: logs, framebuffer screenshots, keys, see [esp32.md](esp32.md) |
| `ios` | WebDriverAgent HTTP | iOS app on a simulator or device, full gesture set, see [ios.md](ios.md) |
| `fake` | in-memory | hardware-free development & framework self-tests |

## The fake ecosystem

`type: fake` devices serve queued screenshots, a screenshot directory, or —
most usefully — **render the fake backend's state** so the full loop
(backend.set → screen change → OpenCV verification) runs with zero
hardware. `config/fake.yaml` is a complete working example. Fakes record
inputs (`taps`, `swipes`, `long_presses`, `drags`, `multi_touches`, `keys`)
for assertions in framework tests.

## Screenshot providers

Devices whose display stack varies (Yocto) delegate capture to a
`ScreenshotProvider`:

```python
class ScreenshotProvider(ABC):
    def capture(self) -> PIL.Image.Image: ...
```

The built-in `CommandScreenshotProvider` runs a configured command remotely
and fetches the resulting file — Weston, grim, X11, framebuffer, or a custom
HTTP service are all just configuration.

## Writing a new adapter

See [plugin-development.md](plugin-development.md) for the full walkthrough.
In short:

```python
from argus.adapters import Device, DeviceCapabilities

class RokuAdapter(Device):
    @classmethod
    def from_config(cls, name, config):     # receives DeviceConfig.options
        return cls(name, host=config.options["host"])
    ...
```

registered via the `argus.devices` entry point or `DeviceRegistry.register`,
then configured with `type: roku`. The engine needs no changes.

## Lifetimes and pooling

`RunSession` connects each device once and keeps the connection for the
whole run (spec §34). `connect()` should therefore be idempotent-friendly
and `health_check()` cheap — preflight and validation call it.
