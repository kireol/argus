# iOS Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Argus tests drive iOS apps on simulators and physical devices — screenshots, app lifecycle, logs, and the full gesture set (tap, swipe, long press, drag, multi-touch, pinch) — through a running WebDriverAgent.

**Architecture:** One new module `src/argus/adapters/ios.py`. A tiny `WdaClient` protocol (`request(method, path, body) -> dict`) with an `urllib`-based production implementation isolates HTTP; `IosAdapter(Device)` maps every `Device` operation to WebDriverAgent endpoints, converts screenshot pixels to WDA points with a scale read once from `/wda/screen`, and emits all touch input as W3C Actions (`POST /session/<id>/actions`) so multi-finger gestures run concurrently. Logs come from an optional `log_command` subprocess pumped into a bounded deque, the same pattern as `tvos_sim`.

**Tech Stack:** Python 3.12, standard library `urllib.request` + `json` + `base64` (no new dependency), Pillow, pytest, `pytest-httpserver` (already a dev dependency) for the HTTP client test.

**Spec:** `docs/superpowers/specs/2026-08-24-ios-and-desktop-adapters-design.md` — section 1 ("iOS adapter"). Read it first; this plan implements that section only. The desktop adapter gets its own plan.

## Global Constraints

- `requires-python = ">=3.12"`; `.venv/bin/ruff check src tests` (line length 100) and `.venv/bin/mypy src` must show **no new** errors on touched files.
- No new runtime dependency: iOS uses only the standard library.
- Coordinates in tests/YAML are **screenshot pixels**; the adapter divides by the WDA scale to get points (spec decision "iOS coordinates").
- Every failure is a remediated exception: transport/HTTP → `DeviceConnectionError`; missing `bundle_id` → `ConfigurationError`; bad PNG → `ScreenshotError`; `get_logs` without `log_command` → `DeviceCapabilityError` via `Device._unsupported`.
- Device type `ios`, platform label `ios`; registry imports alphabetical (android, appletv, browser, esp32, fake, ios, roku, tvos_sim, yocto).
- Log buffer: `collections.deque(maxlen=5000)`, oldest first; `get_logs(lines)` = last `lines` joined by `"\n"`.
- Gesture semantics per `Device` docstrings in `src/argus/adapters/base.py` (drag = press, hold, move; `pinch` is inherited from the base class and must NOT be overridden).
- Every YAML example in docs sets `platform: ios` explicitly.
- Commit messages: short imperative sentence, no prefix, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. `git add` only the files each task lists.
- Baseline on this branch: 12 failing tests (`tests/unit/test_text_verifiers.py`, `tests/unit/test_console_reporter.py`), 1 ruff error (`tests/unit/test_console_reporter.py` import order), 2 mypy errors (`src/argus/ocr/preprocess.py`, `src/argus/verifiers/image.py`). Out of scope; the gate is *no new failures*.
- Run from the repo root: `.venv/bin/python -m pytest ...`, `.venv/bin/ruff check ...`, `.venv/bin/mypy src`.

---

## File map

| Path | Responsibility |
| --- | --- |
| `src/argus/adapters/ios.py` (new) | `WdaClient` protocol, `_HttpWdaClient`, `_LogPump`, `IosAdapter` |
| `src/argus/adapters/registry.py` (modify) | register `ios` |
| `tests/unit/test_ios_adapter.py` (new) | all unit tests, with `FakeWda` and `FakeProcess` |
| `tests/integration/test_ios_adapter_e2e.py` (new) | live smoke test, skipped unless `ARGUS_WDA_URL` + `ARGUS_IOS_BUNDLE_ID` |
| `docs/ios.md` (new) | prerequisites, config, operation table, gestures, troubleshooting |
| `docs/adapters.md`, `docs/configuration.md`, `docs/test-authoring.md`, `README.md`, `CHANGELOG.md` (modify) | references |

---

### Task 1: HTTP client and error mapping

**Files:**
- Create: `src/argus/adapters/ios.py`
- Test: `tests/unit/test_ios_adapter.py`

**Interfaces:**
- Produces: `WdaClient` protocol — `request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]`; `_HttpWdaClient(base_url: str, timeout: float)`; `WdaClientFactory = Callable[[], WdaClient]`; `_wda_error(payload: dict) -> tuple[str, str] | None`.

- [ ] **Step 1: Write the failing tests**

```python
"""IosAdapter (WebDriverAgent) behaviour, verified through the requests it sends."""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import pytest
from PIL import Image

from argus.adapters.ios import _HttpWdaClient
from argus.exceptions import DeviceConnectionError


def _png(size: tuple[int, int] = (4, 6)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (9, 8, 7)).save(buf, format="PNG")
    return buf.getvalue()


class TestHttpClient:
    def test_get_and_post_json(self, httpserver):
        httpserver.expect_request("/status", method="GET").respond_with_json(
            {"value": {"ready": True}}
        )
        httpserver.expect_request(
            "/session", method="POST", json={"capabilities": {}}
        ).respond_with_json({"value": {"sessionId": "abc"}, "sessionId": "abc"})
        client = _HttpWdaClient(httpserver.url_for("/"), timeout=2.0)
        assert client.request("GET", "/status") == {"value": {"ready": True}}
        assert client.request("POST", "/session", {"capabilities": {}})["sessionId"] == "abc"

    def test_wda_error_payload_becomes_connection_error(self, httpserver):
        httpserver.expect_request("/session/x/actions", method="POST").respond_with_json(
            {"value": {"error": "invalid session id", "message": "Session does not exist"}},
            status=404,
        )
        client = _HttpWdaClient(httpserver.url_for("/"), timeout=2.0)
        with pytest.raises(DeviceConnectionError, match="invalid session id") as info:
            client.request("POST", "/session/x/actions", {})
        assert "Session does not exist" in str(info.value)
        assert "reconnect" in (info.value.remediation or "")

    def test_unreachable_server_is_connection_error(self):
        client = _HttpWdaClient("http://127.0.0.1:1", timeout=0.5)
        with pytest.raises(DeviceConnectionError, match="WebDriverAgent") as info:
            client.request("GET", "/status")
        assert "docs/ios.md" in (info.value.remediation or "")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'argus.adapters.ios'`.

- [ ] **Step 3: Write the client**

Create `src/argus/adapters/ios.py`:

```python
"""iOS device adapter (WebDriverAgent over HTTP).

Drives an iOS app on a simulator or a physical device through a running
WebDriverAgent (https://github.com/appium/WebDriverAgent): app lifecycle,
screenshots and W3C-Actions touch input (tap, swipe, long press, drag,
multi-touch, pinch). Logs come from an optional ``log_command`` subprocess
(``xcrun simctl spawn ... log stream`` for simulators, ``idevicesyslog`` for
devices). Only the standard library is used.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import shlex
import subprocess
import threading
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from PIL import Image as PILImage
from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities, Point
from argus.config.models import DeviceConfig
from argus.exceptions import (
    ConfigurationError,
    DeviceConnectionError,
    ScreenshotError,
)
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo

_DEFAULT_URL = "http://127.0.0.1:8100"
_DEFAULT_TIMEOUT = 30.0
_MAX_LOG_LINES = 5000
_WDA_DOCS = "See docs/ios.md for building and running WebDriverAgent."


class WdaClient(Protocol):
    """The one call the adapter needs: a JSON request against WebDriverAgent."""

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


WdaClientFactory = Callable[[], WdaClient]


def _wda_error(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Return (error, message) when ``payload`` is a WebDriver error response."""
    value = payload.get("value")
    if isinstance(value, dict) and value.get("error"):
        return str(value["error"]), str(value.get("message", ""))
    return None


def _raise_for_wda_error(payload: dict[str, Any]) -> None:
    error = _wda_error(payload)
    if error is None:
        return
    name, message = error
    remediation = "Check the WebDriverAgent log for details."
    if "session" in name:
        remediation = "The WDA session is gone; reconnect the device (a new run does this)."
    raise DeviceConnectionError(
        f"WebDriverAgent error {name!r}: {message}".rstrip(": "), remediation=remediation
    )


class _HttpWdaClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self._base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            payload = _decode(raw)
            _raise_for_wda_error(payload)
            raise DeviceConnectionError(
                f"WebDriverAgent {method} {path} returned HTTP {exc.code}.",
                remediation=_WDA_DOCS,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DeviceConnectionError(
                f"Cannot reach WebDriverAgent at {self._base_url}: {exc}",
                remediation="Is WebDriverAgent running (xcodebuild ... test) and the port "
                f"forwarded? {_WDA_DOCS}",
            ) from exc
        payload = _decode(raw)
        _raise_for_wda_error(payload)
        return payload


def _decode(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeviceConnectionError(
            f"WebDriverAgent returned non-JSON data ({len(raw)} bytes).",
            remediation="Check the url points at WebDriverAgent, not another service.",
        ) from exc
    return payload if isinstance(payload, dict) else {"value": payload}
```

(The unused imports — `base64`, `io`, `shlex`, `subprocess`, `threading`, `deque`, `Sequence`, `PILImage`, `Image`, `Device`, `DeviceCapabilities`, `Point`, `DeviceConfig`, `ConfigurationError`, `ScreenshotError`, `get_logger`, `HealthCheckResult`, `ScreenInfo`, `_MAX_LOG_LINES`, `_DEFAULT_URL`, `_DEFAULT_TIMEOUT` — are used by Tasks 2–7. Ruff will flag them until then; that is expected and resolved by Task 2.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/ios.py tests/unit/test_ios_adapter.py
git commit -m "Add WebDriverAgent HTTP client for the iOS adapter

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: IosAdapter identity, configuration, connection

**Files:**
- Modify: `src/argus/adapters/ios.py`
- Test: `tests/unit/test_ios_adapter.py`

**Interfaces:**
- Consumes: `WdaClient`, `WdaClientFactory`, `_HttpWdaClient`.
- Produces: `IosAdapter(name, *, bundle_id, url=_DEFAULT_URL, timeout=_DEFAULT_TIMEOUT, log_command=None, client_factory=None, spawner=None)`; `IosAdapter.from_config`; `_session_path(self, suffix) -> str`; `_require_session(self) -> str`; `_client` attribute; `Spawner = Callable[[list[str]], Any]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ios_adapter.py` (add `from argus.adapters.ios import IosAdapter, _HttpWdaClient` to the import, and `from argus.config.models import DeviceConfig`, `from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError`):

```python
class FakeWda:
    """Records (method, path, body); answers by exact path, then by prefix."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.responses: dict[tuple[str, str], Any] = {
            ("GET", "/status"): {"value": {"ready": True, "state": "success"}},
            ("POST", "/session"): {"sessionId": "S1", "value": {"sessionId": "S1"}},
            ("GET", "/screenshot"): {"value": base64.b64encode(_png((8, 12))).decode()},
            ("GET", "/session/S1/window/size"): {"value": {"width": 4, "height": 6}},
            ("GET", "/session/S1/wda/screen"): {"value": {"scale": 2, "statusBarSize": {}}},
            ("POST", "/session/S1/wda/apps/state"): {"value": 4},
        }
        self.fail_with: DeviceConnectionError | None = None

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, path, body))
        if self.fail_with is not None:
            raise self.fail_with
        return self.responses.get((method, path), {"value": None})

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.calls if method is None or m == method]

    def body(self, method: str, path: str) -> dict[str, Any] | None:
        return next(b for m, p, b in self.calls if (m, p) == (method, path))


@pytest.fixture
def wda() -> FakeWda:
    return FakeWda()


@pytest.fixture
def adapter(wda: FakeWda) -> IosAdapter:
    return IosAdapter("iphone", bundle_id="com.example.app", client_factory=lambda: wda)


class TestIdentity:
    def test_platform_and_capabilities(self, adapter):
        assert adapter.platform == "ios"
        caps = adapter.capabilities
        assert caps.supports_screenshot and caps.supports_app_lifecycle
        assert caps.supports_tap and caps.supports_swipe and caps.supports_keyboard
        assert caps.supports_long_press and caps.supports_drag and caps.supports_multi_touch
        assert caps.supports_logs is False

    def test_logs_capability_follows_log_command(self, wda):
        adapter = IosAdapter(
            "iphone", bundle_id="com.example.app", log_command="idevicesyslog",
            client_factory=lambda: wda,
        )
        assert adapter.capabilities.supports_logs is True


class TestConnection:
    def test_connect_creates_session_for_bundle(self, adapter, wda):
        adapter.connect()
        assert wda.paths() == ["/status", "/session"]
        assert wda.body("POST", "/session") == {
            "capabilities": {"alwaysMatch": {"bundleId": "com.example.app"}}
        }
        assert adapter._session_path("/actions") == "/session/S1/actions"

    def test_disconnect_deletes_session(self, adapter, wda):
        adapter.connect()
        adapter.disconnect()
        assert ("DELETE", "/session/S1", None) in wda.calls
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter._require_session()

    def test_connect_unreachable_raises(self, adapter, wda):
        wda.fail_with = DeviceConnectionError("Cannot reach WebDriverAgent", remediation="x")
        with pytest.raises(DeviceConnectionError, match="Cannot reach"):
            adapter.connect()

    def test_connect_without_session_id_raises(self, adapter, wda):
        wda.responses[("POST", "/session")] = {"value": {}}
        with pytest.raises(DeviceConnectionError, match="session"):
            adapter.connect()

    def test_is_available_and_health_check(self, adapter, wda):
        assert adapter.is_available() is True
        adapter.connect()
        result = adapter.health_check()
        assert result.healthy
        assert result.details["app_running"] is True
        wda.fail_with = DeviceConnectionError("down")
        assert adapter.is_available() is False
        assert adapter.health_check().healthy is False


class TestConfig:
    def test_from_config(self):
        config = DeviceConfig.model_validate(
            {
                "type": "ios",
                "bundle_id": "com.example.app",
                "url": "http://10.0.0.5:8100/",
                "timeout": 5,
                "log_command": "idevicesyslog -u 0001",
            }
        )
        adapter = IosAdapter.from_config("iphone", config)
        assert adapter._bundle_id == "com.example.app"
        assert adapter._url == "http://10.0.0.5:8100/"
        assert adapter._timeout == 5.0
        assert adapter._log_command == "idevicesyslog -u 0001"
        assert isinstance(adapter._client, _HttpWdaClient)

    def test_from_config_requires_bundle_id(self):
        with pytest.raises(ConfigurationError, match="bundle_id"):
            IosAdapter.from_config("iphone", DeviceConfig.model_validate({"type": "ios"}))
```

Check `HealthCheckResult` field names before running: `grep -n "class HealthCheckResult" -A 12 src/argus/models/common.py` — the test uses `.healthy` and `.details`; adjust to the real attribute names if they differ.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider`
Expected: ImportError `cannot import name 'IosAdapter'`.

- [ ] **Step 3: Add the adapter class**

Append to `src/argus/adapters/ios.py`:

```python
Spawner = Callable[[list[str]], Any]


def _subprocess_spawner(argv: list[str]) -> Any:
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


class IosAdapter(Device):
    """Controls an iOS app through WebDriverAgent."""

    def __init__(
        self,
        name: str,
        *,
        bundle_id: str,
        url: str = _DEFAULT_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        log_command: str | None = None,
        client_factory: WdaClientFactory | None = None,
        spawner: Spawner | None = None,
    ) -> None:
        super().__init__(name)
        self._bundle_id = bundle_id
        self._url = url
        self._timeout = float(timeout)
        self._log_command = log_command
        self._client: WdaClient = (
            client_factory() if client_factory else _HttpWdaClient(url, self._timeout)
        )
        self._spawn: Spawner = spawner or _subprocess_spawner
        self._session_id: str | None = None
        self._scale: float | None = None
        self._screen_info: ScreenInfo | None = None
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._log_process: Any = None
        self._log_pump: threading.Thread | None = None
        self._log = get_logger("argus.ios", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> IosAdapter:
        options: dict[str, Any] = config.options
        bundle_id = options.get("bundle_id")
        if not bundle_id:
            raise ConfigurationError(
                f"iOS device {name!r} needs a bundle_id.",
                remediation="Set devices.<name>.bundle_id (e.g. com.example.app).",
            )
        return cls(
            name,
            bundle_id=str(bundle_id),
            url=str(options.get("url", _DEFAULT_URL)),
            timeout=float(options.get("timeout", _DEFAULT_TIMEOUT)),
            log_command=options.get("log_command"),
        )

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=True,
            supports_tap=True,
            supports_swipe=True,
            supports_long_press=True,
            supports_drag=True,
            supports_multi_touch=True,
            supports_keyboard=True,
            supports_app_lifecycle=True,
            supports_logs=self._log_command is not None,
        )

    @property
    def platform(self) -> str:
        return "ios"

    # -- session plumbing ---------------------------------------------------------------

    def _require_session(self) -> str:
        if self._session_id is None:
            raise DeviceConnectionError(
                f"iOS device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        return self._session_id

    def _session_path(self, suffix: str) -> str:
        return f"/session/{self._require_session()}{suffix}"

    def _post(self, suffix: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._client.request("POST", self._session_path(suffix), body or {})

    # -- connection -------------------------------------------------------------------------

    def connect(self) -> None:
        self._client.request("GET", "/status")
        payload = self._client.request(
            "POST",
            "/session",
            {"capabilities": {"alwaysMatch": {"bundleId": self._bundle_id}}},
        )
        session_id = payload.get("sessionId") or (payload.get("value") or {}).get("sessionId")
        if not session_id:
            raise DeviceConnectionError(
                "WebDriverAgent did not return a session id.",
                remediation=f"Check the WDA log; is the app installed? {_WDA_DOCS}",
            )
        self._session_id = str(session_id)
        self._scale = None
        self._screen_info = None

    def disconnect(self) -> None:
        session_id, self._session_id = self._session_id, None
        if session_id is not None:
            with contextlib.suppress(DeviceConnectionError):
                self._client.request("DELETE", f"/session/{session_id}")

    def is_available(self) -> bool:
        try:
            self._client.request("GET", "/status")
        except DeviceConnectionError:
            return False
        return True

    def health_check(self) -> HealthCheckResult:
        try:
            status = self._client.request("GET", "/status").get("value") or {}
            details: dict[str, Any] = {"url": self._url, "wda_state": status.get("state")}
            if self._session_id is not None:
                details["app_running"] = self.is_application_running()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(str(exc))
        return HealthCheckResult.ok("WebDriverAgent responsive", **details)

    # -- application lifecycle ----------------------------------------------------------

    def is_application_running(self) -> bool:
        state = self._post("/wda/apps/state", {"bundleId": self._bundle_id}).get("value")
        return state == 4
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider`
Expected: all pass (`is_application_running` is included here because `health_check` needs it).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check src/argus/adapters/ios.py tests/unit/test_ios_adapter.py`
Expected: only unused-import warnings for names Tasks 3–7 will use (`base64`, `io`, `shlex`, `Sequence`, `PILImage`, `Image`, `Point`, `ScreenshotError`). If ruff reports anything else, fix it.

- [ ] **Step 6: Commit**

```bash
git add src/argus/adapters/ios.py tests/unit/test_ios_adapter.py
git commit -m "Add IosAdapter session handling and configuration

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: App lifecycle

**Files:**
- Modify: `src/argus/adapters/ios.py`
- Test: `tests/unit/test_ios_adapter.py`

**Interfaces:**
- Consumes: `_post`, `_bundle_id`.
- Produces: `start_application`, `stop_application`, `reset_application`.

- [ ] **Step 1: Write the failing tests**

```python
class TestLifecycle:
    def test_start_stop_reset(self, adapter, wda):
        adapter.connect()
        adapter.start_application()
        adapter.stop_application()
        adapter.reset_application()
        launches = [b for m, p, b in wda.calls if p == "/session/S1/wda/apps/launch"]
        terminates = [b for m, p, b in wda.calls if p == "/session/S1/wda/apps/terminate"]
        assert launches == [{"bundleId": "com.example.app"}] * 2
        assert terminates == [{"bundleId": "com.example.app"}] * 2
        # reset = terminate then launch, in that order
        order = [p for _, p, _ in wda.calls[-2:]]
        assert order == ["/session/S1/wda/apps/terminate", "/session/S1/wda/apps/launch"]

    def test_is_application_running_reads_state(self, adapter, wda):
        adapter.connect()
        assert adapter.is_application_running() is True
        wda.responses[("POST", "/session/S1/wda/apps/state")] = {"value": 1}
        assert adapter.is_application_running() is False

    def test_lifecycle_before_connect_raises(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter.start_application()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider -k Lifecycle`
Expected: FAIL with `DeviceCapabilityError ... does not support 'start_application'`.

- [ ] **Step 3: Implement**

Add to `IosAdapter` after `is_application_running`:

```python
    def start_application(self) -> None:
        self._post("/wda/apps/launch", {"bundleId": self._bundle_id})

    def stop_application(self) -> None:
        self._post("/wda/apps/terminate", {"bundleId": self._bundle_id})

    def reset_application(self) -> None:
        # WebDriverAgent cannot wipe app data; a cold relaunch is the closest reset.
        self.stop_application()
        self.start_application()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/ios.py tests/unit/test_ios_adapter.py
git commit -m "Add iOS app lifecycle through WebDriverAgent

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Screenshot and screen info (scale)

**Files:**
- Modify: `src/argus/adapters/ios.py`
- Test: `tests/unit/test_ios_adapter.py`

**Interfaces:**
- Produces: `screenshot() -> Image`, `get_screen_info() -> ScreenInfo`, `_pixel_scale(self) -> float`, `_to_points(self, point: Point) -> tuple[float, float]`.

- [ ] **Step 1: Write the failing tests**

```python
class TestObservation:
    def test_screenshot_decodes_base64_png(self, adapter, wda):
        adapter.connect()
        img = adapter.screenshot()
        assert img.mode == "RGB" and img.size == (8, 12)

    def test_screenshot_bad_data_raises(self, adapter, wda):
        adapter.connect()
        wda.responses[("GET", "/screenshot")] = {"value": base64.b64encode(b"nope").decode()}
        with pytest.raises(ScreenshotError, match="PNG"):
            adapter.screenshot()

    def test_screen_info_is_points_times_scale(self, adapter, wda):
        adapter.connect()
        info = adapter.get_screen_info()
        assert (info.width, info.height) == (8, 12)
        assert adapter._pixel_scale() == 2.0
        adapter.get_screen_info()
        assert wda.paths("GET").count("/session/S1/wda/screen") == 1  # cached

    def test_scale_defaults_to_one_when_missing(self, adapter, wda):
        adapter.connect()
        wda.responses[("GET", "/session/S1/wda/screen")] = {"value": {}}
        assert adapter._pixel_scale() == 1.0
        assert adapter._to_points((10, 20)) == (10, 20)

    def test_to_points_divides_by_scale(self, adapter, wda):
        adapter.connect()
        assert adapter._to_points((101, 20)) == (50.5, 10)
```

Add `ScreenshotError` to the exceptions import in the test file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider -k Observation`
Expected: FAIL with `does not support 'screenshot'` / `AttributeError: _pixel_scale`.

- [ ] **Step 3: Implement**

Add to `IosAdapter`:

```python
    # -- observation -------------------------------------------------------------------------

    def screenshot(self) -> Image:
        payload = self._client.request("GET", "/screenshot")
        encoded = payload.get("value") or ""
        try:
            data = base64.b64decode(encoded)
            with PILImage.open(io.BytesIO(data)) as img:
                return img.convert("RGB")
        except (ValueError, OSError) as exc:
            raise ScreenshotError(
                f"WebDriverAgent screenshot is not a valid PNG ({len(encoded)} chars).",
                remediation="Check the device is unlocked and WDA is attached to it.",
            ) from exc

    def _pixel_scale(self) -> float:
        if self._scale is None:
            screen = self._client.request("GET", self._session_path("/wda/screen"))
            value = (screen.get("value") or {}).get("scale")
            self._scale = float(value) if value else 1.0
        return self._scale

    def get_screen_info(self) -> ScreenInfo:
        if self._screen_info is None:
            size = self._client.request("GET", self._session_path("/window/size")).get("value")
            size = size or {}
            scale = self._pixel_scale()
            self._screen_info = ScreenInfo(
                width=round(float(size.get("width", 0)) * scale),
                height=round(float(size.get("height", 0)) * scale),
            )
        return self._screen_info

    def _to_points(self, point: Point) -> tuple[float, float]:
        scale = self._pixel_scale()
        return (point[0] / scale, point[1] / scale)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/ios.py tests/unit/test_ios_adapter.py
git commit -m "Add iOS screenshots and pixel scale from WebDriverAgent

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Gesture engine (W3C Actions)

**Files:**
- Modify: `src/argus/adapters/ios.py`
- Test: `tests/unit/test_ios_adapter.py`

**Interfaces:**
- Consumes: `_to_points`, `_post`, `_session_path`.
- Produces: `_perform(self, sources: list[dict]) -> None`; `_finger(index, actions) -> dict`; `tap`, `swipe`, `long_press`, `drag`, `multi_touch`. `pinch` is inherited.

- [ ] **Step 1: Write the failing tests**

```python
def _sources(wda: FakeWda) -> list[dict[str, Any]]:
    body = wda.body("POST", "/session/S1/actions")
    assert body is not None
    return body["actions"]


class TestGestures:
    def test_tap_is_move_down_up_in_points(self, adapter, wda):
        adapter.connect()
        adapter.tap(100, 40)
        (finger,) = _sources(wda)
        assert finger["type"] == "pointer"
        assert finger["parameters"] == {"pointerType": "touch"}
        assert finger["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 50, "y": 20},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerUp", "button": 0},
        ]
        assert wda.paths()[-1] == "/session/S1/actions"
        assert wda.calls[-1][0] == "DELETE"  # pointer state released

    def test_swipe_moves_with_duration(self, adapter, wda):
        adapter.connect()
        adapter.swipe(0, 0, 200, 100, duration_ms=300)
        (finger,) = _sources(wda)
        assert finger["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 0, "y": 0},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "duration": 300, "x": 100, "y": 50},
            {"type": "pointerUp", "button": 0},
        ]

    def test_long_press_pauses(self, adapter, wda):
        adapter.connect()
        adapter.long_press(10, 10, duration_ms=1500)
        (finger,) = _sources(wda)
        assert finger["actions"][2] == {"type": "pause", "duration": 1500}
        assert [a["type"] for a in finger["actions"]] == [
            "pointerMove", "pointerDown", "pause", "pointerUp",
        ]

    def test_drag_holds_then_moves(self, adapter, wda):
        adapter.connect()
        adapter.drag(0, 0, 20, 20, hold_ms=600, duration_ms=250)
        (finger,) = _sources(wda)
        assert finger["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 0, "y": 0},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 600},
            {"type": "pointerMove", "duration": 250, "x": 10, "y": 10},
            {"type": "pointerUp", "button": 0},
        ]

    def test_multi_touch_one_source_per_finger_segment_durations(self, adapter, wda):
        adapter.connect()
        adapter.multi_touch([[(0, 0), (20, 0), (20, 20)], [(100, 100), (80, 80)]], 400)
        first, second = _sources(wda)
        assert first["id"] == "finger0" and second["id"] == "finger1"
        assert first["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 0, "y": 0},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "duration": 200, "x": 10, "y": 0},
            {"type": "pointerMove", "duration": 200, "x": 10, "y": 10},
            {"type": "pointerUp", "button": 0},
        ]
        assert second["actions"] == [
            {"type": "pointerMove", "duration": 0, "x": 50, "y": 50},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "duration": 400, "x": 40, "y": 40},
            {"type": "pointerUp", "button": 0},
        ]

    def test_pinch_produces_two_mirrored_fingers(self, adapter, wda):
        adapter.connect()
        adapter.pinch(200, 300, start_distance=100, end_distance=200, duration_ms=500)
        left, right = _sources(wda)
        assert left["actions"][0] == {"type": "pointerMove", "duration": 0, "x": 75, "y": 150}
        assert left["actions"][2] == {"type": "pointerMove", "duration": 500, "x": 50, "y": 150}
        assert right["actions"][0] == {"type": "pointerMove", "duration": 0, "x": 125, "y": 150}
        assert right["actions"][2] == {"type": "pointerMove", "duration": 500, "x": 150, "y": 150}

    def test_gesture_before_connect_raises(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter.tap(1, 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider -k Gestures`
Expected: FAIL with `does not support 'tap'` etc.

- [ ] **Step 3: Implement**

Add to `IosAdapter`:

```python
    # -- input (W3C Actions) --------------------------------------------------------------

    def _move(self, point: Point, duration_ms: int) -> dict[str, Any]:
        x, y = self._to_points(point)
        return {"type": "pointerMove", "duration": duration_ms, "x": _num(x), "y": _num(y)}

    @staticmethod
    def _finger(index: int, actions: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "type": "pointer",
            "id": f"finger{index}",
            "parameters": {"pointerType": "touch"},
            "actions": actions,
        }

    def _perform(self, sources: list[dict[str, Any]]) -> None:
        self._post("/actions", {"actions": sources})
        with contextlib.suppress(DeviceConnectionError):
            self._client.request("DELETE", self._session_path("/actions"))

    def tap(self, x: int, y: int) -> None:
        self._perform([self._finger(0, [self._move((x, y), 0), _DOWN, _UP])])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._perform(
            [
                self._finger(
                    0,
                    [self._move((x1, y1), 0), _DOWN, self._move((x2, y2), duration_ms), _UP],
                )
            ]
        )

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        self._perform(
            [self._finger(0, [self._move((x, y), 0), _DOWN, _pause(duration_ms), _UP])]
        )

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, hold_ms: int = 500, duration_ms: int = 500
    ) -> None:
        self._perform(
            [
                self._finger(
                    0,
                    [
                        self._move((x1, y1), 0),
                        _DOWN,
                        _pause(hold_ms),
                        self._move((x2, y2), duration_ms),
                        _UP,
                    ],
                )
            ]
        )

    def multi_touch(self, fingers: Sequence[Sequence[Point]], duration_ms: int = 500) -> None:
        sources = []
        for index, path in enumerate(fingers):
            segments = max(1, len(path) - 1)
            segment_ms = round(duration_ms / segments)
            actions = [self._move(path[0], 0), _DOWN]
            actions += [self._move(point, segment_ms) for point in path[1:]]
            actions.append(_UP)
            sources.append(self._finger(index, actions))
        self._perform(sources)
```

And module-level helpers (after `_decode`):

```python
_DOWN = {"type": "pointerDown", "button": 0}
_UP = {"type": "pointerUp", "button": 0}


def _pause(duration_ms: int) -> dict[str, Any]:
    return {"type": "pause", "duration": duration_ms}


def _num(value: float) -> float | int:
    """Render whole numbers as ints so request bodies read like the docs."""
    return int(value) if float(value).is_integer() else value
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/ios.py tests/unit/test_ios_adapter.py
git commit -m "Add iOS touch gestures as WebDriverAgent W3C actions

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Key input

**Files:**
- Modify: `src/argus/adapters/ios.py`
- Test: `tests/unit/test_ios_adapter.py`

**Interfaces:**
- Produces: `press_key(key: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
class TestKeys:
    @pytest.mark.parametrize(
        ("key", "path", "body"),
        [
            ("HOME", "/session/S1/wda/homescreen", {}),
            ("KEYCODE_HOME", "/session/S1/wda/homescreen", {}),
            ("VOLUME_UP", "/session/S1/wda/pressButton", {"name": "volumeUp"}),
            ("VOLUME_DOWN", "/session/S1/wda/pressButton", {"name": "volumeDown"}),
            ("LOCK", "/session/S1/wda/pressButton", {"name": "lock"}),
            ("ENTER", "/session/S1/wda/keys", {"value": ["\n"]}),
            ("DEL", "/session/S1/wda/keys", {"value": ["\b"]}),
            ("a", "/session/S1/wda/keys", {"value": ["a"]}),
            ("hello", "/session/S1/wda/keys", {"value": ["h", "e", "l", "l", "o"]}),
        ],
    )
    def test_press_key_mapping(self, adapter, wda, key, path, body):
        adapter.connect()
        adapter.press_key(key)
        assert wda.calls[-1] == ("POST", path, body)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider -k Keys`
Expected: FAIL with `does not support 'press_key'`.

- [ ] **Step 3: Implement**

Module-level tables (after `_UP`):

```python
_BUTTONS = {"VOLUME_UP": "volumeUp", "VOLUME_DOWN": "volumeDown", "LOCK": "lock"}
_KEY_TEXT = {"ENTER": "\n", "DPAD_CENTER": "\n", "DEL": "\b", "BACKSPACE": "\b", "TAB": "\t",
             "SPACE": " "}
```

Method on `IosAdapter`:

```python
    def press_key(self, key: str) -> None:
        name = key.removeprefix("KEYCODE_")
        upper = name.upper()
        if upper == "HOME":
            self._post("/wda/homescreen", {})
        elif upper in _BUTTONS:
            self._post("/wda/pressButton", {"name": _BUTTONS[upper]})
        else:
            text = _KEY_TEXT.get(upper, name)
            self._post("/wda/keys", {"value": list(text)})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/ios.py tests/unit/test_ios_adapter.py
git commit -m "Add iOS key input through WebDriverAgent

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Log stream

**Files:**
- Modify: `src/argus/adapters/ios.py`
- Test: `tests/unit/test_ios_adapter.py`

**Interfaces:**
- Consumes: `Spawner`, `_spawn`, `_log_command`, `_logs`.
- Produces: `get_logs(lines)`, `_LogPump`, `_start_log_stream`, `_stop_log_stream`; `connect` starts the stream, `disconnect` stops it.

- [ ] **Step 1: Write the failing tests**

Add at top of the test file `import time` and the helpers:

```python
class FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = io.BytesIO("".join(line + "\n" for line in lines).encode())
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestLogs:
    def test_get_logs_unsupported_without_log_command(self, adapter):
        adapter.connect()
        with pytest.raises(DeviceCapabilityError, match="get_logs"):
            adapter.get_logs()

    def test_log_command_is_spawned_and_pumped(self, wda):
        process = FakeProcess(["boot", "Player: state=PLAYING"])
        spawned: list[list[str]] = []

        def spawn(argv):
            spawned.append(argv)
            return process

        adapter = IosAdapter(
            "iphone",
            bundle_id="com.example.app",
            log_command="xcrun simctl spawn booted log stream --predicate 'process == \"Ex\"'",
            client_factory=lambda: wda,
            spawner=spawn,
        )
        adapter.connect()
        assert spawned == [
            ["xcrun", "simctl", "spawn", "booted", "log", "stream", "--predicate",
             'process == "Ex"']
        ]
        assert _wait_for(lambda: "Player: state=PLAYING" in adapter.get_logs())
        assert adapter.get_logs(1) == "Player: state=PLAYING"
        adapter.disconnect()
        assert process.terminated

    def test_missing_log_binary_raises_remediated(self, wda):
        def spawn(argv):
            raise FileNotFoundError(argv[0])

        adapter = IosAdapter(
            "iphone", bundle_id="com.example.app", log_command="idevicesyslog",
            client_factory=lambda: wda, spawner=spawn,
        )
        with pytest.raises(DeviceConnectionError, match="idevicesyslog"):
            adapter.connect()
        # connect failed after the session was created: it must be cleaned up
        assert ("DELETE", "/session/S1", None) in wda.calls
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider -k Logs`
Expected: `test_get_logs_unsupported_without_log_command` passes already (base class); the other two FAIL (`spawned == []`, no exception).

- [ ] **Step 3: Implement**

Module-level, after `_subprocess_spawner`:

```python
class _LogPump(threading.Thread):
    """Copies a process's stdout lines into a bounded deque."""

    def __init__(self, process: Any, sink: deque[str]) -> None:
        super().__init__(daemon=True, name="ios-log")
        self._process = process
        self._sink = sink

    def run(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            self._sink.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
```

In `IosAdapter.connect`, after `self._screen_info = None`:

```python
        if self._log_command:
            try:
                self._start_log_stream()
            except DeviceConnectionError:
                self.disconnect()
                raise
```

In `disconnect`, first line: `self._stop_log_stream()`.

Add methods:

```python
    # -- logs --------------------------------------------------------------------------------

    def _start_log_stream(self) -> None:
        argv = shlex.split(self._log_command or "")
        self._logs.clear()
        try:
            self._log_process = self._spawn(argv)
        except FileNotFoundError as exc:
            raise DeviceConnectionError(
                f"log_command binary not found: {argv[0] if argv else '<empty>'!r}.",
                remediation="Install it (Xcode for xcrun, libimobiledevice for idevicesyslog) "
                "or remove devices.<name>.log_command.",
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

    def get_logs(self, lines: int = 200) -> str:
        if self._log_command is None:
            raise self._unsupported("get_logs")
        if lines <= 0:
            return ""
        return "\n".join(list(self._logs)[-lines:])
```

- [ ] **Step 4: Run the tests and lint**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider && .venv/bin/ruff check src/argus/adapters/ios.py tests/unit/test_ios_adapter.py && .venv/bin/mypy src/argus/adapters/ios.py`
Expected: all pass; ruff clean (every import now used — remove any that is not); mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/ios.py tests/unit/test_ios_adapter.py
git commit -m "Stream iOS logs from an optional log_command

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Registry, integration test, docs

**Files:**
- Modify: `src/argus/adapters/registry.py:57-70`
- Create: `tests/integration/test_ios_adapter_e2e.py`, `docs/ios.md`
- Modify: `docs/adapters.md` (built-in table), `docs/configuration.md:47`, `docs/test-authoring.md` (gesture note), `README.md` (supported line ~line 26 and docs table ~line 98), `CHANGELOG.md` (Unreleased → Added)
- Test: `tests/unit/test_ios_adapter.py`

- [ ] **Step 1: Write the failing registry test**

```python
class TestRegistry:
    def test_registered_as_ios(self):
        from argus.adapters.registry import DeviceRegistry

        registry = DeviceRegistry()
        assert "ios" in registry.types()
        device = registry.create(
            "iphone", DeviceConfig.model_validate({"type": "ios", "bundle_id": "com.x.y"})
        )
        assert isinstance(device, IosAdapter)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider -k Registry`
Expected: FAIL `assert 'ios' in [...]`.

- [ ] **Step 3: Register**

In `src/argus/adapters/registry.py` `register_builtin_devices`, add `from argus.adapters.ios import IosAdapter` between the `fake` and `roku` imports and `registry.register("ios", IosAdapter.from_config)` after the `fake` registration line, keeping the alphabetical order of the import block.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_ios_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Integration smoke test**

Create `tests/integration/test_ios_adapter_e2e.py`:

```python
"""IosAdapter against a running WebDriverAgent. Skipped unless configured."""

from __future__ import annotations

import os

import pytest

from argus.adapters.ios import IosAdapter
from argus.exceptions import DeviceConnectionError

pytestmark = pytest.mark.integration

WDA_URL = os.environ.get("ARGUS_WDA_URL")
BUNDLE_ID = os.environ.get("ARGUS_IOS_BUNDLE_ID")


@pytest.fixture
def device():
    if not WDA_URL or not BUNDLE_ID:
        pytest.skip("ARGUS_WDA_URL / ARGUS_IOS_BUNDLE_ID not set")
    adapter = IosAdapter("iphone", bundle_id=BUNDLE_ID, url=WDA_URL, timeout=15)
    try:
        adapter.connect()
    except DeviceConnectionError as exc:
        pytest.skip(f"WebDriverAgent unavailable: {exc}")
    yield adapter
    adapter.disconnect()


def test_launch_screenshot_and_tap(device: IosAdapter):
    device.start_application()
    assert device.is_application_running()
    img = device.screenshot()
    info = device.get_screen_info()
    assert img.size == (info.width, info.height)
    device.tap(info.width // 2, info.height // 2)
    device.pinch(info.width // 2, info.height // 2, 100, 300)
```

Run: `.venv/bin/python -m pytest tests/integration/test_ios_adapter_e2e.py -q -p no:cacheprovider`
Expected: 1 skipped.

- [ ] **Step 6: Write `docs/ios.md`**

```markdown
# iOS

Argus drives iOS apps — in the Simulator or on a physical device — through
[WebDriverAgent](https://github.com/appium/WebDriverAgent) (WDA), Appium's
open-source iOS automation server. No Appium server is needed: Argus talks
to WDA's HTTP API directly.

| Operation | Implementation |
| --- | --- |
| Connect | `GET /status`, then `POST /session` for `bundle_id` |
| Screenshot | `GET /screenshot` (base64 PNG) |
| Screen size | `window/size` (points) × `wda/screen` scale = pixels |
| Start / stop app | `wda/apps/launch` / `wda/apps/terminate` |
| Reset app | terminate + launch (WDA cannot wipe app data) |
| App running? | `wda/apps/state == 4` |
| Tap, swipe, long press, drag, multi-touch, pinch | W3C Actions (`POST /session/<id>/actions`), one touch input source per finger |
| Keys | `HOME` → `wda/homescreen`; `VOLUME_UP` / `VOLUME_DOWN` / `LOCK` → `wda/pressButton`; anything else is typed with `wda/keys` (`ENTER` = newline, `DEL` = backspace) |
| Logs | optional `log_command` subprocess (see below) |

Coordinates in tests are **screenshot pixels**, exactly as on Android; the
adapter converts to WDA points using the device scale.

## Prerequisites

1. macOS with Xcode.
2. WebDriverAgent running against your target. Clone it, open
   `WebDriverAgent.xcodeproj`, and run the `WebDriverAgentRunner` scheme's
   tests on the simulator or device (or from a terminal):

   ```bash
   xcodebuild -project WebDriverAgent.xcodeproj \
     -scheme WebDriverAgentRunner \
     -destination 'id=<udid>' test
   ```

   Physical devices need a signing team and a unique bundle id for the
   runner (Xcode → Signing & Capabilities), plus Developer Mode enabled on
   the device.
3. Reachability: simulators listen on `http://127.0.0.1:8100`. For a device
   either forward the port (`brew install libimobiledevice && iproxy 8100 8100`)
   or use the device's Wi‑Fi IP in `url`.
4. The app under test installed on the target.

## Configuration

```yaml
devices:
  iphone:
    type: ios
    platform: ios
    bundle_id: com.example.app          # required
    url: http://127.0.0.1:8100          # optional, WebDriverAgent base URL
    timeout: 30                         # optional, seconds per request
    # Optional log source (enables log_contains / get_logs):
    log_command: xcrun simctl spawn booted log stream --style compact --predicate 'process == "Example"'
    # Physical device alternative:
    # log_command: idevicesyslog -u <udid>
```

## Gestures

All `device.*` gestures from [test-authoring.md](test-authoring.md) work on
iOS, including `device.pinch` and `device.multi_touch`; WebDriverAgent runs
every finger's sequence concurrently.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Cannot reach WebDriverAgent` | WDA is not running or the port is not forwarded. Re-run the `xcodebuild … test` command and keep it running. |
| `WebDriverAgent error 'invalid session id'` | WDA restarted mid-run; start the run again. |
| `did not return a session id` | The app is not installed for that `bundle_id`; check WDA's log. |
| Black or wrong-size screenshots | Unlock the device; verify `bundle_id`. |
| `log_command binary not found` | Install Xcode (`xcrun`) or libimobiledevice (`idevicesyslog`), or drop `log_command`. |
```

- [ ] **Step 7: Cross-references**

- `docs/adapters.md` built-in table: after the `esp32` row add
  `| \`ios\` | WebDriverAgent HTTP | iOS app on a simulator or device, full gesture set, see [ios.md](ios.md) |`.
- `docs/configuration.md:47`: extend the type comment to `android | ios | yocto | browser | roku | tvos_sim | appletv | esp32 | fake | plugin-provided`.
- `docs/test-authoring.md`: in the gesture paragraph added by the touch-gestures branch, replace "Gestures are only available where the device can produce them (see each adapter's page)" with "Gestures are only available where the device can produce them: pinch and multi-touch work on Android, iOS and chromium browsers; long press and drag additionally on every mouse-driven adapter; TV platforms have no touch input (see each adapter's page)".
- `README.md`: in the "Supported today" paragraph add **iOS** (WebDriverAgent) after Android; in the docs table add `| iOS setup | [docs/ios.md](docs/ios.md) |` next to the Apple TV row.
- `CHANGELOG.md` Unreleased → Added, first bullet:
  `- \`ios\` device adapter: iOS apps on simulators and physical devices through WebDriverAgent — screenshots, launch/terminate, W3C-Actions gestures (tap, swipe, long press, drag, multi-touch, pinch), key input, and optional \`log_command\` logs. Platform label \`ios\`.`

- [ ] **Step 8: Full verification**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | grep -c FAILED` → expected `12` (baseline only).
Run: `.venv/bin/ruff check src tests` → only the pre-existing `test_console_reporter.py` import-order error.
Run: `.venv/bin/mypy src` → only the two pre-existing errors.

- [ ] **Step 9: Commit**

```bash
git add src/argus/adapters/registry.py tests/unit/test_ios_adapter.py tests/integration/test_ios_adapter_e2e.py docs/ios.md docs/adapters.md docs/configuration.md docs/test-authoring.md README.md CHANGELOG.md
git commit -m "Register the iOS adapter and document it

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review against the spec (section 1)

- Transport / `WdaClient` protocol / `urllib` — Task 1. ✔
- `connect` → `/status` + `POST /session`; `disconnect` → `DELETE /session/<id>`; `is_available`; `health_check` with `app_running` — Task 2. ✔
- Lifecycle (launch / terminate / reset = terminate+launch / state == 4) — Tasks 2–3. ✔
- Screenshot base64 PNG → RGB, `ScreenshotError`; screen info = points × scale, cached — Task 4. ✔
- Gesture engine: one helper, touch pointer sources, pause for hold, stepped moves, `DELETE /actions` after — Task 5; `pinch` inherited. ✔
- Keys: home / pressButton / keys with ENTER + DEL mapping — Task 6. ✔
- Logs: optional `log_command`, stream thread, `supports_logs` follows config — Tasks 2 and 7. ✔
- Errors: transport → `DeviceConnectionError` with docs pointer; WDA error payload surfaced with `error` + `message`, session errors add "reconnect"; session-less use → `DeviceConnectionError` — Tasks 1, 2, 5. ✔
- Config (`bundle_id` required, `url`, `timeout`, `log_command`), `from_config` — Task 2. ✔
- Preflight: existing `DeviceCheck` uses `is_available`/`health_check`; no new class. ✔
- Registry, docs (`docs/ios.md`, adapters table, README, configuration, test-authoring, CHANGELOG), integration test — Task 8. ✔
