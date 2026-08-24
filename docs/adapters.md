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

    # input (optional)
    def tap(self, x: int, y: int) -> None: ...
    def swipe(self, x1, y1, x2, y2, duration_ms=300) -> None: ...
    def press_key(self, key: str) -> None: ...
```

Not every device supports every operation. Capabilities are discoverable:

```python
if device.capabilities.supports_tap:
    device.tap(100, 200)
```

Unsupported operations raise `DeviceCapabilityError` with a clear message —
never a silent no-op.

## Built-in adapters

| Type | Transport | Notes |
| --- | --- | --- |
| `android` | ADB subprocess | see [android.md](android.md) |
| `yocto` | SSH (paramiko) | pluggable screenshots, see [yocto.md](yocto.md) |
| `browser` | Playwright | web apps in chromium/firefox/webkit, see [browser.md](browser.md) |
| `fake` | in-memory | hardware-free development & framework self-tests |

## The fake ecosystem

`type: fake` devices serve queued screenshots, a screenshot directory, or —
most usefully — **render the fake backend's state** so the full loop
(backend.set → screen change → OpenCV verification) runs with zero
hardware. `config/fake.yaml` is a complete working example. Fakes record
inputs (`taps`, `swipes`, `keys`) for assertions in framework tests.

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
