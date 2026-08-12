# Plugin Development

New capabilities — actions, conditions, devices, OCR providers, screenshot
providers, reporters — are added **without modifying the engine**. Register
them programmatically or through Python entry points.

## Entry points

Declare in your plugin package's `pyproject.toml`:

```toml
[project.entry-points."argus.actions"]
myplugin = "myplugin.actions:register"

[project.entry-points."argus.devices"]
myplugin = "myplugin.devices:register"
```

Each target is a function receiving the registry; the framework discovers
installed plugins automatically.

## A new action

```python
# myplugin/actions.py
from typing import Any
from argus.actions import Action, ActionRegistry, ActionResult

class ClearCacheAction(Action):
    name = "device.clear_cache"

    def execute(self, context, params: dict[str, Any]) -> ActionResult:
        device = context.require_device()
        # ... adapter-specific work ...
        return ActionResult.ok(f"cache cleared on {device.name}")

def register(registry: ActionRegistry) -> None:
    registry.register(ClearCacheAction())
```

Used from YAML immediately:

```yaml
- action: device.clear_cache
```

Rules of thumb: get dependencies from the context
(`require_device/require_backend/require_instrumentation`), return
`ActionResult.failed(msg, category=...)` for expected failures (categories:
`assertion`, `timeout`, `device_connection`, `backend`, `screenshot`), and
raise `ActionError` only for authoring mistakes (missing parameters —
`self.require_param(params, "x")` does this for you).

## A new condition

```python
# myplugin/conditions.py
from argus.conditions import Condition, ConditionFactory
from argus.models.results import VerificationResult

class BatteryAboveCondition(Condition):
    name = "battery_above"
    needs_observation = False          # True = engine supplies a screenshot

    def __init__(self, params):
        self.minimum = int(params["percent"])

    def evaluate(self, context, observation) -> VerificationResult:
        level = 87  # read from device/instrumentation
        return VerificationResult(
            passed=level >= self.minimum,
            verifier=self.name,
            message=f"battery {level}% (need ≥ {self.minimum}%)",
        )

def register(factory: ConditionFactory) -> None:
    factory.register("battery_above", lambda params, ctx: BatteryAboveCondition(params))
```

```yaml
- action: wait_until
  condition:
    type: battery_above
    percent: 50
```

Conditions that inspect the screen set `needs_observation = True` and use
the observation they're handed — never capture their own screenshot, so
composites stay one-capture-per-poll.

## A new device adapter

```python
# myplugin/devices.py
from argus.adapters import Device, DeviceCapabilities
from argus.adapters.registry import DeviceRegistry

class RokuAdapter(Device):
    @classmethod
    def from_config(cls, name: str, config) -> "RokuAdapter":
        options = config.options          # free-form dict from YAML
        return cls(name, host=options["host"])

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(supports_screenshot=True, supports_keyboard=True)

    # implement connect/disconnect/is_available/health_check + what you support

def register(registry: DeviceRegistry) -> None:
    registry.register("roku", RokuAdapter.from_config)
```

```yaml
devices:
  living_room_roku:
    type: roku
    host: ${ROKU_HOST}
```

Guidelines: validate options in `from_config` and raise
`ConfigurationError` with remediation text; give every network call a
timeout; raise `DeviceConnectionError`/`ScreenshotError` (never bare
`Exception`); keep `platform` meaningful for filtering.

## A new OCR provider

Implement `argus.ocr.OCRProvider` (`extract_text`, `is_available`) and extend
`create_ocr_provider`, or ship it in your plugin and select it with
`ocr.provider: myocr`.

## A new screenshot provider (Yocto)

Implement `argus.adapters.ScreenshotProvider.capture()` and wire it in a
custom device adapter, or — usually simpler — express the capture as a
command for the built-in `CommandScreenshotProvider`.

## A new reporter / GUI integration

Subscribe to the event bus; no registration needed:

```python
from argus.events import EventBus, TestFailed

events = EventBus()
events.subscribe(lambda e: notify_slack(e.result), TestFailed)
runner = TestRunner(config, events)
```

The full event list is in [architecture.md](architecture.md).

## Testing plugins

Use the fakes (`FakeDevice`, `FakeBackend`, `FakeInstrumentation`,
`FakeOCRProvider`) exactly as the framework's own tests do — see
`tests/unit/test_actions.py` for the pattern.
