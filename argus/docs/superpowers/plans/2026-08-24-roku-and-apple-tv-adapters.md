# Roku and Apple TV Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Argus tests drive and verify TV apps on a dev-mode Roku, the tvOS Simulator, and a physical Apple TV, plus a `now_playing` condition for playback-state assertions.

**Architecture:** Three independent `Device` plug-ins (`roku`, `tvos_sim`, `appletv`) registered in `register_builtin_devices`, each with an injectable transport so unit tests run against fakes. A `PlaybackState` model and `Device.get_playback_state()` hook (with `supports_playback_state` capability) feed a new `now_playing` condition registered in `ConditionFactory`. No engine changes.

**Tech Stack:** Python 3.12, pydantic 2, Pillow, pytest + pytest-httpserver (dev dep), stdlib `urllib`/`socket`/`subprocess`/`asyncio`, optional `pyatv>=0.14` (`argus[appletv]`), macOS `xcrun simctl` + `osascript` (tvOS Simulator).

**Spec:** `docs/superpowers/specs/2026-08-24-roku-and-apple-tv-adapters-design.md` — read it first; this plan implements it section by section.

## Global Constraints

- `requires-python = ">=3.12"`; code must pass `ruff check src tests` (line length 100, rules E F W I UP B SIM) and `mypy src` **on every touched file**.
- Optional tooling is optional: importing `argus` and the full non-integration suite must pass with **none** of `pyatv`, Xcode, or a Roku present.
- Unsupported operations raise `DeviceCapabilityError` via `Device._unsupported(...)` (never silent no-ops); missing tooling raises `DeviceConnectionError` with a `remediation=` string.
- New device types register by name in `argus.adapters.registry.register_builtin_devices` (imports alphabetical: android, appletv, browser, fake, roku, tvos_sim, yocto).
- Log buffers: `collections.deque(maxlen=5000)`, oldest first; `get_logs(lines)` returns the last `lines` joined with `"\n"` (`""` for `lines <= 0`).
- Key names: strip a `KEYCODE_` prefix, upper-case, look up in the adapter's map.
- Platform labels: `roku`, `tvos_sim`, `appletv`. Every YAML example in docs sets `platform:` explicitly (the runner filters on `DeviceConfig.effective_platform`, i.e. config `platform` or `type`).
- Commit messages: short imperative sentence, no prefix.
- Baseline before this plan already has 12 failing tests (`tests/unit/test_text_verifiers.py`, `test_console_reporter.py`, `test_ocr_tesseract.py`), 3 ruff errors (`src/argus/engine/runner.py`, `src/argus/reporting/html.py`, `tests/unit/test_console_reporter.py`) and 2 mypy errors (`src/argus/ocr/preprocess.py`, `src/argus/verifiers/image.py`). These are **out of scope**; the gate for every task is *no new failures* and *ruff/mypy clean on touched files*.
- Run commands from the repo root with the project venv: `.venv/bin/python -m pytest ...`, `.venv/bin/ruff check src tests`, `.venv/bin/mypy src`.
- `git add` only the files each task lists (never `argus.rtf`, `.superpowers/`, or unrelated `docs/superpowers/` files).

---

## File map

| Path | Responsibility |
| --- | --- |
| `src/argus/models/common.py` | add `PlaybackState` |
| `src/argus/adapters/base.py` | `supports_playback_state` capability + `Device.get_playback_state()` hook |
| `src/argus/adapters/fake.py` | `FakeDevice.playback_state` |
| `src/argus/conditions/builtin.py` | `_NowPlayingCondition`, register `now_playing` |
| `tests/unit/test_playback_state.py` (new), `tests/unit/test_conditions.py` | Task 1 tests |
| `src/argus/adapters/roku.py` (new) | `RokuAdapter` (ECP, dev installer, debug-console log reader) |
| `tests/unit/test_roku_adapter.py`, `tests/integration/test_roku_adapter_e2e.py` (new) | Task 2 tests |
| `src/argus/adapters/tvos_sim.py` (new) | `TvosSimAdapter` (`xcrun simctl`, `osascript`) |
| `tests/unit/test_tvos_sim_adapter.py`, `tests/integration/test_tvos_sim_adapter_e2e.py` (new) | Task 3 tests |
| `src/argus/adapters/appletv.py` (new) | `AppleTvAdapter` (pyatv on a loop thread) |
| `tests/unit/test_appletv_adapter.py`, `tests/integration/test_appletv_adapter_e2e.py` (new) | Task 4 tests |
| `src/argus/adapters/registry.py` | register `roku`, `tvos_sim`, `appletv` (Tasks 2–4) |
| `pyproject.toml` | `appletv` extra, `all`, keywords, mypy override (Task 4) |
| `docs/roku.md`, `docs/tvos.md` (new), `docs/adapters.md`, `docs/test-authoring.md`, `docs/getting-started.md`, `docs/configuration.md`, `README.md`, `CHANGELOG.md` | documentation (Tasks 1 and 5) |

---

### Task 1: `PlaybackState`, device hook, and `now_playing` condition

**Files:**
- Modify: `src/argus/models/common.py` (append class; add `Literal` import)
- Modify: `src/argus/adapters/base.py` (capability field, hook method, import)
- Modify: `src/argus/adapters/fake.py` (`FakeDevice`: attribute, capability, method)
- Modify: `src/argus/conditions/builtin.py` (class before `def register`, one `factory.register` line, `import time`)
- Modify: `docs/test-authoring.md` (row after `log_contains`, example after the `log_contains` example), `CHANGELOG.md`
- Test: `tests/unit/test_playback_state.py` (new), `tests/unit/test_conditions.py`

**Interfaces:**
- Consumes: `Condition`, `ConditionFactory`, `VerificationResult`, `TestContext.require_device()` (all existing).
- Produces:
  - `argus.models.common.PlaybackState(state: Literal["playing","paused","stopped","idle","loading","seeking"], title: str | None, app_id: str | None, position: float | None, duration: float | None)`.
  - `DeviceCapabilities.supports_playback_state: bool = False`.
  - `Device.get_playback_state(self) -> PlaybackState` (default raises `DeviceCapabilityError`).
  - `FakeDevice.playback_state: PlaybackState | None` (returned by `get_playback_state`; `PlaybackState(state="idle")` when `None`).
  - Condition `now_playing` with params `state`, `title` (case-insensitive substring), `app_id`, `position_advancing: bool`, `interval: float = 1.0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_playback_state.py`:

```python
"""PlaybackState model and the Device.get_playback_state hook."""

from __future__ import annotations

import pytest

from argus.adapters.base import Device, DeviceCapabilities
from argus.adapters.fake import FakeDevice
from argus.exceptions import DeviceCapabilityError
from argus.models.common import HealthCheckResult, PlaybackState


class _MinimalDevice(Device):
    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities()

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def is_available(self) -> bool:
        return True

    def health_check(self) -> HealthCheckResult:
        return HealthCheckResult.ok()


def test_playback_state_defaults():
    state = PlaybackState(state="playing")
    assert state.title is None and state.position is None
    assert state.model_dump()["state"] == "playing"


def test_playback_state_rejects_unknown_state():
    with pytest.raises(ValueError):
        PlaybackState(state="dancing")


def test_default_hook_is_unsupported():
    device = _MinimalDevice("min")
    assert device.capabilities.supports_playback_state is False
    with pytest.raises(DeviceCapabilityError, match="get_playback_state"):
        device.get_playback_state()


def test_fake_device_reports_playback_state():
    device = FakeDevice()
    assert device.capabilities.supports_playback_state
    assert device.get_playback_state() == PlaybackState(state="idle")
    device.playback_state = PlaybackState(state="playing", title="Trailer", position=3.5)
    assert device.get_playback_state().title == "Trailer"
```

Append to `tests/unit/test_conditions.py` inside `class TestLeafConditions` (after the `log_contains` tests; `context.device` is a `FakeDevice`):

```python
    def test_now_playing_matches_all_fields(self, context):
        from argus.models.common import PlaybackState

        context.device.playback_state = PlaybackState(
            state="playing", title="Big Buck Bunny", app_id="com.example.tv"
        )
        condition = build(
            context,
            {
                "type": "now_playing",
                "state": "playing",
                "title": "bunny",
                "app_id": "com.example.tv",
            },
        )
        assert not condition.needs_observation
        result = condition.evaluate(context, None)
        assert result.passed
        assert result.details["observed"]["title"] == "Big Buck Bunny"

    def test_now_playing_state_mismatch(self, context):
        from argus.models.common import PlaybackState

        context.device.playback_state = PlaybackState(state="paused")
        condition = build(context, {"type": "now_playing", "state": "playing"})
        result = condition.evaluate(context, None)
        assert not result.passed
        assert "state is 'paused'" in result.message

    def test_now_playing_position_advancing(self, base_config):
        from argus.models.common import PlaybackState

        class Advancing(FakeDevice):
            def __init__(self):
                super().__init__()
                self.position = 0.0

            def get_playback_state(self):
                self.position += 1.0
                return PlaybackState(state="playing", position=self.position)

        context = make_context(base_config, device=Advancing())
        condition = build(
            context, {"type": "now_playing", "position_advancing": True, "interval": 0}
        )
        result = condition.evaluate(context, None)
        assert result.passed
        assert result.details["second"]["position"] == 2.0

    def test_now_playing_position_stalled(self, context):
        from argus.models.common import PlaybackState

        context.device.playback_state = PlaybackState(state="paused", position=10.0)
        condition = build(
            context, {"type": "now_playing", "position_advancing": True, "interval": 0}
        )
        result = condition.evaluate(context, None)
        assert not result.passed
        assert "did not advance" in result.message

    def test_now_playing_requires_a_param(self, context):
        with pytest.raises(ConditionError, match="at least one"):
            build(context, {"type": "now_playing"})

    def test_now_playing_device_without_capability(self, base_config):
        from argus.adapters.base import DeviceCapabilities

        class NoPlayback(FakeDevice):
            @property
            def capabilities(self):
                return DeviceCapabilities(supports_screenshot=True)

        context = make_context(base_config, device=NoPlayback())
        condition = build(context, {"type": "now_playing", "state": "playing"})
        with pytest.raises(ConditionError, match="does not support playback state"):
            condition.evaluate(context, None)

    def test_now_playing_inside_not(self, context):
        condition = build(context, {"not": {"type": "now_playing", "state": "playing"}})
        assert condition.evaluate(context, None).passed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_playback_state.py tests/unit/test_conditions.py -k "playback or now_playing" -v`
Expected: `ImportError: cannot import name 'PlaybackState'` (collection error for the new file) and `Unknown condition type 'now_playing'` failures.

- [ ] **Step 3: Implement the model and hook**

In `src/argus/models/common.py` change the typing import to `from typing import Any, Literal` and append:

```python
class PlaybackState(BaseModel):
    """Media playback state reported by a device (e.g. Apple TV now-playing)."""

    state: Literal["playing", "paused", "stopped", "idle", "loading", "seeking"]
    title: str | None = None
    app_id: str | None = None
    position: float | None = None
    duration: float | None = None
```

In `src/argus/adapters/base.py`:
- change the import to `from argus.models.common import HealthCheckResult, PlaybackState, ScreenInfo`
- add `supports_playback_state: bool = False` as the last field of `DeviceCapabilities`
- add after `get_logs` in the observation section:

```python
    def get_playback_state(self) -> PlaybackState:
        """Current media playback state (devices with supports_playback_state)."""
        raise self._unsupported("get_playback_state")
```

In `src/argus/adapters/fake.py`:
- change the import to `from argus.models.common import HealthCheckResult, PlaybackState, ScreenInfo`
- in `FakeDevice.__init__` after `self.screenshot_count = 0` add `self.playback_state: PlaybackState | None = None`
- add `supports_playback_state=True,` to the `DeviceCapabilities(...)` in `capabilities`
- add after `get_logs`:

```python
    def get_playback_state(self) -> PlaybackState:
        return self.playback_state or PlaybackState(state="idle")
```

- [ ] **Step 4: Implement the condition**

In `src/argus/conditions/builtin.py` add `import time` after `import re`, then insert immediately before `def register(`:

```python
class _NowPlayingCondition(Condition):
    """True when the device's playback state matches the expected fields.

    Reads ``Device.get_playback_state()`` on every evaluation (so it works in
    ``wait_until``). ``position_advancing`` samples twice, ``interval`` seconds
    apart, and requires the position to increase. Negate with ``not:``.
    """

    name = "now_playing"

    def __init__(self, params: dict[str, Any]) -> None:
        self._state = params.get("state")
        self._title = params.get("title")
        self._app_id = params.get("app_id")
        self._position_advancing = bool(params.get("position_advancing", False))
        self._interval = float(params.get("interval", 1.0))
        if (
            self._state is None
            and self._title is None
            and self._app_id is None
            and not self._position_advancing
        ):
            raise ConditionError(
                "now_playing requires at least one of 'state', 'title', 'app_id' "
                "or 'position_advancing'.",
                remediation="Add the playback field(s) the test should assert on.",
            )

    def evaluate(
        self, context: TestContext, observation: Observation | None
    ) -> VerificationResult:
        device = context.require_device()
        if not device.capabilities.supports_playback_state:
            raise ConditionError(
                f"Device {device.name!r} does not support playback state; "
                "now_playing cannot run.",
                remediation="Use a device type that reports playback state (appletv, fake).",
            )
        first = device.get_playback_state()
        details: dict[str, Any] = {"observed": first.model_dump()}
        failures: list[str] = []
        if self._state is not None and first.state != self._state:
            failures.append(f"state is {first.state!r}, expected {self._state!r}")
        if self._title is not None and str(self._title).lower() not in (first.title or "").lower():
            failures.append(f"title {first.title!r} does not contain {self._title!r}")
        if self._app_id is not None and first.app_id != self._app_id:
            failures.append(f"app is {first.app_id!r}, expected {self._app_id!r}")
        if self._position_advancing:
            if self._interval > 0:
                time.sleep(self._interval)
            second = device.get_playback_state()
            details["second"] = second.model_dump()
            if (
                first.position is None
                or second.position is None
                or second.position <= first.position
            ):
                failures.append(
                    f"position did not advance ({first.position} -> {second.position})"
                )
        if failures:
            return VerificationResult(
                passed=False, verifier=self.name, message="; ".join(failures), details=details
            )
        return VerificationResult(
            passed=True,
            verifier=self.name,
            message=f"Now playing matches (state={first.state!r}, title={first.title!r})",
            details=details,
        )
```

Add to the end of `register()`:

```python
    factory.register("now_playing", lambda p, c: _NowPlayingCondition(p))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_playback_state.py tests/unit/test_conditions.py -k "playback or now_playing" -v`
Expected: 11 passed.

Run: `.venv/bin/python -m pytest -q 2>&1 | grep -c "^FAILED"` → `12` (baseline only). `.venv/bin/ruff check src/argus/models/common.py src/argus/adapters/base.py src/argus/adapters/fake.py src/argus/conditions/builtin.py tests/unit/test_playback_state.py tests/unit/test_conditions.py` → clean. `.venv/bin/mypy src` → only the 2 baseline errors.

- [ ] **Step 6: Document**

In `docs/test-authoring.md` add this row directly after the `log_contains` row:

```markdown
| `now_playing` | `state` (`playing`/`paused`/`stopped`/`idle`/`loading`/`seeking`), `title` (substring), `app_id`, `position_advancing` (+ `interval`, default 1s) | the device's media playback state matches (Apple TV via pyatv, fake); negate with `not:` |
```

Add directly after the `log_contains` example block (before `### Composition`):

````markdown
Playback assertions read the device's now-playing state, so a media test can
prove playback really started:

```yaml
- action: wait_until
  timeout: 15s
  condition:
    type: now_playing
    state: playing
    position_advancing: true
```
````

In `CHANGELOG.md` under `## [Unreleased]` → `### Added`, append after the `browser` bullet:

```markdown
- `now_playing` condition and `Device.get_playback_state()` hook: assert on a
  device's media playback state (state, title, app id, position advancing);
  usable in `wait_until`, negatable with `not:`.
```

- [ ] **Step 7: Commit**

```bash
git add src/argus/models/common.py src/argus/adapters/base.py src/argus/adapters/fake.py src/argus/conditions/builtin.py tests/unit/test_playback_state.py tests/unit/test_conditions.py docs/test-authoring.md CHANGELOG.md
git commit -m "Add playback state hook and now_playing condition."
```

---

### Task 2: `roku` adapter

**Files:**
- Create: `src/argus/adapters/roku.py`
- Modify: `src/argus/adapters/registry.py` (`register_builtin_devices`)
- Test: `tests/unit/test_roku_adapter.py` (new), `tests/integration/test_roku_adapter_e2e.py` (new)

**Interfaces:**
- Consumes: `Device`, `DeviceCapabilities`, `DeviceConfig`, `HealthCheckResult`, `ScreenInfo`, exceptions, `get_logger` (stdlib-style `ContextLogger`: `.debug("msg %s", arg)`).
- Produces: `RokuAdapter(name, *, host, dev_password=None, channel_zip=None, ecp_port=8060, debug_port=8085, installer_port=80, timeout=10.0)`; `RokuAdapter.from_config`; device type `"roku"`; `platform == "roku"`. (`installer_port` exists so tests can point the dev-installer client at a local server; production default 80.)

- [ ] **Step 1: Write the failing unit tests**

Create `tests/unit/test_roku_adapter.py`:

```python
"""RokuAdapter unit tests against pytest-httpserver (ECP + dev installer) and a fake console."""

from __future__ import annotations

import io
import socket
import threading
import time

import pytest
from PIL import Image
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

from argus.adapters.registry import DeviceRegistry
from argus.adapters.roku import RokuAdapter
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError

DEVICE_INFO = """<?xml version="1.0" encoding="UTF-8" ?>
<device-info>
  <model-name>Roku Ultra</model-name>
  <software-version>13.0.0</software-version>
  <ui-resolution>1080p</ui-resolution>
  <developer-enabled>true</developer-enabled>
</device-info>"""

ACTIVE_DEV = '<active-app><app id="dev" type="appl" version="1.0.0">My Channel</app></active-app>'
ACTIVE_HOME = "<active-app><app>Roku</app></active-app>"


def _png(size: tuple[int, int] = (64, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (9, 8, 7)).save(buf, format="PNG")
    return buf.getvalue()


def _digest_protected(body: bytes, content_type: str):
    """Handler that demands Digest auth once, then serves `body`."""

    def handler(request: Request) -> Response:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Digest "):
            return Response(
                "auth required",
                status=401,
                headers={"WWW-Authenticate": 'Digest realm="rokudev", nonce="abc123", qop="auth"'},
            )
        assert 'username="rokudev"' in auth
        return Response(body, status=200, content_type=content_type)

    return handler


class _FakeDebugConsole:
    """Minimal TCP server standing in for the BrightScript console on port 8085."""

    def __init__(self, lines: list[str]) -> None:
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        self._lines = lines
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._server.accept()
        except OSError:
            return
        with conn:
            for line in self._lines:
                conn.sendall((line + "\r\n").encode())
            time.sleep(0.3)

    def close(self) -> None:
        self._server.close()


@pytest.fixture
def roku(httpserver: HTTPServer) -> RokuAdapter:
    httpserver.expect_request("/query/device-info").respond_with_data(
        DEVICE_INFO, content_type="text/xml"
    )
    return RokuAdapter(
        "tv",
        host=httpserver.host,
        dev_password="secret",
        ecp_port=httpserver.port,
        installer_port=httpserver.port,
        debug_port=1,  # nothing listens; the reader just retries quietly
    )


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestIdentity:
    def test_capabilities_with_dev_password(self, roku):
        caps = roku.capabilities
        assert caps.supports_screenshot and caps.supports_keyboard
        assert caps.supports_app_lifecycle and caps.supports_logs
        assert not caps.supports_tap and not caps.supports_swipe
        assert roku.platform == "roku"

    def test_capabilities_without_dev_password(self):
        adapter = RokuAdapter("tv", host="10.0.0.5")
        assert not adapter.capabilities.supports_screenshot
        with pytest.raises(DeviceCapabilityError, match="screenshot"):
            adapter.screenshot()

    def test_tap_and_swipe_unsupported(self, roku):
        with pytest.raises(DeviceCapabilityError):
            roku.tap(1, 2)
        with pytest.raises(DeviceCapabilityError):
            roku.swipe(0, 0, 1, 1)


class TestConnection:
    def test_connect_and_health(self, roku):
        roku.connect()
        health = roku.health_check()
        assert health.healthy
        assert health.details["model"] == "Roku Ultra"
        assert roku.get_screen_info().size == (1920, 1080)
        roku.disconnect()

    def test_unreachable_host(self):
        adapter = RokuAdapter("tv", host="127.0.0.1", ecp_port=1, timeout=0.5)
        with pytest.raises(DeviceConnectionError, match="device-info"):
            adapter.connect()
        assert not adapter.health_check().healthy

    def test_operations_before_connect_raise(self, roku):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            roku.press_key("HOME")


class TestLifecycle:
    def test_start_stop_running(self, roku, httpserver: HTTPServer):
        httpserver.expect_request("/launch/dev", method="POST").respond_with_data("")
        httpserver.expect_request("/keypress/Home", method="POST").respond_with_data("")
        httpserver.expect_ordered_request("/query/active-app").respond_with_data(
            ACTIVE_DEV, content_type="text/xml"
        )
        httpserver.expect_ordered_request("/query/active-app").respond_with_data(
            ACTIVE_HOME, content_type="text/xml"
        )
        roku.connect()
        roku.start_application()
        assert roku.is_application_running()
        roku.stop_application()
        assert not roku.is_application_running()
        assert any(r.path == "/launch/dev" for r, _ in httpserver.log)
        assert any(r.path == "/keypress/Home" for r, _ in httpserver.log)

    def test_sideload_on_connect(self, httpserver: HTTPServer, tmp_path):
        zip_path = tmp_path / "channel.zip"
        zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        httpserver.expect_request("/query/device-info").respond_with_data(
            DEVICE_INFO, content_type="text/xml"
        )
        httpserver.expect_request("/plugin_install", method="POST").respond_with_handler(
            _digest_protected(b"<html>Install Success.</html>", "text/html")
        )
        adapter = RokuAdapter(
            "tv",
            host=httpserver.host,
            dev_password="secret",
            channel_zip=zip_path,
            ecp_port=httpserver.port,
            installer_port=httpserver.port,
            debug_port=1,
        )
        adapter.connect()
        installs = [r for r, _ in httpserver.log if r.path == "/plugin_install"]
        assert installs, "expected a POST to /plugin_install"
        body = installs[-1].get_data()
        assert b'name="mysubmit"' in body and b"Install" in body
        assert b'filename="channel.zip"' in body
        adapter.disconnect()

    def test_sideload_requires_dev_password(self, tmp_path):
        with pytest.raises(ConfigurationError, match="dev_password"):
            RokuAdapter("tv", host="10.0.0.5", channel_zip=tmp_path / "x.zip")


class TestObservation:
    def test_screenshot_uses_digest_auth(self, roku, httpserver: HTTPServer):
        httpserver.expect_request("/plugin_inspect", method="POST").respond_with_handler(
            _digest_protected(b"<html>Screenshot ok</html>", "text/html")
        )
        httpserver.expect_request("/pkgs/dev.jpg").respond_with_handler(
            _digest_protected(_png((64, 32)), "image/png")
        )
        roku.connect()
        img = roku.screenshot()
        assert img.mode == "RGB" and img.size == (64, 32)
        inspect = [r for r, _ in httpserver.log if r.path == "/plugin_inspect"]
        assert b"Screenshot" in inspect[-1].get_data()

    def test_wrong_dev_password(self, roku, httpserver: HTTPServer):
        def always_401(request: Request) -> Response:
            return Response(
                "nope",
                status=401,
                headers={"WWW-Authenticate": 'Digest realm="rokudev", nonce="n", qop="auth"'},
            )

        httpserver.expect_request("/plugin_inspect", method="POST").respond_with_handler(always_401)
        roku.connect()
        with pytest.raises(DeviceConnectionError, match="developer password"):
            roku.screenshot()

    def test_logs_from_debug_console(self, httpserver: HTTPServer):
        console = _FakeDebugConsole(["BrightScript Debugger", "Player: state=PLAYING", "done"])
        httpserver.expect_request("/query/device-info").respond_with_data(
            DEVICE_INFO, content_type="text/xml"
        )
        adapter = RokuAdapter(
            "tv",
            host="127.0.0.1",
            ecp_port=httpserver.port,
            installer_port=httpserver.port,
            debug_port=console.port,
        )
        try:
            adapter.connect()
            assert _wait_for(lambda: "done" in adapter.get_logs())
            assert adapter.get_logs(lines=2).splitlines() == ["Player: state=PLAYING", "done"]
            assert adapter.get_logs(lines=0) == ""
        finally:
            adapter.disconnect()
            console.close()

    def test_logs_cleared_on_start(self, roku, httpserver: HTTPServer):
        httpserver.expect_request("/launch/dev", method="POST").respond_with_data("")
        roku.connect()
        roku._logs.append("stale")
        roku.start_application()
        assert roku.get_logs() == ""


class TestInput:
    @pytest.mark.parametrize(
        ("key", "ecp"),
        [
            ("KEYCODE_DPAD_LEFT", "Left"),
            ("enter", "Select"),
            ("BACK", "Back"),
            ("MEDIA_PLAY_PAUSE", "Play"),
            ("MEDIA_FAST_FORWARD", "Fwd"),
            ("Info", "Info"),
            ("a", "Lit_a"),
            ("%", "Lit_%25"),
            ("InstantReplay", "InstantReplay"),
        ],
    )
    def test_press_key_mapping(self, roku, httpserver: HTTPServer, key, ecp):
        httpserver.expect_request(f"/keypress/{ecp}", method="POST").respond_with_data("")
        roku.connect()
        roku.press_key(key)
        assert httpserver.log[-1][0].path == f"/keypress/{ecp}"


class TestConfig:
    def test_from_config(self):
        config = DeviceConfig.model_validate(
            {
                "type": "roku",
                "host": "10.0.0.5",
                "dev_password": "pw",
                "ecp_port": 9060,
                "debug_port": 9085,
                "timeout": 3,
            }
        )
        adapter = RokuAdapter.from_config("tv", config)
        assert adapter._host == "10.0.0.5"
        assert adapter._dev_password == "pw"
        assert adapter._ecp_port == 9060 and adapter._debug_port == 9085
        assert adapter._timeout == 3.0

    def test_from_config_requires_host(self):
        with pytest.raises(ConfigurationError, match="host"):
            RokuAdapter.from_config("tv", DeviceConfig.model_validate({"type": "roku"}))

    def test_registered_as_roku(self):
        registry = DeviceRegistry()
        assert "roku" in registry.types()
        device = registry.create(
            "tv", DeviceConfig.model_validate({"type": "roku", "host": "10.0.0.5"})
        )
        assert isinstance(device, RokuAdapter)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_roku_adapter.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'argus.adapters.roku'`.

- [ ] **Step 3: Implement the adapter**

Create `src/argus/adapters/roku.py`:

```python
"""Roku device adapter (developer mode).

Control goes through Roku's External Control Protocol (ECP, port 8060):
key presses, launching the sideloaded ``dev`` channel, and device queries.
Screenshots and channel sideloading use the developer web installer (port 80,
Digest auth as user ``rokudev``) and therefore only work for the sideloaded
channel on a Roku with developer mode enabled. The BrightScript debug console
(telnet, port 8085) is streamed into the device log buffer so ``log_contains``
works. Stdlib only — no optional dependency.
"""

from __future__ import annotations

import contextlib
import io
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceConnectionError, ScreenshotError
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo

_DEFAULT_TIMEOUT = 10.0
_MAX_LOG_LINES = 5000
_DEV_APP_ID = "dev"
_DEV_USER = "rokudev"

# Roku responses are parsed with the stdlib parser: Python 3.12's ElementTree does not
# resolve external entities, and the Roku is a trusted LAN device.

_RESOLUTIONS = {
    "480p": (720, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2160p": (3840, 2160),
    "4k": (3840, 2160),
}

# Android-style key names -> ECP key names. Unknown names pass through unchanged
# (ECP names such as "InstantReplay" or "Search" work directly); single
# characters become literal text input (Lit_<char>).
_KEY_MAP = {
    "DPAD_UP": "Up",
    "DPAD_DOWN": "Down",
    "DPAD_LEFT": "Left",
    "DPAD_RIGHT": "Right",
    "UP": "Up",
    "DOWN": "Down",
    "LEFT": "Left",
    "RIGHT": "Right",
    "ENTER": "Select",
    "DPAD_CENTER": "Select",
    "SELECT": "Select",
    "BACK": "Back",
    "HOME": "Home",
    "MEDIA_PLAY_PAUSE": "Play",
    "MEDIA_PLAY": "Play",
    "MEDIA_PAUSE": "Play",
    "MEDIA_REWIND": "Rev",
    "MEDIA_FAST_FORWARD": "Fwd",
    "INFO": "Info",
    "DEL": "Backspace",
    "BACKSPACE": "Backspace",
    "SEARCH": "Search",
}


def _encode_multipart(
    fields: dict[str, str], files: dict[str, tuple[str, bytes]]
) -> tuple[bytes, str]:
    """Build a multipart/form-data body (the dev installer is a plain HTML form)."""
    boundary = f"----argus{uuid.uuid4().hex}"
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(value.encode())
        out.write(b"\r\n")
    for name, (filename, data) in files.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        out.write(b"Content-Type: application/octet-stream\r\n\r\n")
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


class _DebugConsoleReader(threading.Thread):
    """Streams the BrightScript debug console into a bounded deque.

    Reconnects with backoff: the Roku drops the console connection whenever
    the channel is (re)launched. Failures are logged at debug level only.
    """

    def __init__(self, host: str, port: int, sink: deque[str], log: Any) -> None:
        super().__init__(daemon=True, name=f"roku-console-{host}")
        self._host = host
        self._port = port
        self._sink = sink
        self._log = log
        self._stop = threading.Event()
        self._sock: socket.socket | None = None

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()

    def run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                sock = socket.create_connection((self._host, self._port), timeout=5)
            except OSError as exc:
                self._log.debug("Roku debug console unavailable: %s", exc)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 10.0)
                continue
            self._sock = sock
            backoff = 0.5
            try:
                self._pump(sock)
            except OSError as exc:
                self._log.debug("Roku debug console dropped: %s", exc)
            finally:
                with contextlib.suppress(OSError):
                    sock.close()
                self._sock = None

    def _pump(self, sock: socket.socket) -> None:
        sock.settimeout(1.0)
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                continue
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self._sink.append(line.decode("utf-8", errors="replace").rstrip("\r"))


class RokuAdapter(Device):
    """Controls a developer-mode Roku running a sideloaded channel."""

    def __init__(
        self,
        name: str,
        *,
        host: str,
        dev_password: str | None = None,
        channel_zip: str | Path | None = None,
        ecp_port: int = 8060,
        debug_port: int = 8085,
        installer_port: int = 80,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(name)
        if channel_zip is not None and not dev_password:
            raise ConfigurationError(
                f"Roku device {name!r}: 'channel_zip' requires 'dev_password'.",
                remediation="Set devices.<name>.dev_password to the developer-mode password.",
            )
        self._host = host
        self._dev_password = dev_password
        self._channel_zip = Path(channel_zip) if channel_zip is not None else None
        self._ecp_port = int(ecp_port)
        self._debug_port = int(debug_port)
        self._installer_port = int(installer_port)
        self._timeout = float(timeout)
        self._connected = False
        self._app_running = False
        self._resolution: tuple[int, int] = (1920, 1080)
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._reader: _DebugConsoleReader | None = None
        self._log = get_logger("argus.roku", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> RokuAdapter:
        options: dict[str, Any] = config.options
        host = options.get("host")
        if not host:
            raise ConfigurationError(
                f"Roku device {name!r} requires a 'host' option.",
                remediation="Set devices.<name>.host to the Roku's IP address.",
            )
        return cls(
            name,
            host=str(host),
            dev_password=options.get("dev_password"),
            channel_zip=options.get("channel_zip"),
            ecp_port=int(options.get("ecp_port", 8060)),
            debug_port=int(options.get("debug_port", 8085)),
            installer_port=int(options.get("installer_port", 80)),
            timeout=float(options.get("timeout", _DEFAULT_TIMEOUT)),
        )

    # -- identity -----------------------------------------------------------------

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=bool(self._dev_password),
            supports_keyboard=True,
            supports_app_lifecycle=True,
            supports_logs=True,
            supports_instrumentation=True,
        )

    @property
    def platform(self) -> str:
        return "roku"

    # -- HTTP plumbing ----------------------------------------------------------------

    def _ecp(self, method: str, path: str) -> bytes:
        url = f"http://{self._host}:{self._ecp_port}/{path}"
        request = urllib.request.Request(
            url, method=method, data=b"" if method == "POST" else None
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return bytes(response.read())
        except (urllib.error.URLError, OSError) as exc:
            raise DeviceConnectionError(
                f"Roku {self._host!r}: {method} /{path} failed: {exc}",
                remediation="Check the Roku is powered on, reachable on the network, and "
                "'host'/'ecp_port' are correct.",
            ) from exc

    @property
    def _installer_url(self) -> str:
        return f"http://{self._host}:{self._installer_port}"

    def _installer(self, path: str, body: bytes | None, content_type: str | None) -> bytes:
        if not self._dev_password:
            raise DeviceConnectionError(
                f"Roku {self._host!r}: developer installer needs 'dev_password'.",
                remediation="Enable developer mode on the Roku and set devices.<name>.dev_password.",
            )
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, self._installer_url, _DEV_USER, self._dev_password)
        opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(manager))
        headers = {"Content-Type": content_type} if content_type else {}
        request = urllib.request.Request(
            self._installer_url + path,
            data=body,
            headers=headers,
            method="POST" if body is not None else "GET",
        )
        try:
            with opener.open(request, timeout=self._timeout) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise DeviceConnectionError(
                    f"Roku {self._host!r}: developer password rejected.",
                    remediation="Check devices.<name>.dev_password (user 'rokudev').",
                ) from exc
            raise DeviceConnectionError(
                f"Roku {self._host!r}: installer {path} returned HTTP {exc.code}.",
                remediation="Check developer mode is enabled (Home x3, Up x2, Right, Left, "
                "Right, Left, Right).",
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DeviceConnectionError(
                f"Roku {self._host!r}: installer {path} failed: {exc}",
                remediation="Check developer mode is enabled and port "
                f"{self._installer_port} is reachable.",
            ) from exc

    def _installer_form(
        self, path: str, fields: dict[str, str], files: dict[str, tuple[str, bytes]]
    ) -> bytes:
        body, content_type = _encode_multipart(fields, files)
        return self._installer(path, body, content_type)

    def _require_connected(self) -> None:
        if not self._connected:
            raise DeviceConnectionError(
                f"Roku device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )

    def _device_info(self) -> dict[str, str]:
        root = ET.fromstring(self._ecp("GET", "query/device-info"))
        return {child.tag: (child.text or "").strip() for child in root}

    # -- connection -----------------------------------------------------------------

    def connect(self) -> None:
        if self._connected:
            return
        info = self._device_info()
        self._resolution = _RESOLUTIONS.get(info.get("ui-resolution", "").lower(), (1920, 1080))
        if self._channel_zip is not None:
            self._sideload(self._channel_zip)
        self._reader = _DebugConsoleReader(self._host, self._debug_port, self._logs, self._log)
        self._reader.start()
        self._connected = True
        self._log.info("Connected to Roku %s (%s)", self._host, info.get("model-name", "?"))

    def disconnect(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.stop()
            reader.join(timeout=2.0)
        self._connected = False
        self._app_running = False

    def is_available(self) -> bool:
        return True

    def health_check(self) -> HealthCheckResult:
        try:
            info = self._device_info()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(str(exc))
        return HealthCheckResult.ok(
            "roku reachable",
            model=info.get("model-name", ""),
            software=info.get("software-version", ""),
            developer_enabled=info.get("developer-enabled", ""),
            connected=self._connected,
        )

    def _sideload(self, archive: Path) -> None:
        if not archive.is_file():
            raise ConfigurationError(
                f"Roku device {self.name!r}: channel_zip {str(archive)!r} not found.",
                remediation="Point devices.<name>.channel_zip at the packaged channel .zip.",
            )
        self._log.info("Sideloading %s", archive.name)
        self._installer_form(
            "/plugin_install",
            {"mysubmit": "Install"},
            {"archive": (archive.name, archive.read_bytes())},
        )

    # -- application lifecycle --------------------------------------------------------

    def start_application(self) -> None:
        self._require_connected()
        self._logs.clear()
        self._ecp("POST", f"launch/{_DEV_APP_ID}")
        self._app_running = True

    def stop_application(self) -> None:
        self._require_connected()
        self._ecp("POST", "keypress/Home")
        self._app_running = False

    def is_application_running(self) -> bool:
        self._require_connected()
        root = ET.fromstring(self._ecp("GET", "query/active-app"))
        app = root.find("app")
        return app is not None and app.get("id") == _DEV_APP_ID

    # -- observation --------------------------------------------------------------------

    def screenshot(self) -> Image:
        if not self._dev_password:
            raise self._unsupported("screenshot")
        self._require_connected()
        try:
            self._installer_form(
                "/plugin_inspect", {"mysubmit": "Screenshot"}, {"archive": ("", b"")}
            )
            data = self._installer("/pkgs/dev.jpg", None, None)
            with PILImage.open(io.BytesIO(data)) as img:
                return img.convert("RGB")
        except DeviceConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001 - any decode failure
            raise ScreenshotError(
                f"Roku screenshot failed: {exc}",
                remediation="Screenshots only work for the sideloaded channel while it runs.",
            ) from exc

    def get_screen_info(self) -> ScreenInfo:
        return ScreenInfo(width=self._resolution[0], height=self._resolution[1])

    def get_logs(self, lines: int = 200) -> str:
        entries = list(self._logs)[-lines:] if lines > 0 else []
        return "\n".join(entries)

    # -- input ----------------------------------------------------------------------------

    def press_key(self, key: str) -> None:
        self._require_connected()
        name = key.removeprefix("KEYCODE_")
        if len(name) == 1:
            ecp_key = "Lit_" + urllib.parse.quote(name, safe="")
        else:
            ecp_key = _KEY_MAP.get(name.upper(), name)
        self._ecp("POST", f"keypress/{ecp_key}")
```

In `src/argus/adapters/registry.py` `register_builtin_devices` add `from argus.adapters.roku import RokuAdapter` (alphabetical, after `fake`) and `registry.register("roku", RokuAdapter.from_config)` after the `browser` line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_roku_adapter.py -v`
Expected: all pass. If `test_screenshot_uses_digest_auth` fails with a 401, check the handler's `WWW-Authenticate` header includes `qop="auth"` (urllib's digest handler requires `realm` and `nonce`; `qop` must be `auth` or absent).

Run: `.venv/bin/python -m pytest -q 2>&1 | grep -c "^FAILED"` → `12`. `.venv/bin/ruff check src/argus/adapters/roku.py src/argus/adapters/registry.py tests/unit/test_roku_adapter.py` → clean. `.venv/bin/mypy src` → only the 2 baseline errors.

- [ ] **Step 5: Write the integration test**

Create `tests/integration/test_roku_adapter_e2e.py`:

```python
"""RokuAdapter against a real developer-mode Roku. Skipped unless ARGUS_ROKU_HOST is set."""

from __future__ import annotations

import os
import time

import pytest

from argus.adapters.roku import RokuAdapter

pytestmark = pytest.mark.integration

HOST = os.environ.get("ARGUS_ROKU_HOST")
DEV_PASSWORD = os.environ.get("ARGUS_ROKU_DEV_PASSWORD")


@pytest.fixture
def roku():
    if not HOST:
        pytest.skip("ARGUS_ROKU_HOST not set")
    device = RokuAdapter("tv", host=HOST, dev_password=DEV_PASSWORD)
    device.connect()
    yield device
    device.disconnect()


def test_device_info_and_keys(roku: RokuAdapter):
    health = roku.health_check()
    assert health.healthy
    assert roku.get_screen_info().width > 0
    roku.press_key("HOME")
    time.sleep(1)
    assert not roku.is_application_running()


def test_sideloaded_channel_screenshot(roku: RokuAdapter):
    if not DEV_PASSWORD:
        pytest.skip("ARGUS_ROKU_DEV_PASSWORD not set")
    roku.start_application()
    time.sleep(5)
    assert roku.is_application_running()
    img = roku.screenshot()
    assert img.size == roku.get_screen_info().size
    assert roku.get_logs() != ""
```

Run: `.venv/bin/python -m pytest tests/integration/test_roku_adapter_e2e.py -m integration -v`
Expected: 2 skipped ("ARGUS_ROKU_HOST not set") on a machine without a Roku; 2 passed when the variables point at a dev-mode Roku running a sideloaded channel.

- [ ] **Step 6: Commit**

```bash
git add src/argus/adapters/roku.py src/argus/adapters/registry.py tests/unit/test_roku_adapter.py tests/integration/test_roku_adapter_e2e.py
git commit -m "Add Roku device adapter using ECP and the developer installer."
```

---

### Task 3: `tvos_sim` adapter

**Files:**
- Create: `src/argus/adapters/tvos_sim.py`
- Modify: `src/argus/adapters/registry.py`
- Test: `tests/unit/test_tvos_sim_adapter.py` (new), `tests/integration/test_tvos_sim_adapter_e2e.py` (new)

**Interfaces:**
- Produces: `CommandResult(returncode: int, stdout: bytes, stderr: bytes)`; `Runner = Callable[[list[str]], CommandResult]`; `Spawner = Callable[[list[str]], Any]` (returns an object with `.stdout` (binary file), `.terminate()`, `.wait(timeout)`); `TvosSimAdapter(name, *, bundle_id, udid="booted", app_path=None, boot=True, process_name=None, timeout=30.0, runner=None, spawner=None)`; `from_config`; device type `"tvos_sim"`; `platform == "tvos_sim"`.
- Key handling deviation from the spec's generic rule: `osascript` has no notion of pass-through names, so single characters become `keystroke "<c>"`, mapped names become `key code <n>`, and any other name raises `DeviceCapabilityError`.

- [ ] **Step 1: Write the failing unit tests**

Create `tests/unit/test_tvos_sim_adapter.py`:

```python
"""TvosSimAdapter unit tests with an injected command runner (no Xcode needed)."""

from __future__ import annotations

import io
import json
import time

import pytest
from PIL import Image

from argus.adapters.registry import DeviceRegistry
from argus.adapters.tvos_sim import CommandResult, TvosSimAdapter
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError

UDID = "AAAA-1111"
DEVICES_JSON = json.dumps(
    {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.tvOS-17-0": [
                {"udid": UDID, "name": "Apple TV 4K", "state": "Booted", "isAvailable": True},
                {"udid": "BBBB-2222", "name": "Apple TV", "state": "Shutdown", "isAvailable": True},
            ],
            "com.apple.CoreSimulator.SimRuntime.iOS-17-0": [
                {"udid": "CCCC-3333", "name": "iPhone 15", "state": "Booted", "isAvailable": True}
            ],
        }
    }
).encode()


def _png(size=(1920, 1080)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


class FakeRunner:
    """Records argv lists; answers by longest matching argv prefix."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: dict[tuple[str, ...], CommandResult] = {}
        self.responses[("xcrun", "simctl", "list", "devices", "-j")] = CommandResult(
            0, DEVICES_JSON, b""
        )
        self.responses[("xcrun", "simctl", "io")] = CommandResult(0, _png(), b"")

    def __call__(self, argv: list[str]) -> CommandResult:
        self.calls.append(list(argv))
        for prefix in sorted(self.responses, key=len, reverse=True):
            if tuple(argv[: len(prefix)]) == prefix:
                return self.responses[prefix]
        return CommandResult(0, b"", b"")

    def argv_with(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if tuple(c[: len(prefix)]) == prefix]


class FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = io.BytesIO("".join(line + "\n" for line in lines).encode())
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def process() -> FakeProcess:
    return FakeProcess(["app started", "Player: state=PLAYING"])


@pytest.fixture
def sim(runner: FakeRunner, process: FakeProcess) -> TvosSimAdapter:
    return TvosSimAdapter(
        "sim", bundle_id="com.example.tv", runner=runner, spawner=lambda argv: process
    )


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestIdentity:
    def test_capabilities(self, sim):
        caps = sim.capabilities
        assert caps.supports_screenshot and caps.supports_keyboard and caps.supports_logs
        assert caps.supports_app_lifecycle
        assert not caps.supports_tap and not caps.supports_swipe
        assert sim.platform == "tvos_sim"

    def test_tap_unsupported(self, sim):
        with pytest.raises(DeviceCapabilityError):
            sim.tap(1, 1)


class TestConnection:
    def test_connect_resolves_booted_tvos_device(self, sim, runner):
        sim.connect()
        assert sim._udid == UDID
        assert ["open", "-a", "Simulator"] in runner.calls
        spawn_free = runner.argv_with("xcrun", "simctl", "boot")
        assert spawn_free == []  # already booted
        assert sim.health_check().healthy

    def test_connect_boots_shutdown_device(self, runner, process):
        adapter = TvosSimAdapter(
            "sim",
            bundle_id="com.example.tv",
            udid="BBBB-2222",
            runner=runner,
            spawner=lambda argv: process,
        )
        adapter.connect()
        assert runner.argv_with("xcrun", "simctl", "boot", "BBBB-2222")
        assert runner.argv_with("xcrun", "simctl", "bootstatus", "BBBB-2222", "-b")

    def test_connect_fails_without_booted_tvos(self, runner, process):
        runner.responses[("xcrun", "simctl", "list", "devices", "-j")] = CommandResult(
            0, json.dumps({"devices": {}}).encode(), b""
        )
        adapter = TvosSimAdapter(
            "sim", bundle_id="com.example.tv", runner=runner, spawner=lambda argv: process
        )
        with pytest.raises(DeviceConnectionError, match="no booted tvOS simulator"):
            adapter.connect()

    def test_connect_installs_app_path(self, runner, process, tmp_path):
        app = tmp_path / "Demo.app"
        app.mkdir()
        adapter = TvosSimAdapter(
            "sim",
            bundle_id="com.example.tv",
            app_path=app,
            runner=runner,
            spawner=lambda argv: process,
        )
        adapter.connect()
        assert runner.argv_with("xcrun", "simctl", "install", UDID, str(app))

    def test_missing_xcrun(self, process):
        def no_xcrun(argv):
            raise FileNotFoundError("xcrun")

        adapter = TvosSimAdapter(
            "sim", bundle_id="com.example.tv", runner=no_xcrun, spawner=lambda argv: process
        )
        assert adapter.is_available() is False
        with pytest.raises(DeviceConnectionError, match="Xcode"):
            adapter.connect()

    def test_operations_before_connect_raise(self, sim):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            sim.screenshot()


class TestLifecycle:
    def test_start_stop_reset(self, sim, runner, tmp_path):
        sim.connect()
        sim.start_application()
        assert runner.argv_with("xcrun", "simctl", "launch", UDID, "com.example.tv")
        assert sim.is_application_running()
        sim.stop_application()
        assert runner.argv_with("xcrun", "simctl", "terminate", UDID, "com.example.tv")
        assert not sim.is_application_running()
        sim.reset_application()
        assert len(runner.argv_with("xcrun", "simctl", "launch")) == 2
        assert not runner.argv_with("xcrun", "simctl", "uninstall")

    def test_reset_reinstalls_when_app_path(self, runner, process, tmp_path):
        app = tmp_path / "Demo.app"
        app.mkdir()
        adapter = TvosSimAdapter(
            "sim",
            bundle_id="com.example.tv",
            app_path=app,
            runner=runner,
            spawner=lambda argv: process,
        )
        adapter.connect()
        adapter.reset_application()
        assert runner.argv_with("xcrun", "simctl", "uninstall", UDID, "com.example.tv")
        assert len(runner.argv_with("xcrun", "simctl", "install")) == 2

    def test_launch_failure_raises(self, sim, runner):
        runner.responses[("xcrun", "simctl", "launch")] = CommandResult(1, b"", b"no such app")
        sim.connect()
        with pytest.raises(DeviceConnectionError, match="no such app"):
            sim.start_application()

    def test_disconnect_stops_log_stream(self, sim, process):
        sim.connect()
        sim.disconnect()
        assert process.terminated
        assert not sim.is_application_running()


class TestObservation:
    def test_screenshot_and_screen_info(self, sim, runner):
        sim.connect()
        img = sim.screenshot()
        assert img.mode == "RGB" and img.size == (1920, 1080)
        assert runner.argv_with("xcrun", "simctl", "io", UDID, "screenshot", "--type", "png", "-")
        assert sim.get_screen_info().size == (1920, 1080)

    def test_logs_stream(self, sim):
        sim.connect()
        assert _wait_for(lambda: "PLAYING" in sim.get_logs())
        assert sim.get_logs().splitlines() == ["app started", "Player: state=PLAYING"]
        assert sim.get_logs(lines=1) == "Player: state=PLAYING"

    def test_log_predicate_uses_process_name(self, sim, runner):
        sim.connect()
        assert sim._spawned_argv[-1] == 'process == "tv"'


class TestInput:
    @pytest.mark.parametrize(
        ("key", "fragment"),
        [
            ("KEYCODE_DPAD_UP", "key code 126"),
            ("left", "key code 123"),
            ("ENTER", "key code 36"),
            ("BACK", "key code 53"),
            ("MEDIA_PLAY_PAUSE", "key code 49"),
            ("HOME", 'keystroke "h" using {command down, shift down}'),
            ("a", 'keystroke "a"'),
        ],
    )
    def test_press_key_scripts(self, sim, runner, key, fragment):
        sim.connect()
        sim.press_key(key)
        script = runner.calls[-1]
        assert script[0] == "osascript"
        assert 'tell application "Simulator" to activate' in script
        assert any(fragment in part for part in script)

    def test_unknown_key_raises(self, sim):
        sim.connect()
        with pytest.raises(DeviceCapabilityError, match="F13"):
            sim.press_key("F13")

    def test_accessibility_denied(self, sim, runner):
        runner.responses[("osascript",)] = CommandResult(
            1, b"", b"osascript is not allowed assistive access. (-1719)"
        )
        sim.connect()
        with pytest.raises(DeviceConnectionError, match="Accessibility"):
            sim.press_key("ENTER")


class TestConfig:
    def test_from_config(self, tmp_path):
        config = DeviceConfig.model_validate(
            {
                "type": "tvos_sim",
                "bundle_id": "com.example.tv",
                "udid": UDID,
                "boot": False,
                "process_name": "DemoTV",
                "timeout": 5,
            }
        )
        adapter = TvosSimAdapter.from_config("sim", config)
        assert adapter._bundle_id == "com.example.tv"
        assert adapter._requested_udid == UDID
        assert adapter._boot is False
        assert adapter._process_name == "DemoTV"
        assert adapter._timeout == 5.0

    def test_from_config_requires_bundle_id(self):
        with pytest.raises(ConfigurationError, match="bundle_id"):
            TvosSimAdapter.from_config("sim", DeviceConfig.model_validate({"type": "tvos_sim"}))

    def test_registered_as_tvos_sim(self):
        registry = DeviceRegistry()
        assert "tvos_sim" in registry.types()
        device = registry.create(
            "sim", DeviceConfig.model_validate({"type": "tvos_sim", "bundle_id": "com.x.y"})
        )
        assert isinstance(device, TvosSimAdapter)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_tvos_sim_adapter.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'argus.adapters.tvos_sim'`.

- [ ] **Step 3: Implement the adapter**

Create `src/argus/adapters/tvos_sim.py`:

```python
"""tvOS Simulator adapter (macOS + Xcode).

Everything goes through ``xcrun simctl``: boot, install, launch/terminate,
PNG screenshots, and a streamed ``log`` subprocess that feeds the device log
buffer. The Simulator has no remote-key API, so key presses are sent as
keyboard shortcuts to the Simulator app via ``osascript`` (this needs the
terminal to have Accessibility permission in System Settings).
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceConnectionError, ScreenshotError
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo

_DEFAULT_TIMEOUT = 30.0
_MAX_LOG_LINES = 5000
_BOOTED = "booted"

# macOS virtual key codes for the Simulator's remote shortcuts.
_KEY_CODES = {
    "DPAD_UP": 126,
    "UP": 126,
    "DPAD_DOWN": 125,
    "DOWN": 125,
    "DPAD_LEFT": 123,
    "LEFT": 123,
    "DPAD_RIGHT": 124,
    "RIGHT": 124,
    "ENTER": 36,
    "DPAD_CENTER": 36,
    "SELECT": 36,
    "BACK": 53,
    "MENU": 53,
    "ESCAPE": 53,
    "MEDIA_PLAY_PAUSE": 49,
    "SPACE": 49,
}
_KEYSTROKES = {
    "HOME": 'keystroke "h" using {command down, shift down}',
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


Runner = Callable[[list[str]], CommandResult]
Spawner = Callable[[list[str]], Any]


def _subprocess_runner(timeout: float) -> Runner:
    def run(argv: list[str]) -> CommandResult:
        completed = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    return run


def _subprocess_spawner(argv: list[str]) -> Any:
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


class _LogPump(threading.Thread):
    """Copies a process's stdout lines into a bounded deque."""

    def __init__(self, process: Any, sink: deque[str]) -> None:
        super().__init__(daemon=True, name="tvos-sim-log")
        self._process = process
        self._sink = sink

    def run(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            self._sink.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))


class TvosSimAdapter(Device):
    """Controls a tvOS app running in the Xcode Simulator."""

    def __init__(
        self,
        name: str,
        *,
        bundle_id: str,
        udid: str = _BOOTED,
        app_path: str | Path | None = None,
        boot: bool = True,
        process_name: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        runner: Runner | None = None,
        spawner: Spawner | None = None,
    ) -> None:
        super().__init__(name)
        self._bundle_id = bundle_id
        self._requested_udid = udid
        self._app_path = Path(app_path) if app_path is not None else None
        self._boot = boot
        self._process_name = process_name or bundle_id.rsplit(".", 1)[-1]
        self._timeout = float(timeout)
        self._run: Runner = runner or _subprocess_runner(self._timeout)
        self._spawn: Spawner = spawner or _subprocess_spawner
        self._udid: str | None = None
        self._app_running = False
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._log_process: Any = None
        self._log_pump: _LogPump | None = None
        self._spawned_argv: list[str] = []
        self._screen_size: tuple[int, int] | None = None
        self._log = get_logger("argus.tvos_sim", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> TvosSimAdapter:
        options: dict[str, Any] = config.options
        bundle_id = options.get("bundle_id")
        if not bundle_id:
            raise ConfigurationError(
                f"tvOS Simulator device {name!r} requires a 'bundle_id' option.",
                remediation="Set devices.<name>.bundle_id to the app's bundle identifier.",
            )
        return cls(
            name,
            bundle_id=str(bundle_id),
            udid=str(options.get("udid", _BOOTED)),
            app_path=options.get("app_path"),
            boot=bool(options.get("boot", True)),
            process_name=options.get("process_name"),
            timeout=float(options.get("timeout", _DEFAULT_TIMEOUT)),
        )

    # -- identity -----------------------------------------------------------------

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=True,
            supports_keyboard=True,
            supports_app_lifecycle=True,
            supports_logs=True,
            supports_instrumentation=True,
        )

    @property
    def platform(self) -> str:
        return "tvos_sim"

    # -- command plumbing -------------------------------------------------------------

    def _command(self, argv: list[str], *, check: bool = True) -> CommandResult:
        try:
            result = self._run(argv)
        except FileNotFoundError as exc:
            raise DeviceConnectionError(
                f"{argv[0]!r} not found; the tvOS Simulator needs Xcode.",
                remediation="Install Xcode and run: xcode-select --install",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DeviceConnectionError(
                f"{' '.join(argv[:3])} timed out after {self._timeout}s.",
                remediation="Check the Simulator is responsive or raise 'timeout'.",
            ) from exc
        if check and result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise DeviceConnectionError(
                f"{' '.join(argv[:3])} failed ({result.returncode}): {stderr}",
                remediation="Run the command manually to diagnose: " + " ".join(argv),
            )
        return result

    def _simctl(self, *args: str, check: bool = True) -> CommandResult:
        return self._command(["xcrun", "simctl", *args], check=check)

    def _require_udid(self) -> str:
        if self._udid is None:
            raise DeviceConnectionError(
                f"tvOS Simulator device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        return self._udid

    def _list_tvos_devices(self) -> list[dict[str, Any]]:
        result = self._simctl("list", "devices", "-j")
        payload = json.loads(result.stdout.decode("utf-8") or "{}")
        devices: list[dict[str, Any]] = []
        for runtime, entries in payload.get("devices", {}).items():
            if "tvos" in runtime.lower():
                devices.extend(entries)
        return devices

    def _resolve_udid(self) -> tuple[str, str]:
        """Return (udid, state) for the requested simulator."""
        devices = self._list_tvos_devices()
        if self._requested_udid == _BOOTED:
            for device in devices:
                if device.get("state") == "Booted":
                    return str(device["udid"]), "Booted"
            raise DeviceConnectionError(
                "no booted tvOS simulator found.",
                remediation="Boot one in Xcode (or run: xcrun simctl boot <udid>) or set "
                "devices.<name>.udid so Argus can boot it.",
            )
        for device in devices:
            if device.get("udid") == self._requested_udid:
                return str(device["udid"]), str(device.get("state", ""))
        raise DeviceConnectionError(
            f"tvOS simulator {self._requested_udid!r} not found.",
            remediation="List simulators with: xcrun simctl list devices",
        )

    # -- connection -----------------------------------------------------------------

    def connect(self) -> None:
        if self._udid is not None:
            return
        udid, state = self._resolve_udid()
        if state != "Booted":
            if not self._boot:
                raise DeviceConnectionError(
                    f"tvOS simulator {udid!r} is {state or 'not booted'} and boot=false.",
                    remediation="Boot it in Xcode or set devices.<name>.boot: true.",
                )
            self._simctl("boot", udid)
            self._simctl("bootstatus", udid, "-b")
        self._command(["open", "-a", "Simulator"], check=False)
        if self._app_path is not None:
            self._simctl("install", udid, str(self._app_path))
        self._udid = udid
        self._start_log_stream()
        self._log.info("Connected to tvOS simulator %s", udid)

    def _start_log_stream(self) -> None:
        argv = [
            "xcrun",
            "simctl",
            "spawn",
            self._require_udid(),
            "log",
            "stream",
            "--style",
            "compact",
            "--predicate",
            f'process == "{self._process_name}"',
        ]
        self._spawned_argv = argv
        try:
            self._log_process = self._spawn(argv)
        except FileNotFoundError as exc:
            raise DeviceConnectionError(
                "'xcrun' not found; the tvOS Simulator needs Xcode.",
                remediation="Install Xcode and run: xcode-select --install",
            ) from exc
        self._log_pump = _LogPump(self._log_process, self._logs)
        self._log_pump.start()

    def _stop_log_stream(self) -> None:
        process, self._log_process = self._log_process, None
        if process is not None:
            with contextlib.suppress(Exception):
                process.terminate()
            with contextlib.suppress(Exception):
                process.wait(timeout=2.0)
        pump, self._log_pump = self._log_pump, None
        if pump is not None:
            pump.join(timeout=2.0)

    def disconnect(self) -> None:
        self._stop_log_stream()
        self._udid = None
        self._app_running = False

    def is_available(self) -> bool:
        try:
            return self._run(["xcrun", "simctl", "help"]).returncode == 0
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return False

    def health_check(self) -> HealthCheckResult:
        if self._udid is None:
            return HealthCheckResult.failed("simulator not connected")
        try:
            devices = self._list_tvos_devices()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(str(exc))
        for device in devices:
            if device.get("udid") == self._udid:
                if device.get("state") == "Booted":
                    return HealthCheckResult.ok("simulator booted", udid=self._udid)
                return HealthCheckResult.failed(f"simulator state is {device.get('state')}")
        return HealthCheckResult.failed("simulator no longer listed")

    # -- application lifecycle --------------------------------------------------------

    def start_application(self) -> None:
        udid = self._require_udid()
        self._logs.clear()
        self._simctl("launch", udid, self._bundle_id)
        self._app_running = True

    def stop_application(self) -> None:
        udid = self._require_udid()
        self._simctl("terminate", udid, self._bundle_id, check=False)
        self._app_running = False

    def reset_application(self) -> None:
        udid = self._require_udid()
        self.stop_application()
        if self._app_path is not None:
            self._simctl("uninstall", udid, self._bundle_id, check=False)
            self._simctl("install", udid, str(self._app_path))
        self.start_application()

    def is_application_running(self) -> bool:
        return self._udid is not None and self._app_running

    # -- observation --------------------------------------------------------------------

    def screenshot(self) -> Image:
        udid = self._require_udid()
        result = self._simctl("io", udid, "screenshot", "--type", "png", "-")
        try:
            with PILImage.open(io.BytesIO(result.stdout)) as img:
                rgb = img.convert("RGB")
        except Exception as exc:  # noqa: BLE001 - any decode failure
            raise ScreenshotError(
                f"Simulator screenshot could not be decoded: {exc}",
                remediation="Check the simulator is booted and showing a screen.",
            ) from exc
        self._screen_size = rgb.size
        return rgb

    def get_screen_info(self) -> ScreenInfo:
        if self._screen_size is None:
            self.screenshot()
        assert self._screen_size is not None
        return ScreenInfo(width=self._screen_size[0], height=self._screen_size[1])

    def get_logs(self, lines: int = 200) -> str:
        entries = list(self._logs)[-lines:] if lines > 0 else []
        return "\n".join(entries)

    # -- input ----------------------------------------------------------------------------

    def press_key(self, key: str) -> None:
        self._require_udid()
        name = key.removeprefix("KEYCODE_")
        upper = name.upper()
        if len(name) == 1:
            action = f'keystroke "{name}"'
        elif upper in _KEY_CODES:
            action = f"key code {_KEY_CODES[upper]}"
        elif upper in _KEYSTROKES:
            action = _KEYSTROKES[upper]
        else:
            raise DeviceCapabilityError(
                f"tvOS Simulator device {self.name!r} cannot send key {key!r}.",
                remediation="Use DPAD_*, ENTER, BACK/MENU, HOME, MEDIA_PLAY_PAUSE or a single "
                "character.",
            )
        argv = [
            "osascript",
            "-e",
            'tell application "Simulator" to activate',
            "-e",
            f'tell application "System Events" to {action}',
        ]
        result = self._command(argv, check=False)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            if "assistive access" in stderr or "not allowed" in stderr:
                raise DeviceConnectionError(
                    "osascript was denied Accessibility access to the Simulator.",
                    remediation="System Settings > Privacy & Security > Accessibility: enable "
                    "your terminal application, then re-run.",
                )
            raise DeviceConnectionError(
                f"osascript failed ({result.returncode}): {stderr.strip()}",
                remediation="Check the Simulator app is running and frontmost.",
            )
```

Add `from argus.exceptions import ... DeviceCapabilityError ...` to the import line (the module needs `ConfigurationError, DeviceCapabilityError, DeviceConnectionError, ScreenshotError`).

In `src/argus/adapters/registry.py` add `from argus.adapters.tvos_sim import TvosSimAdapter` (after `roku`) and `registry.register("tvos_sim", TvosSimAdapter.from_config)` after the `roku` line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_tvos_sim_adapter.py -v`
Expected: all pass. Note `test_missing_xcrun`: `is_available()` must consult the injected runner when `xcrun` isn't on PATH (the implementation above does), so it returns `False` on a machine without Xcode and on one with Xcode alike when the runner raises `FileNotFoundError`.

Run: `.venv/bin/python -m pytest -q 2>&1 | grep -c "^FAILED"` → `12`. `.venv/bin/ruff check src/argus/adapters/tvos_sim.py src/argus/adapters/registry.py tests/unit/test_tvos_sim_adapter.py` → clean. `.venv/bin/mypy src` → only the 2 baseline errors.

- [ ] **Step 5: Write the integration test**

Create `tests/integration/test_tvos_sim_adapter_e2e.py`:

```python
"""TvosSimAdapter against a booted tvOS Simulator. Skipped unless configured."""

from __future__ import annotations

import os
import shutil

import pytest

from argus.adapters.tvos_sim import TvosSimAdapter
from argus.exceptions import DeviceConnectionError

pytestmark = pytest.mark.integration

BUNDLE_ID = os.environ.get("ARGUS_TVOS_SIM_BUNDLE_ID")


@pytest.fixture
def sim():
    if not BUNDLE_ID:
        pytest.skip("ARGUS_TVOS_SIM_BUNDLE_ID not set")
    if shutil.which("xcrun") is None:
        pytest.skip("xcrun not available")
    device = TvosSimAdapter("sim", bundle_id=BUNDLE_ID, app_path=os.environ.get("ARGUS_TVOS_SIM_APP"))
    try:
        device.connect()
    except DeviceConnectionError as exc:
        device.disconnect()
        pytest.skip(f"tvOS simulator unavailable: {exc}")
    yield device
    device.disconnect()


def test_launch_screenshot_and_keys(sim: TvosSimAdapter):
    sim.start_application()
    assert sim.is_application_running()
    img = sim.screenshot()
    assert img.size == sim.get_screen_info().size
    sim.press_key("DPAD_RIGHT")
    sim.press_key("MENU")
    assert sim.health_check().healthy
```

Run: `.venv/bin/python -m pytest tests/integration/test_tvos_sim_adapter_e2e.py -m integration -v`
Expected: 1 skipped without `ARGUS_TVOS_SIM_BUNDLE_ID`; 1 passed with a booted tvOS simulator and the app installed (or `ARGUS_TVOS_SIM_APP` pointing at its `.app`).

- [ ] **Step 6: Commit**

```bash
git add src/argus/adapters/tvos_sim.py src/argus/adapters/registry.py tests/unit/test_tvos_sim_adapter.py tests/integration/test_tvos_sim_adapter_e2e.py
git commit -m "Add tvOS Simulator device adapter driven by simctl and osascript."
```

---

### Task 4: `appletv` adapter (pyatv)

**Files:**
- Create: `src/argus/adapters/appletv.py`
- Modify: `src/argus/adapters/registry.py`, `pyproject.toml`
- Test: `tests/unit/test_appletv_adapter.py` (new), `tests/integration/test_appletv_adapter_e2e.py` (new)

**Interfaces:**
- Consumes: `PlaybackState`, `DeviceCapabilities.supports_playback_state` (Task 1).
- Produces: `AppleTvAdapter(name, *, app_id, host=None, identifier=None, credentials=None, timeout=10.0, atv_factory=None)` where `atv_factory: Callable[[], Awaitable[Any]]` returns a connected pyatv-like object with `remote_control`, `apps`, `metadata`, `power`, `close()`; `from_config`; device type `"appletv"`; `platform == "appletv"`; `get_playback_state() -> PlaybackState`.

- [ ] **Step 1: Add the optional dependency**

In `pyproject.toml`: under `[project.optional-dependencies]` add `appletv = ["pyatv>=0.14"]`; change `all` to `all = ["argus[yocto,ocr,browser,appletv]"]`; add `"roku", "tvos", "apple-tv", "pyatv"` to `keywords`; change the mypy override to `module = ["pytesseract", "cv2", "playwright", "playwright.*", "pyatv", "pyatv.*"]`.

Run: `uv pip install --python .venv/bin/python -e ".[dev]"` (re-resolve metadata; do **not** install the `appletv` extra in this task).

- [ ] **Step 2: Write the failing unit tests**

Create `tests/unit/test_appletv_adapter.py`:

```python
"""AppleTvAdapter unit tests with a fake pyatv interface (pyatv not required)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from argus.adapters.appletv import AppleTvAdapter
from argus.adapters.registry import DeviceRegistry
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError

_REMOTE_METHODS = {
    "up", "down", "left", "right", "select", "menu", "home", "play", "pause", "play_pause",
    "stop", "next", "previous", "volume_up", "volume_down",
}


class FakeRemote:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def __getattr__(self, name: str):
        if name not in _REMOTE_METHODS:
            raise AttributeError(name)

        async def press() -> None:
            self._calls.append(name)

        return press


class FakeApps:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def launch_app(self, bundle_id: str) -> None:
        self._calls.append(f"launch:{bundle_id}")


class FakeMetadata:
    def __init__(self) -> None:
        self.app = SimpleNamespace(identifier="com.example.tv", name="Example")
        self.now = SimpleNamespace(
            device_state=SimpleNamespace(name="Playing"),
            title="Big Buck Bunny",
            position=12,
            total_time=600,
        )

    async def playing(self):
        return self.now


class FakeAtv:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.remote_control = FakeRemote(self.calls)
        self.apps = FakeApps(self.calls)
        self.metadata = FakeMetadata()
        self.power = SimpleNamespace(power_state=SimpleNamespace(name="On"))
        self.closed = False

    def close(self) -> set:
        self.closed = True
        return set()


@pytest.fixture
def atv() -> FakeAtv:
    return FakeAtv()


@pytest.fixture
def adapter(atv: FakeAtv) -> AppleTvAdapter:
    async def factory() -> FakeAtv:
        await asyncio.sleep(0)
        return atv

    device = AppleTvAdapter("atv", app_id="com.example.tv", host="10.0.0.9", atv_factory=factory)
    yield device
    device.disconnect()


class TestIdentity:
    def test_capabilities(self, adapter):
        caps = adapter.capabilities
        assert caps.supports_keyboard and caps.supports_app_lifecycle
        assert caps.supports_playback_state
        assert not caps.supports_screenshot and not caps.supports_logs
        assert adapter.platform == "appletv"

    def test_unsupported_operations(self, adapter):
        adapter.connect()
        with pytest.raises(DeviceCapabilityError):
            adapter.screenshot()
        with pytest.raises(DeviceCapabilityError):
            adapter.get_logs()
        with pytest.raises(DeviceCapabilityError):
            adapter.tap(1, 1)


class TestConnection:
    def test_connect_disconnect_lifecycle(self, adapter, atv):
        adapter.connect()
        assert adapter.health_check().healthy
        adapter.connect()  # idempotent
        adapter.disconnect()
        assert atv.closed
        assert not adapter.health_check().healthy
        adapter.disconnect()  # idempotent

    def test_operations_before_connect_raise(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter.press_key("ENTER")

    def test_factory_failure_wrapped(self):
        async def failing():
            raise RuntimeError("pairing required")

        device = AppleTvAdapter("atv", app_id="x", host="10.0.0.9", atv_factory=failing)
        with pytest.raises(DeviceConnectionError, match="pairing required"):
            device.connect()
        device.disconnect()

    def test_call_timeout(self, atv):
        async def factory():
            return atv

        async def slow_launch(bundle_id: str) -> None:
            await asyncio.sleep(5)

        atv.apps.launch_app = slow_launch  # type: ignore[method-assign]
        device = AppleTvAdapter(
            "atv", app_id="com.example.tv", host="10.0.0.9", timeout=0.2, atv_factory=factory
        )
        device.connect()
        try:
            with pytest.raises(DeviceConnectionError, match="timed out"):
                device.start_application()
        finally:
            device.disconnect()


class TestLifecycle:
    def test_start_stop_running(self, adapter, atv):
        adapter.connect()
        adapter.start_application()
        assert atv.calls == ["launch:com.example.tv"]
        assert adapter.is_application_running()
        atv.metadata.app = SimpleNamespace(identifier="com.apple.TVHome", name="Home")
        assert not adapter.is_application_running()
        adapter.stop_application()
        assert atv.calls[-1] == "home"
        adapter.reset_application()
        assert atv.calls[-2:] == ["home", "launch:com.example.tv"]


class TestPlayback:
    def test_playback_state_mapping(self, adapter, atv):
        adapter.connect()
        state = adapter.get_playback_state()
        assert state.state == "playing"
        assert state.title == "Big Buck Bunny"
        assert state.app_id == "com.example.tv"
        assert state.position == 12.0 and state.duration == 600.0

    def test_playback_state_idle(self, adapter, atv):
        atv.metadata.now = SimpleNamespace(
            device_state=SimpleNamespace(name="Idle"), title=None, position=None, total_time=None
        )
        atv.metadata.app = None
        adapter.connect()
        state = adapter.get_playback_state()
        assert state.state == "idle" and state.app_id is None


class TestInput:
    @pytest.mark.parametrize(
        ("key", "method"),
        [
            ("KEYCODE_DPAD_LEFT", "left"),
            ("enter", "select"),
            ("BACK", "menu"),
            ("HOME", "home"),
            ("MEDIA_PLAY_PAUSE", "play_pause"),
            ("MEDIA_NEXT", "next"),
            ("VOLUME_UP", "volume_up"),
            ("play_pause", "play_pause"),
        ],
    )
    def test_press_key_mapping(self, adapter, atv, key, method):
        adapter.connect()
        adapter.press_key(key)
        assert atv.calls[-1] == method

    def test_unknown_key(self, adapter):
        adapter.connect()
        with pytest.raises(DeviceCapabilityError, match="F13"):
            adapter.press_key("F13")


class TestConfig:
    def test_from_config(self):
        config = DeviceConfig.model_validate(
            {
                "type": "appletv",
                "host": "10.0.0.9",
                "app_id": "com.example.tv",
                "credentials": {"companion": "c1", "airplay": "a1"},
                "timeout": 4,
            }
        )
        adapter = AppleTvAdapter.from_config("atv", config)
        assert adapter._host == "10.0.0.9"
        assert adapter._app_id == "com.example.tv"
        assert adapter._credentials == {"companion": "c1", "airplay": "a1"}
        assert adapter._timeout == 4.0

    def test_from_config_requires_host_or_identifier(self):
        with pytest.raises(ConfigurationError, match="host"):
            AppleTvAdapter.from_config(
                "atv", DeviceConfig.model_validate({"type": "appletv", "app_id": "x"})
            )

    def test_from_config_requires_app_id(self):
        with pytest.raises(ConfigurationError, match="app_id"):
            AppleTvAdapter.from_config(
                "atv", DeviceConfig.model_validate({"type": "appletv", "host": "10.0.0.9"})
            )

    def test_registered_as_appletv(self):
        registry = DeviceRegistry()
        assert "appletv" in registry.types()
        device = registry.create(
            "atv",
            DeviceConfig.model_validate({"type": "appletv", "host": "10.0.0.9", "app_id": "x"}),
        )
        assert isinstance(device, AppleTvAdapter)

    def test_missing_pyatv_gives_remediation(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("pyatv"):
                raise ImportError("no pyatv")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        device = AppleTvAdapter("atv", app_id="x", host="10.0.0.9")
        assert device.is_available() is False
        with pytest.raises(DeviceConnectionError, match=r'pip install "argus\[appletv\]"'):
            device.connect()
        device.disconnect()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_appletv_adapter.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'argus.adapters.appletv'`.

- [ ] **Step 4: Implement the adapter**

Create `src/argus/adapters/appletv.py`:

```python
"""Physical Apple TV adapter (pyatv).

Remote control, app launching, and now-playing metadata over pyatv's
Companion/MRP protocols. pyatv is asyncio-based; the adapter runs a private
event loop on a daemon thread and exposes a synchronous ``Device`` surface.
No screenshots or logs are possible on a physical Apple TV — use the
``now_playing`` condition (``get_playback_state``) for verification.
Optional dependency: ``pip install "argus[appletv]"``; pair once with
``atvremote wizard`` to obtain credentials.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from argus.adapters.base import Device, DeviceCapabilities
from argus.config.models import DeviceConfig
from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, PlaybackState

_DEFAULT_TIMEOUT = 10.0
_SCAN_TIMEOUT = 5

# Android-style key names -> pyatv RemoteControl method names. pyatv method
# names themselves (lower-case) are accepted too.
_KEY_MAP = {
    "DPAD_UP": "up",
    "DPAD_DOWN": "down",
    "DPAD_LEFT": "left",
    "DPAD_RIGHT": "right",
    "UP": "up",
    "DOWN": "down",
    "LEFT": "left",
    "RIGHT": "right",
    "ENTER": "select",
    "DPAD_CENTER": "select",
    "SELECT": "select",
    "BACK": "menu",
    "MENU": "menu",
    "HOME": "home",
    "MEDIA_PLAY_PAUSE": "play_pause",
    "MEDIA_PLAY": "play",
    "MEDIA_PAUSE": "pause",
    "MEDIA_STOP": "stop",
    "MEDIA_NEXT": "next",
    "MEDIA_PREVIOUS": "previous",
    "VOLUME_UP": "volume_up",
    "VOLUME_DOWN": "volume_down",
}
_REMOTE_METHODS = frozenset(_KEY_MAP.values())

_STATE_MAP = {
    "idle": "idle",
    "loading": "loading",
    "stopped": "stopped",
    "paused": "paused",
    "playing": "playing",
    "seeking": "seeking",
}

AtvFactory = Callable[[], Awaitable[Any]]


class AppleTvAdapter(Device):
    """Controls a physical Apple TV through pyatv."""

    def __init__(
        self,
        name: str,
        *,
        app_id: str,
        host: str | None = None,
        identifier: str | None = None,
        credentials: dict[str, str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        atv_factory: AtvFactory | None = None,
    ) -> None:
        super().__init__(name)
        if not host and not identifier:
            raise ConfigurationError(
                f"Apple TV device {name!r} requires 'host' or 'identifier'.",
                remediation="Set devices.<name>.host to the Apple TV's IP address.",
            )
        self._app_id = app_id
        self._host = host
        self._identifier = identifier
        self._credentials = dict(credentials or {})
        self._timeout = float(timeout)
        self._atv_factory = atv_factory
        self._atv: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._log = get_logger("argus.appletv", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> AppleTvAdapter:
        options: dict[str, Any] = config.options
        app_id = options.get("app_id")
        if not app_id:
            raise ConfigurationError(
                f"Apple TV device {name!r} requires an 'app_id' option.",
                remediation="Set devices.<name>.app_id to the app's bundle identifier.",
            )
        credentials = options.get("credentials") or {}
        return cls(
            name,
            app_id=str(app_id),
            host=options.get("host"),
            identifier=options.get("identifier"),
            credentials={str(k).lower(): str(v) for k, v in dict(credentials).items()},
            timeout=float(options.get("timeout", _DEFAULT_TIMEOUT)),
        )

    # -- identity -----------------------------------------------------------------

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_keyboard=True,
            supports_app_lifecycle=True,
            supports_instrumentation=True,
            supports_playback_state=True,
        )

    @property
    def platform(self) -> str:
        return "appletv"

    # -- event loop plumbing -----------------------------------------------------------

    def _start_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True, name=f"appletv-{self.name}")
        thread.start()
        self._loop, self._thread = loop, thread
        return loop

    def _stop_loop(self) -> None:
        loop, self._loop = self._loop, None
        thread, self._thread = self._thread, None
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2.0)
        with contextlib.suppress(Exception):
            loop.close()

    def _run(self, coro: Awaitable[Any]) -> Any:
        if self._loop is None:
            raise DeviceConnectionError(
                f"Apple TV device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]
        try:
            return future.result(self._timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise DeviceConnectionError(
                f"Apple TV {self.name!r}: operation timed out after {self._timeout}s.",
                remediation="Check the Apple TV is awake and reachable; raise 'timeout' if slow.",
            ) from exc

    def _require_atv(self) -> Any:
        if self._atv is None:
            raise DeviceConnectionError(
                f"Apple TV device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        return self._atv

    async def _connect_pyatv(self) -> Any:
        try:
            import pyatv
            from pyatv.const import Protocol
        except ImportError as exc:
            raise DeviceConnectionError(
                "pyatv is not installed (required for appletv devices).",
                remediation='Install Apple TV support: pip install "argus[appletv]"',
            ) from exc
        loop = asyncio.get_running_loop()
        hosts = [self._host] if self._host else None
        identifier = self._identifier
        found = await pyatv.scan(loop, hosts=hosts, identifier=identifier, timeout=_SCAN_TIMEOUT)
        if not found:
            raise DeviceConnectionError(
                f"Apple TV {self._host or self._identifier!r} not found on the network.",
                remediation="Check the address and that the Apple TV is awake; "
                "run 'atvremote scan'.",
            )
        conf = found[0]
        protocols = {
            "companion": Protocol.Companion,
            "airplay": Protocol.AirPlay,
            "mrp": Protocol.MRP,
            "raop": Protocol.RAOP,
            "dmap": Protocol.DMAP,
        }
        for key, value in self._credentials.items():
            protocol = protocols.get(key)
            if protocol is not None:
                conf.set_credentials(protocol, value)
        try:
            return await pyatv.connect(conf, loop)
        except Exception as exc:  # noqa: BLE001 - pyatv raises many exception types
            raise DeviceConnectionError(
                f"Unable to connect to Apple TV {self._host or self._identifier!r}: {exc}",
                remediation="Pair with 'atvremote --address <host> wizard' and put the "
                "credentials under devices.<name>.credentials.",
            ) from exc

    # -- connection -----------------------------------------------------------------

    def connect(self) -> None:
        if self._atv is not None:
            return
        self._start_loop()
        factory = self._atv_factory or self._connect_pyatv
        try:
            self._atv = self._run(factory())
        except DeviceConnectionError:
            self._stop_loop()
            raise
        except Exception as exc:  # noqa: BLE001 - factory/pyatv failure
            self._stop_loop()
            raise DeviceConnectionError(
                f"Unable to connect to Apple TV {self.name!r}: {exc}",
                remediation="Check host/credentials; pair with 'atvremote wizard'.",
            ) from exc
        self._log.info("Connected to Apple TV %s", self._host or self._identifier)

    def disconnect(self) -> None:
        atv, self._atv = self._atv, None
        if atv is not None and self._loop is not None:
            with contextlib.suppress(Exception):
                self._loop.call_soon_threadsafe(atv.close)
        self._stop_loop()

    def is_available(self) -> bool:
        if self._atv_factory is not None:
            return True
        try:
            import pyatv  # noqa: F401
        except ImportError:
            return False
        return True

    def health_check(self) -> HealthCheckResult:
        if self._atv is None:
            if not self.is_available():
                return HealthCheckResult.failed("pyatv not installed")
            return HealthCheckResult.failed("apple tv not connected")
        power = getattr(self._atv.power, "power_state", None)
        state = getattr(power, "name", str(power))
        if state and state.lower() == "off":
            return HealthCheckResult.failed("apple tv is powered off", power=state)
        return HealthCheckResult.ok("apple tv connected", power=state)

    # -- application lifecycle --------------------------------------------------------

    def start_application(self) -> None:
        atv = self._require_atv()
        self._run(atv.apps.launch_app(self._app_id))

    def stop_application(self) -> None:
        atv = self._require_atv()
        self._run(atv.remote_control.home())

    def is_application_running(self) -> bool:
        atv = self._require_atv()
        app = atv.metadata.app
        return app is not None and getattr(app, "identifier", None) == self._app_id

    # -- observation --------------------------------------------------------------------

    def get_playback_state(self) -> PlaybackState:
        atv = self._require_atv()
        playing = self._run(atv.metadata.playing())
        raw_state = getattr(playing.device_state, "name", str(playing.device_state))
        app = atv.metadata.app
        position = getattr(playing, "position", None)
        duration = getattr(playing, "total_time", None)
        return PlaybackState(
            state=_STATE_MAP.get(str(raw_state).lower(), "idle"),  # type: ignore[arg-type]
            title=getattr(playing, "title", None),
            app_id=getattr(app, "identifier", None) if app is not None else None,
            position=float(position) if position is not None else None,
            duration=float(duration) if duration is not None else None,
        )

    # -- input ----------------------------------------------------------------------------

    def press_key(self, key: str) -> None:
        atv = self._require_atv()
        name = key.removeprefix("KEYCODE_")
        method = _KEY_MAP.get(name.upper(), name.lower())
        if method not in _REMOTE_METHODS:
            raise DeviceCapabilityError(
                f"Apple TV device {self.name!r} cannot send key {key!r}.",
                remediation="Use DPAD_*, ENTER, BACK/MENU, HOME, MEDIA_*, VOLUME_* or a pyatv "
                "remote method name.",
            )
        self._run(getattr(atv.remote_control, method)())
```

In `src/argus/adapters/registry.py` add `from argus.adapters.appletv import AppleTvAdapter` (first, alphabetical) and `registry.register("appletv", AppleTvAdapter.from_config)` before the `android` line.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_appletv_adapter.py -v`
Expected: all pass. If `test_call_timeout` hangs, check `_run` cancels the future and `_stop_loop` stops the loop with `call_soon_threadsafe(loop.stop)` — the sleeping coroutine is cancelled when the loop closes.

Run: `.venv/bin/python -m pytest -q 2>&1 | grep -c "^FAILED"` → `12`. `.venv/bin/ruff check src/argus/adapters/appletv.py src/argus/adapters/registry.py tests/unit/test_appletv_adapter.py` → clean. `.venv/bin/mypy src` → only the 2 baseline errors (if mypy complains about `type: ignore` comments being unused, remove the specific comment it names — `warn_unused_ignores` is on).

- [ ] **Step 6: Write the integration test**

Create `tests/integration/test_appletv_adapter_e2e.py`:

```python
"""AppleTvAdapter against a real Apple TV. Skipped unless ARGUS_APPLETV_HOST is set."""

from __future__ import annotations

import json
import os
import time

import pytest

from argus.adapters.appletv import AppleTvAdapter

pytestmark = pytest.mark.integration

pytest.importorskip("pyatv")

HOST = os.environ.get("ARGUS_APPLETV_HOST")
APP_ID = os.environ.get("ARGUS_APPLETV_APP_ID", "com.apple.TVWatchList")
CREDENTIALS = os.environ.get("ARGUS_APPLETV_CREDENTIALS")


@pytest.fixture
def atv():
    if not HOST or not CREDENTIALS:
        pytest.skip("ARGUS_APPLETV_HOST / ARGUS_APPLETV_CREDENTIALS not set")
    device = AppleTvAdapter(
        "atv", app_id=APP_ID, host=HOST, credentials=json.loads(CREDENTIALS)
    )
    device.connect()
    yield device
    device.disconnect()


def test_launch_keys_and_playback_state(atv: AppleTvAdapter):
    assert atv.health_check().healthy
    atv.start_application()
    time.sleep(3)
    assert atv.is_application_running()
    atv.press_key("DPAD_DOWN")
    atv.press_key("MENU")
    state = atv.get_playback_state()
    assert state.state in {"playing", "paused", "stopped", "idle", "loading", "seeking"}
    atv.stop_application()
```

Run: `.venv/bin/python -m pytest tests/integration/test_appletv_adapter_e2e.py -m integration -v`
Expected: 1 skipped ("pyatv" not installed, or env vars unset). With `uv pip install --python .venv/bin/python -e ".[dev,appletv]"`, `ARGUS_APPLETV_HOST`, and `ARGUS_APPLETV_CREDENTIALS='{"companion": "...", "airplay": "..."}'` from `atvremote --address <host> wizard`: 1 passed. Afterwards run `uv pip uninstall --python .venv/bin/python pyatv` and `.venv/bin/python -m pytest -q 2>&1 | grep -c "^FAILED"` → `12` to prove the suite is still green without pyatv.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/argus/adapters/appletv.py src/argus/adapters/registry.py tests/unit/test_appletv_adapter.py tests/integration/test_appletv_adapter_e2e.py
git commit -m "Add pyatv-backed Apple TV device adapter with playback state."
```

---

### Task 5: Documentation

**Files:**
- Create: `docs/roku.md`, `docs/tvos.md`
- Modify: `docs/adapters.md` (built-in adapters table), `docs/getting-started.md:48`, `docs/configuration.md:47`, `README.md` (line 30 sentence and docs table after the Web browser row), `CHANGELOG.md`

**Interfaces:**
- Consumes: option names from Tasks 2–4 and the `now_playing` params from Task 1.

- [ ] **Step 1: Write `docs/roku.md`**

````markdown
# Roku

The Roku adapter drives a Roku in **developer mode** running a **sideloaded**
channel. Control uses Roku's External Control Protocol (ECP); screenshots and
sideloading use the developer web installer; the BrightScript debug console is
captured as the device log.

## Prerequisites

1. Enable developer mode on the Roku (Home ×3, Up ×2, Right, Left, Right, Left,
   Right) and note the password you set.
2. Package your channel as a `.zip` (or sideload it yourself through
   `http://<roku-ip>/`).

No extra Python dependency is needed.

## Configuration

```yaml
devices:
  tv:
    type: roku
    platform: roku                  # label used by tests' `platforms:` filter
    host: 192.168.1.42              # required
    dev_password: rokudev           # needed for screenshots and sideloading
    channel_zip: build/channel.zip  # optional — sideloaded on connect
    ecp_port: 8060                  # default
    debug_port: 8085                # BrightScript console, default
    timeout: 10                     # seconds per request
```

Tests filter with `platforms: [roku]`.

## What the adapter does

| Operation | Implementation |
| --- | --- |
| Connect | `GET /query/device-info`, optional sideload (`POST /plugin_install`), start the console reader |
| Screenshot | `POST /plugin_inspect` (Screenshot) then `GET /pkgs/dev.jpg` — sideloaded channel only |
| Start app | `POST /launch/dev` (clears captured logs) |
| Stop app | `POST /keypress/Home` |
| Reset app | stop + start |
| Key | `POST /keypress/<Key>`; Android names map (`DPAD_LEFT` → `Left`, `ENTER` → `Select`, `BACK` → `Back`, `MEDIA_PLAY_PAUSE` → `Play`, `MEDIA_FAST_FORWARD` → `Fwd`); single characters send `Lit_<char>`; ECP names such as `InstantReplay` pass through |
| Logs | lines from the debug console on port 8085 (reconnects automatically) |
| Screen size | from `ui-resolution` (`720p`, `1080p`, `2160p`) |

Without `dev_password` the device reports `supports_screenshot: false` and visual
conditions raise a capability error — use `log_contains` or backend/instrumentation
conditions instead. `tap`/`swipe` are not supported (no pointer on Roku).

## Asserting on the debug console

```yaml
- action: wait_until
  timeout: 10s
  condition:
    type: log_contains
    pattern: "Player: state=(PLAYING|BUFFERING)"
```

## Limitations

- Store channels cannot be screenshotted or sideloaded; only the `dev` channel.
- Screenshots are JPEG/PNG captures of the channel's own render, not HDMI output.
- One Roku per device entry; the debug console allows a single client at a time.
````

- [ ] **Step 2: Write `docs/tvos.md`**

````markdown
# Apple TV

Argus supports two Apple TV setups with different capabilities:

| | `tvos_sim` (Simulator) | `appletv` (physical, pyatv) |
| --- | --- | --- |
| Screenshots | yes (`simctl io screenshot`) | **no** |
| Logs | yes (`log stream` for the app process) | **no** |
| Remote keys | via `osascript` keyboard shortcuts | via pyatv |
| App launch / stop | `simctl launch` / `terminate` | `launch_app` / Home |
| Playback state (`now_playing`) | no | **yes** |

## tvOS Simulator (`tvos_sim`)

### Prerequisites

- macOS with Xcode and a tvOS simulator (`xcrun simctl list devices`).
- Accessibility permission for your terminal (System Settings → Privacy &
  Security → Accessibility) so `osascript` can send keys to the Simulator.

### Configuration

```yaml
devices:
  sim:
    type: tvos_sim
    platform: tvos_sim
    bundle_id: com.example.tvapp     # required
    udid: booted                      # or a simulator UDID
    app_path: build/Example.app       # optional — installed on connect and reset
    boot: true                        # boot the simulator if needed
    process_name: Example             # log stream predicate (default: last part of bundle_id)
    timeout: 30
```

### Keys

`DPAD_UP/DOWN/LEFT/RIGHT` → arrow keys, `ENTER`/`DPAD_CENTER` → Return (Select),
`BACK`/`MENU` → Escape (Menu), `MEDIA_PLAY_PAUSE` → Space, `HOME` → ⌘⇧H, single
characters → typed. Anything else raises a capability error. The Simulator window
is brought to the front before each key.

### Limitations

- No touch/trackpad gestures (`tap`/`swipe` unsupported).
- Keys go to whichever Simulator window is frontmost; run one simulator at a time.
- Argus never shuts the simulator down on disconnect.

## Physical Apple TV (`appletv`)

### Prerequisites

```bash
pip install "argus[appletv]"
atvremote --address 192.168.1.50 wizard   # pair; prints Companion/AirPlay credentials
```

### Configuration

```yaml
devices:
  living_room:
    type: appletv
    platform: appletv
    host: 192.168.1.50                # or identifier: <pyatv id>
    app_id: com.example.tvapp         # required — launched by start_application
    credentials:
      companion: "..."                # from atvremote wizard
      airplay: "..."
    timeout: 10
```

### Verification without screenshots

A physical Apple TV exposes no screenshot or log API, so verify through the
`now_playing` condition (state, title, app id, position advancing) and through
backend/instrumentation conditions:

```yaml
- action: press_key
  key: MEDIA_PLAY_PAUSE
- action: wait_until
  timeout: 15s
  condition:
    type: now_playing
    state: playing
    title: "Big Buck Bunny"
    position_advancing: true
```

Image/text conditions raise a capability error on this device.

### Keys

`DPAD_*`, `ENTER`/`DPAD_CENTER` (select), `BACK`/`MENU` (menu), `HOME`,
`MEDIA_PLAY_PAUSE`, `MEDIA_PLAY`, `MEDIA_PAUSE`, `MEDIA_STOP`, `MEDIA_NEXT`,
`MEDIA_PREVIOUS`, `VOLUME_UP`, `VOLUME_DOWN` — or any pyatv remote method name
(`play_pause`, `top_menu`, ...).

### Limitations

- "Stop application" presses Home; tvOS has no kill API.
- Playback metadata depends on the app publishing now-playing info.
````

- [ ] **Step 3: Cross-link**

`docs/adapters.md` built-in adapters table — add after the `browser` row:

```markdown
| `roku` | ECP + dev installer | developer-mode Roku with a sideloaded channel, see [roku.md](roku.md) |
| `tvos_sim` | `xcrun simctl` + `osascript` | tvOS app in the Xcode Simulator, see [tvos.md](tvos.md) |
| `appletv` | pyatv | physical Apple TV (remote + playback state, no screenshots), see [tvos.md](tvos.md) |
```

`docs/getting-started.md` line 48 → `- your devices — see [android.md](android.md), [yocto.md](yocto.md), [browser.md](browser.md), [roku.md](roku.md), and [tvos.md](tvos.md)`

`docs/configuration.md` line 47 comment → `# android | yocto | browser | roku | tvos_sim | appletv | fake | plugin-provided`

`README.md` line 30: extend the support sentence so it reads `Supported today: **backend REST APIs**, **Android** (ADB), **web browsers** (Playwright), **Roku** (developer mode), **Apple TV** (tvOS Simulator and pyatv), and **Yocto /` — keep the rest of the sentence unchanged and re-wrap the paragraph at the file's usual width.

`README.md` docs table — add after the Web browser row:

```markdown
| Roku setup | [docs/roku.md](docs/roku.md) |
| Apple TV setup | [docs/tvos.md](docs/tvos.md) |
```

`CHANGELOG.md` `### Added` — append after the `now_playing` bullet:

```markdown
- `roku` device adapter: ECP remote/launch control, developer-installer
  screenshots and sideloading, BrightScript console captured as device logs.
- `tvos_sim` device adapter: tvOS Simulator via `xcrun simctl` (screenshots,
  launch/terminate, `log stream`) with remote keys sent through `osascript`.
- `appletv` device adapter (pyatv, optional `argus[appletv]` extra): remote
  keys, app launch, and now-playing state for `now_playing` assertions.
```

- [ ] **Step 4: Verify links and run the suite**

Run: `grep -rn "roku.md\|tvos.md" README.md docs/*.md` → hits in README, adapters.md, getting-started.md.
Run: `.venv/bin/python -m pytest -q 2>&1 | grep -c "^FAILED"` → `12`; `.venv/bin/ruff check src tests` → only the 3 baseline errors.

- [ ] **Step 5: Commit**

```bash
git add docs/roku.md docs/tvos.md docs/adapters.md docs/getting-started.md docs/configuration.md README.md CHANGELOG.md
git commit -m "Document the Roku and Apple TV adapters."
```

---

## Self-review

- **Spec coverage:** §Shared contracts → Global Constraints + each adapter; §1 Roku → Task 2 (all config options incl. the test-only `installer_port`, ECP/installer/console behaviours, capabilities, tests); §2 tvOS Simulator → Task 3 (all options, `simctl` commands, `osascript` keys with Accessibility remediation, tracked `is_application_running`, tests); §3 Apple TV → Task 4 (loop thread, credentials, keys, `get_playback_state`, extra + mypy override, tests); §4 PlaybackState/`now_playing` → Task 1; §5 docs/packaging → Tasks 4 (pyproject) and 5; error-handling table → each adapter's `DeviceConnectionError`/`DeviceCapabilityError` paths; integration tests env-gated per spec.
- **Deviations recorded:** tvOS Simulator unknown key names raise instead of passing through (Task 3 Interfaces); Roku gains `installer_port` for testability; `now_playing` accepts `interval` (spec's default 1.0s kept).
- **Type consistency:** `PlaybackState` fields (`state,title,app_id,position,duration`) match the adapter mapping in Task 4 and the `details["observed"]`/`details["second"]` dumps in Task 1 tests; `CommandResult(returncode, stdout, stderr)` positional order matches the fake runner; `supports_playback_state` is used identically in Tasks 1 and 4; registry names `roku`/`tvos_sim`/`appletv` match `from_config` tests and docs.
- **Placeholders:** none.
