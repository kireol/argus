# Application Instrumentation

Instrumentation is a small HTTP protocol an application can implement so the
framework can read its **internal** state: current screen, readiness,
backend connectivity, rendering status.

Two rules govern its role:

1. **Diagnostic and complementary, never authoritative.** A visual test
   passes only on externally observed pixels. If instrumentation says
   `image_loaded: true` but the artwork is not on screen, the test fails —
   and the instrumentation snapshot in the failure artifacts tells you the
   bug is between "loaded" and "rendered".
2. **Everything is optional and discoverable.** Apps implement the fields
   and endpoints they can; capabilities are advertised, and the framework
   only requires what a test actually uses.

## Protocol

The transport is HTTP; endpoints are configurable per device (defaults
shown):

| Endpoint | Returns |
| --- | --- |
| `GET /test/status` | the standard status document (below) |
| `GET /test/health` | 200 when the app is alive |
| `GET /test/state` | free-form application state (JSON object) |
| `GET /test/screen` | *(optional)* current screen as PNG — usable as a Yocto screenshot provider |
| `GET /test/rendering` | *(optional)* rendering diagnostics |

### Status document

```json
{
  "application": "MyApp",
  "version": "2.14.3",
  "build": "2026-08-12.1042",
  "ready": true,
  "screen": "movie_details",
  "backend_connected": true,
  "rendering": true,
  "image_loaded": true,
  "capabilities": ["status", "screen", "state", "rendering"]
}
```

Every field is optional; unknown extra fields are preserved and reachable
from tests.

## Configuration

```yaml
devices:
  android:
    type: android
    instrumentation:
      base_url: http://127.0.0.1:8085
      timeout: 5s
      # status_endpoint / health_endpoint / state_endpoint overridable
```

## Using it in tests

```yaml
# against /test/status
- action: verify
  condition:
    type: instrumentation_value
    key: ready
    equals: true

# against /test/state, dotted paths supported
- action: wait_until
  condition:
    type: application_state
    key: player.state
    equals: playing
  timeout: 5s
```

`instrumentation_value` also accepts `contains:` for substring checks.

A powerful pattern is combining internal and external checks so failures
localize themselves:

```yaml
- action: wait_until
  condition:
    all:
      - type: instrumentation_value    # app thinks it's ready...
        key: image_loaded
        equals: true
      - type: image_present            # ...and the screen proves it
        image: movie_123.png
```

## Implementing it in your application

Any embedded HTTP listener works — a dozen lines with your framework of
choice. Guidelines:

- Bind to localhost (Android) or the device LAN (Yocto lab networks).
  Ship it only in test/debug builds.
- Keep handlers non-blocking and side-effect-free; the framework may poll
  several times per second during `wait_until`.
- Report honestly: `ready` should mean "fully initialized and interactive",
  not "process started".

## Fake instrumentation

For demos and framework tests, `type: fake` serves a configurable status
in-process — see `config/fake.yaml`.
