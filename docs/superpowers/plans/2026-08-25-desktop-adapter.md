# Desktop Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Argus tests drive native desktop applications on Windows, Ubuntu/Linux and macOS through one `desktop` adapter — launch the app as a process, screenshot the display, drive it with mouse and keyboard, capture its stdout/stderr as logs.

**Architecture:** One new module `src/argus/adapters/desktop.py`. A `DesktopBackend` protocol isolates the slice of `pyautogui` the adapter uses (screenshot, size, click, mouseDown/Up, moveTo, press, hotkey); the production backend imports `pyautogui` lazily. `_ProcessHandle` wraps `subprocess.Popen` with merged stdout/stderr pumped by a daemon thread into a bounded deque. `DesktopAdapter(Device)` owns the backend, the process handle, the log buffer and a cached pixel ratio (screenshot px ÷ logical size) so test authors always type screenshot-pixel coordinates, exactly as on Android/iOS/browser.

**Tech Stack:** Python 3.12, `subprocess` + `threading` (stdlib), Pillow, pytest; optional `pyautogui>=0.9.54` behind an `argus[desktop]` extra.

**Spec:** `docs/superpowers/specs/2026-08-24-ios-and-desktop-adapters-design.md` — section 2 ("Desktop adapter") and "Shared contracts". Read it first; this plan implements that section only.

## Global Constraints

- `requires-python = ">=3.12"`; `.venv/bin/ruff check src tests` (line length 100) and `.venv/bin/mypy src` must show **no new** errors on touched files.
- `pyautogui` is optional: importing `argus` and the full non-integration suite must pass with pyautogui **absent** (it is NOT installed in the dev venv; do not install it). Missing pyautogui → `DeviceConnectionError` with `remediation='pip install "argus[desktop]"'`.
- Coordinates in tests/YAML are **screenshot pixels**; the adapter divides by the measured ratio (screenshot width ÷ `size().width`) and adds the `region` offset to get logical coordinates.
- Every failure is a remediated exception: missing display/pyautogui → `DeviceConnectionError`; missing `command` → `ConfigurationError`; screenshot problems → `ScreenshotError`; `multi_touch`/`pinch` → `DeviceCapabilityError` ("desktop has no touch injection; zoom with `device.key: Ctrl+Plus` / `Cmd+Plus`").
- Device type `desktop`; platform label defaults from `sys.platform`: `win32` → `windows`, `darwin` → `macos`, anything else → `linux`; an explicit `platform:` in config wins (`DeviceConfig.effective_platform` is what the runner filters on — the adapter's `platform` property must return the same derived value).
- Capabilities: screenshot, tap, swipe, long_press, drag, keyboard, app_lifecycle, logs = `True`; multi_touch = `False`.
- Log buffer: `collections.deque(maxlen=5000)`, oldest first; `get_logs(lines)` = last `lines` joined by `"\n"`, `""` for `lines <= 0`.
- Durations in config are strings parsed with `argus.utilities.duration.parse_duration` (returns seconds as float): `startup_wait` default `0s`, `stop_timeout` default `5s`.
- Registry imports alphabetical (android, appletv, browser, desktop, esp32, fake, ios, roku, tvos_sim, yocto); register calls keep `desktop` after `browser`.
- Every YAML example in docs sets `platform:` explicitly.
- Commit messages: short imperative sentence, no prefix, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. `git add` only the files each task lists.
- Baseline on this branch: 11 failing tests (`tests/unit/test_text_verifiers.py` ×5, `tests/unit/test_console_reporter.py` ×6), 3 ruff errors (`src/argus/engine/runner.py` SIM102, `src/argus/reporting/html.py` E501, `tests/unit/test_console_reporter.py` I001), 2 mypy errors (`src/argus/ocr/preprocess.py`, `src/argus/verifiers/image.py`). Out of scope; the gate is *no new failures*.
- Run from the repo root: `.venv/bin/python -m pytest ...`, `.venv/bin/ruff check ...`, `.venv/bin/mypy src`.

---

## File map

| Path | Responsibility |
| --- | --- |
| `src/argus/adapters/desktop.py` (new) | `DesktopBackend` protocol, `_PyAutoGuiBackend`, `_ProcessHandle`, `_LogPump`, `DesktopAdapter`, key mapping |
| `src/argus/adapters/registry.py` (modify) | register `desktop` |
| `pyproject.toml` (modify) | `desktop` extra, included in `all` |
| `tests/unit/test_desktop_adapter.py` (new) | all unit tests (`FakeBackend`, real child process) |
| `tests/integration/test_desktop_adapter_e2e.py` (new) | real screenshot + size; skipped without pyautogui/display |
| `docs/desktop.md` (new) | prerequisites per OS, config, operation table, gestures, troubleshooting |
| `docs/adapters.md`, `docs/configuration.md`, `docs/getting-started.md`, `docs/test-authoring.md`, `README.md`, `CHANGELOG.md` (modify) | references |

---

### Task 1: Backend protocol and process handle

**Files:**
- Create: `src/argus/adapters/desktop.py`
- Test: `tests/unit/test_desktop_adapter.py`

**Interfaces:**
- Produces: `DesktopBackend` protocol (`size() -> tuple[int, int]`, `screenshot() -> Image`, `click(x, y)`, `mouseDown(x, y)`, `mouseUp()`, `moveTo(x, y, duration)`, `press(key)`, `hotkey(*keys)`); `BackendFactory = Callable[[], DesktopBackend]`; `_pyautogui_backend() -> DesktopBackend` (lazy import, remediated error); `_ProcessHandle(argv, cwd, env, sink)` with `.running -> bool`, `.stop(timeout)`, `.pid`; `_LogPump`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_desktop_adapter.py`:

```python
"""DesktopAdapter (pyautogui + subprocess) behaviour, verified through a fake backend."""

from __future__ import annotations

import sys
import time
from collections import deque
from typing import Any

import pytest
from PIL import Image

from argus.adapters.desktop import _ProcessHandle, _pyautogui_backend
from argus.exceptions import DeviceConnectionError

_CHILD = (
    "import sys, time\n"
    "print('child started', flush=True)\n"
    "print('warning: something', file=sys.stderr, flush=True)\n"
    "time.sleep(30)\n"
)


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestBackendImport:
    def test_missing_pyautogui_is_remediated(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyautogui", None)  # makes `import pyautogui` fail
        with pytest.raises(DeviceConnectionError, match="pyautogui") as info:
            _pyautogui_backend()
        assert 'argus[desktop]' in (info.value.remediation or "")


class TestProcessHandle:
    def test_captures_merged_output_and_stops(self):
        sink: deque[str] = deque(maxlen=100)
        handle = _ProcessHandle([sys.executable, "-c", _CHILD], cwd=None, env=None, sink=sink)
        try:
            assert handle.running
            assert _wait_for(lambda: len(sink) >= 2)
            assert list(sink) == ["child started", "warning: something"]
        finally:
            handle.stop(timeout=2.0)
        assert not handle.running

    def test_stop_kills_when_terminate_is_ignored(self):
        sink: deque[str] = deque(maxlen=100)
        stubborn = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(30)\n"
        )
        handle = _ProcessHandle([sys.executable, "-c", stubborn], cwd=None, env=None, sink=sink)
        assert _wait_for(lambda: "ready" in sink)
        started = time.monotonic()
        handle.stop(timeout=0.5)
        assert not handle.running
        assert time.monotonic() - started < 5.0

    def test_missing_executable_is_connection_error(self):
        with pytest.raises(DeviceConnectionError, match="not found"):
            _ProcessHandle(["/definitely/not/a/binary"], cwd=None, env=None, sink=deque())
```

(`test_stop_kills_when_terminate_is_ignored` is POSIX-specific; mark it `@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM semantics")`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'argus.adapters.desktop'`.

- [ ] **Step 3: Write the module foundation**

Create `src/argus/adapters/desktop.py`:

```python
"""Desktop application adapter (Windows / Linux / macOS).

Launches the application under test as a local process, screenshots the
display and drives it with mouse and keyboard through ``pyautogui``
(optional dependency: ``pip install "argus[desktop]"``). The process's
stdout/stderr become the device logs. Coordinates in tests are screenshot
pixels; the adapter converts them to logical (HiDPI-scaled) coordinates.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from PIL.Image import Image

from argus.adapters.base import Device, DeviceCapabilities, Point
from argus.config.models import DeviceConfig
from argus.exceptions import (
    ConfigurationError,
    DeviceCapabilityError,
    DeviceConnectionError,
    ScreenshotError,
)
from argus.logging import get_logger
from argus.models.common import HealthCheckResult, ScreenInfo
from argus.utilities.duration import parse_duration

_MAX_LOG_LINES = 5000
_INSTALL = 'pip install "argus[desktop]"'


class DesktopBackend(Protocol):
    """The slice of pyautogui the adapter relies on (fakeable)."""

    def size(self) -> tuple[int, int]: ...
    def screenshot(self) -> Image: ...
    def click(self, x: float, y: float) -> None: ...
    def mouseDown(self, x: float, y: float) -> None: ...  # noqa: N802 - pyautogui names
    def mouseUp(self) -> None: ...  # noqa: N802
    def moveTo(self, x: float, y: float, duration: float = 0.0) -> None: ...  # noqa: N802
    def press(self, key: str) -> None: ...
    def hotkey(self, *keys: str) -> None: ...


BackendFactory = Callable[[], DesktopBackend]


def _pyautogui_backend() -> DesktopBackend:
    try:
        import pyautogui
    except ImportError as exc:
        raise DeviceConnectionError(
            "pyautogui is not installed (required for desktop devices).",
            remediation=f"Install desktop support: {_INSTALL}",
        ) from exc
    pyautogui.FAILSAFE = False  # a corner-of-screen mouse must not abort a test run
    pyautogui.PAUSE = 0  # gestures pace themselves; no per-call sleep
    return pyautogui  # type: ignore[no-any-return]


class _LogPump(threading.Thread):
    """Copies a process's stdout lines into a bounded deque."""

    def __init__(self, process: Any, sink: deque[str]) -> None:
        super().__init__(daemon=True, name="desktop-app-log")
        self._process = process
        self._sink = sink

    def run(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            self._sink.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))


class _ProcessHandle:
    """A launched application: Popen with stdout+stderr pumped into ``sink``."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path | None,
        env: dict[str, str] | None,
        sink: deque[str],
    ) -> None:
        try:
            self._process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            raise DeviceConnectionError(
                f"Application executable not found: {argv[0]!r}.",
                remediation="Check devices.<name>.command (and cwd) point at the built app.",
            ) from exc
        except OSError as exc:
            raise DeviceConnectionError(
                f"Unable to launch {argv[0]!r}: {exc}",
                remediation="Check the file is executable and the platform matches.",
            ) from exc
        self._pump = _LogPump(self._process, sink)
        self._pump.start()

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def running(self) -> bool:
        return self._process.poll() is None

    def stop(self, timeout: float) -> None:
        if self.running:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=timeout)
        self._pump.join(timeout=2.0)
```

(`os`, `sys`, `time`, `Point`, `Device`, `DeviceCapabilities`, `DeviceConfig`, `ConfigurationError`, `DeviceCapabilityError`, `ScreenshotError`, `get_logger`, `HealthCheckResult`, `ScreenInfo`, `parse_duration` are used by Tasks 2–6; ruff will flag them as unused until then — expected.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider`
Expected: 4 passed (3 on Windows).

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/desktop.py tests/unit/test_desktop_adapter.py
git commit -m "Add desktop backend protocol and application process handle

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: DesktopAdapter identity, configuration, connection

**Files:**
- Modify: `src/argus/adapters/desktop.py`
- Test: `tests/unit/test_desktop_adapter.py`

**Interfaces:**
- Consumes: `DesktopBackend`, `BackendFactory`, `_pyautogui_backend`.
- Produces: `DesktopAdapter(name, *, command, args=(), cwd=None, env=None, startup_wait=0.0, stop_timeout=5.0, reset_command=None, region=None, platform=None, backend_factory=None)`; `DesktopAdapter.from_config`; `_host_platform() -> str`; `_require_backend() -> DesktopBackend`; attributes `_backend`, `_command`, `_args`, `_cwd`, `_env`, `_startup_wait`, `_stop_timeout`, `_reset_command`, `_region`, `_logs`, `_process`.

- [ ] **Step 1: Write the failing tests**

Append to the test file (extend imports: `from argus.adapters.desktop import DesktopAdapter, _ProcessHandle, _host_platform, _pyautogui_backend`; `from argus.config.models import DeviceConfig`; `from argus.exceptions import ConfigurationError, DeviceCapabilityError, DeviceConnectionError, ScreenshotError`):

```python
class FakeBackend:
    """Records pyautogui-style calls; screenshot/logical sizes are configurable."""

    def __init__(
        self,
        *,
        logical: tuple[int, int] = (800, 600),
        pixels: tuple[int, int] = (800, 600),
        fail_size: bool = False,
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.logical = logical
        self.pixels = pixels
        self.fail_size = fail_size
        self.fill = (10, 20, 30)

    def size(self) -> tuple[int, int]:
        if self.fail_size:
            raise RuntimeError("no display")
        return self.logical

    def screenshot(self) -> Image.Image:
        self.calls.append(("screenshot", ()))
        return Image.new("RGB", self.pixels, self.fill)

    def click(self, x: float, y: float) -> None:
        self.calls.append(("click", (x, y)))

    def mouseDown(self, x: float, y: float) -> None:  # noqa: N802
        self.calls.append(("mouseDown", (x, y)))

    def mouseUp(self) -> None:  # noqa: N802
        self.calls.append(("mouseUp", ()))

    def moveTo(self, x: float, y: float, duration: float = 0.0) -> None:  # noqa: N802
        self.calls.append(("moveTo", (x, y, duration)))

    def press(self, key: str) -> None:
        self.calls.append(("press", (key,)))

    def hotkey(self, *keys: str) -> None:
        self.calls.append(("hotkey", keys))


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def adapter(backend: FakeBackend) -> DesktopAdapter:
    return DesktopAdapter(
        "app",
        command=sys.executable,
        args=["-c", _CHILD],
        backend_factory=lambda: backend,
    )


class TestIdentity:
    @pytest.mark.parametrize(
        ("sys_platform", "expected"),
        [("win32", "windows"), ("darwin", "macos"), ("linux", "linux"), ("freebsd14", "linux")],
    )
    def test_platform_derived_from_host(self, monkeypatch, sys_platform, expected):
        monkeypatch.setattr(sys, "platform", sys_platform)
        assert _host_platform() == expected
        adapter = DesktopAdapter("app", command="x", backend_factory=FakeBackend)
        assert adapter.platform == expected

    def test_explicit_platform_wins(self):
        adapter = DesktopAdapter("app", command="x", platform="kiosk", backend_factory=FakeBackend)
        assert adapter.platform == "kiosk"

    def test_capabilities(self, adapter):
        caps = adapter.capabilities
        assert caps.supports_screenshot and caps.supports_tap and caps.supports_swipe
        assert caps.supports_long_press and caps.supports_drag and caps.supports_keyboard
        assert caps.supports_app_lifecycle and caps.supports_logs
        assert caps.supports_multi_touch is False


class TestConnection:
    def test_connect_requires_working_display(self, adapter, backend):
        adapter.connect()
        assert adapter.is_available() is True
        result = adapter.health_check()
        assert result.healthy
        assert result.details["screen"] == "800x600"
        assert result.details["app_running"] is False

    def test_connect_without_display_is_remediated(self, monkeypatch):
        backend = FakeBackend(fail_size=True)
        adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(DeviceConnectionError, match="no display") as info:
            adapter.connect()
        assert "DISPLAY" in (info.value.remediation or "")
        assert adapter.is_available() is False

    def test_macos_remediation_mentions_permissions(self, monkeypatch):
        backend = FakeBackend(fail_size=True)
        adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        monkeypatch.setattr(sys, "platform", "darwin")
        with pytest.raises(DeviceConnectionError) as info:
            adapter.connect()
        assert "Screen Recording" in (info.value.remediation or "")

    def test_operations_before_connect_raise(self, adapter):
        with pytest.raises(DeviceConnectionError, match="not connected"):
            adapter._require_backend()


class TestConfig:
    def test_from_config(self, tmp_path):
        config = DeviceConfig.model_validate(
            {
                "type": "desktop",
                "command": "./build/App",
                "args": ["--fullscreen"],
                "cwd": str(tmp_path),
                "env": {"EXAMPLE": "1"},
                "startup_wait": "2s",
                "stop_timeout": "250ms",
                "reset_command": "./reset.sh",
                "region": [10, 20, 300, 200],
            }
        )
        adapter = DesktopAdapter.from_config("app", config)
        assert adapter._command == "./build/App"
        assert adapter._args == ["--fullscreen"]
        assert adapter._cwd == str(tmp_path)
        assert adapter._env == {"EXAMPLE": "1"}
        assert adapter._startup_wait == 2.0
        assert adapter._stop_timeout == 0.25
        assert adapter._reset_command == "./reset.sh"
        assert adapter._region == (10, 20, 300, 200)

    def test_from_config_uses_effective_platform(self):
        config = DeviceConfig.model_validate({"type": "desktop", "command": "x", "platform": "linux"})
        assert DesktopAdapter.from_config("app", config).platform == "linux"

    def test_from_config_requires_command(self):
        with pytest.raises(ConfigurationError, match="command"):
            DesktopAdapter.from_config("app", DeviceConfig.model_validate({"type": "desktop"}))

    def test_from_config_rejects_bad_region(self):
        config = DeviceConfig.model_validate({"type": "desktop", "command": "x", "region": [1, 2]})
        with pytest.raises(ConfigurationError, match="region"):
            DesktopAdapter.from_config("app", config)
```

Check `DeviceConfig.effective_platform` exists: `grep -n effective_platform src/argus/config/models.py` (it does — the browser/roku adapters rely on it). Note `DeviceConfig.options` excludes `type`/`platform`/`instrumentation`, so pass `platform=config.effective_platform` explicitly.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider`
Expected: ImportError for `DesktopAdapter` / `_host_platform`.

- [ ] **Step 3: Implement**

Append to `src/argus/adapters/desktop.py`:

```python
def _host_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _display_remediation() -> str:
    if sys.platform == "darwin":
        return (
            "Grant your terminal Screen Recording and Accessibility permission "
            "(System Settings > Privacy & Security), then re-run."
        )
    if sys.platform == "win32":
        return "Run from an interactive desktop session (not a service), at the app's integrity level."
    return (
        "Desktop devices need an X11 display: set DISPLAY (or run under Xvfb / XWayland) "
        "and install scrot + python3-tk for pyautogui."
    )


class DesktopAdapter(Device):
    """Controls a native desktop application through pyautogui and a subprocess."""

    def __init__(
        self,
        name: str,
        *,
        command: str,
        args: Sequence[str] = (),
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        startup_wait: float = 0.0,
        stop_timeout: float = 5.0,
        reset_command: str | None = None,
        region: tuple[int, int, int, int] | None = None,
        platform: str | None = None,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        super().__init__(name)
        self._command = command
        self._args = list(args)
        self._cwd = cwd
        self._env = dict(env) if env else None
        self._startup_wait = float(startup_wait)
        self._stop_timeout = float(stop_timeout)
        self._reset_command = reset_command
        self._region = region
        self._platform = platform or _host_platform()
        self._backend_factory: BackendFactory = backend_factory or _pyautogui_backend
        self._backend: DesktopBackend | None = None
        self._process: _ProcessHandle | None = None
        self._logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)
        self._ratio: float | None = None
        self._screen_info: ScreenInfo | None = None
        self._log = get_logger("argus.desktop", device=name)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> DesktopAdapter:
        options: dict[str, Any] = config.options
        command = options.get("command")
        if not command:
            raise ConfigurationError(
                f"Desktop device {name!r} needs a command.",
                remediation="Set devices.<name>.command to the application executable.",
            )
        region: tuple[int, int, int, int] | None = None
        raw_region = options.get("region")
        if raw_region is not None:
            if not isinstance(raw_region, list | tuple) or len(raw_region) != 4:
                raise ConfigurationError(
                    f"Desktop device {name!r}: region must be [x, y, width, height].",
                    remediation="Example: region: [0, 0, 1920, 1080]",
                )
            region = (int(raw_region[0]), int(raw_region[1]), int(raw_region[2]), int(raw_region[3]))
        env = options.get("env")
        return cls(
            name,
            command=str(command),
            args=[str(a) for a in options.get("args", [])],
            cwd=options.get("cwd"),
            env={str(k): str(v) for k, v in env.items()} if env else None,
            startup_wait=parse_duration(options.get("startup_wait", "0s")),
            stop_timeout=parse_duration(options.get("stop_timeout", "5s")),
            reset_command=options.get("reset_command"),
            region=region,
            platform=config.effective_platform if config.platform else None,
        )

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_screenshot=True,
            supports_tap=True,
            supports_swipe=True,
            supports_long_press=True,
            supports_drag=True,
            supports_multi_touch=False,
            supports_keyboard=True,
            supports_app_lifecycle=True,
            supports_logs=True,
        )

    @property
    def platform(self) -> str:
        return self._platform

    # -- connection -------------------------------------------------------------------------

    def _require_backend(self) -> DesktopBackend:
        if self._backend is None:
            raise DeviceConnectionError(
                f"Desktop device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        return self._backend

    def _probe(self) -> tuple[int, int]:
        backend = self._backend_factory()
        try:
            width, height = backend.size()
        except Exception as exc:  # noqa: BLE001 - pyautogui raises assorted backend errors
            raise DeviceConnectionError(
                f"Desktop device {self.name!r}: no display available ({exc}).",
                remediation=_display_remediation(),
            ) from exc
        self._backend = backend
        return int(width), int(height)

    def connect(self) -> None:
        self._probe()
        self._ratio = None
        self._screen_info = None

    def disconnect(self) -> None:
        if self._process is not None and self._process.running:
            self.stop_application()
        self._backend = None

    def is_available(self) -> bool:
        try:
            self._probe()
        except DeviceConnectionError:
            return False
        return True

    def health_check(self) -> HealthCheckResult:
        try:
            width, height = self._probe()
        except DeviceConnectionError as exc:
            return HealthCheckResult.failed(str(exc))
        return HealthCheckResult.ok(
            "Desktop display available",
            screen=f"{width}x{height}",
            platform=self._platform,
            app_running=self.is_application_running(),
        )

    # -- application lifecycle ----------------------------------------------------------

    def is_application_running(self) -> bool:
        return self._process is not None and self._process.running
```

Note on `from_config`'s `platform`: when the config has no explicit `platform`, `effective_platform` returns the type (`"desktop"`), which is not what we want — hence `if config.platform else None` so the host default applies. Check the exact attribute names with `grep -n "def effective_platform" -A 4 src/argus/config/models.py` and adjust if the property differs.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check src/argus/adapters/desktop.py tests/unit/test_desktop_adapter.py` — only unused-import warnings for `os`, `time`, `Point`, `DeviceCapabilityError`, `ScreenshotError` (Tasks 3–6) may remain.

```bash
git add src/argus/adapters/desktop.py tests/unit/test_desktop_adapter.py
git commit -m "Add DesktopAdapter configuration, platform detection and connection

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Application lifecycle and logs

**Files:**
- Modify: `src/argus/adapters/desktop.py`
- Test: `tests/unit/test_desktop_adapter.py`

**Interfaces:**
- Consumes: `_ProcessHandle`, `_logs`, `_process`, `_startup_wait`, `_stop_timeout`, `_reset_command`, `_cwd`, `_env`.
- Produces: `start_application`, `stop_application`, `reset_application`, `get_logs`.

- [ ] **Step 1: Write the failing tests**

```python
class TestLifecycle:
    def test_start_captures_logs_and_stop_terminates(self, adapter):
        adapter.connect()
        adapter.start_application()
        try:
            assert adapter.is_application_running()
            assert _wait_for(lambda: "warning: something" in adapter.get_logs())
            assert adapter.get_logs(1) == "warning: something"
            assert adapter.get_logs(0) == ""
        finally:
            adapter.stop_application()
        assert not adapter.is_application_running()

    def test_start_clears_previous_logs_and_waits(self, backend, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr("argus.adapters.desktop.time.sleep", sleeps.append)
        adapter = DesktopAdapter(
            "app", command=sys.executable, args=["-c", _CHILD], startup_wait=1.5,
            backend_factory=lambda: backend,
        )
        adapter.connect()
        adapter._logs.append("stale")
        adapter.start_application()
        try:
            assert "stale" not in adapter.get_logs()
            assert sleeps == [1.5]
        finally:
            adapter.stop_application()

    def test_start_twice_restarts(self, adapter):
        adapter.connect()
        adapter.start_application()
        first = adapter._process.pid
        adapter.start_application()
        try:
            assert adapter._process.pid != first
        finally:
            adapter.stop_application()

    def test_env_merges_over_os_environ(self, backend, monkeypatch):
        monkeypatch.setenv("ARGUS_BASE", "base")
        script = "import os; print(os.environ['ARGUS_BASE'], os.environ['ARGUS_EXTRA'], flush=True)"
        adapter = DesktopAdapter(
            "app", command=sys.executable, args=["-c", script], env={"ARGUS_EXTRA": "extra"},
            backend_factory=lambda: backend,
        )
        adapter.connect()
        adapter.start_application()
        assert _wait_for(lambda: "base extra" in adapter.get_logs())
        adapter.stop_application()

    def test_reset_runs_reset_command_between_stop_and_start(self, backend, tmp_path):
        marker = tmp_path / "reset.txt"
        reset = f"{sys.executable} -c \"open(r'{marker}', 'w').write('x')\""
        adapter = DesktopAdapter(
            "app", command=sys.executable, args=["-c", _CHILD], reset_command=reset,
            backend_factory=lambda: backend,
        )
        adapter.connect()
        adapter.start_application()
        first = adapter._process.pid
        adapter.reset_application()
        try:
            assert marker.exists()
            assert adapter._process.pid != first
            assert adapter.is_application_running()
        finally:
            adapter.stop_application()

    def test_reset_command_failure_is_connection_error(self, backend):
        adapter = DesktopAdapter(
            "app", command=sys.executable, args=["-c", _CHILD],
            reset_command=f"{sys.executable} -c \"import sys; sys.exit(3)\"",
            backend_factory=lambda: backend,
        )
        adapter.connect()
        with pytest.raises(DeviceConnectionError, match="exit 3"):
            adapter.reset_application()
        assert not adapter.is_application_running()

    def test_stop_when_not_running_is_noop(self, adapter):
        adapter.connect()
        adapter.stop_application()
        assert not adapter.is_application_running()

    def test_disconnect_stops_running_app(self, adapter):
        adapter.connect()
        adapter.start_application()
        adapter.disconnect()
        assert not adapter.is_application_running()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider -k Lifecycle`
Expected: FAIL with `does not support 'start_application'`.

- [ ] **Step 3: Implement**

Add to `DesktopAdapter` after `is_application_running`:

```python
    def start_application(self) -> None:
        self._require_backend()
        if self._process is not None and self._process.running:
            self.stop_application()
        env = {**os.environ, **self._env} if self._env else None
        self._logs.clear()
        self._process = _ProcessHandle(
            [self._command, *self._args], cwd=self._cwd, env=env, sink=self._logs
        )
        self._log.info("Launched %s (pid %d)", self._command, self._process.pid)
        if self._startup_wait > 0:
            time.sleep(self._startup_wait)

    def stop_application(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.stop(timeout=self._stop_timeout)

    def reset_application(self) -> None:
        self.stop_application()
        if self._reset_command:
            completed = subprocess.run(
                self._reset_command,
                shell=True,
                cwd=self._cwd,
                capture_output=True,
                timeout=max(self._stop_timeout, 30.0),
                check=False,
            )
            if completed.returncode != 0:
                stderr = completed.stderr.decode(errors="replace").strip()
                raise DeviceConnectionError(
                    f"reset_command failed (exit {completed.returncode}): {stderr}",
                    remediation="Check devices.<name>.reset_command runs cleanly by hand.",
                )
        self.start_application()

    # -- observation -------------------------------------------------------------------------

    def get_logs(self, lines: int = 200) -> str:
        if lines <= 0:
            return ""
        return "\n".join(list(self._logs)[-lines:])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider`
Expected: all pass, no leaked processes (the run must finish promptly).

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/desktop.py tests/unit/test_desktop_adapter.py
git commit -m "Add desktop application lifecycle and process logs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Screenshot, region and pixel ratio

**Files:**
- Modify: `src/argus/adapters/desktop.py`
- Test: `tests/unit/test_desktop_adapter.py`

**Interfaces:**
- Produces: `screenshot() -> Image`, `get_screen_info() -> ScreenInfo`, `_pixel_ratio() -> float`, `_to_logical(point: Point) -> tuple[float, float]`.

- [ ] **Step 1: Write the failing tests**

```python
class TestObservation:
    def test_screenshot_is_rgb_full_screen(self, adapter, backend):
        adapter.connect()
        img = adapter.screenshot()
        assert img.mode == "RGB" and img.size == (800, 600)

    def test_region_crops_screenshot(self, backend):
        adapter = DesktopAdapter(
            "app", command="x", region=(10, 20, 300, 200), backend_factory=lambda: backend
        )
        adapter.connect()
        assert adapter.screenshot().size == (300, 200)
        info = adapter.get_screen_info()
        assert (info.width, info.height) == (300, 200)

    def test_region_outside_screen_is_error(self, backend):
        adapter = DesktopAdapter(
            "app", command="x", region=(700, 500, 300, 200), backend_factory=lambda: backend
        )
        adapter.connect()
        with pytest.raises(ScreenshotError, match="region"):
            adapter.screenshot()

    def test_hidpi_ratio_from_screenshot_vs_logical(self):
        backend = FakeBackend(logical=(800, 600), pixels=(1600, 1200))
        adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        adapter.connect()
        assert adapter._pixel_ratio() == 2.0
        assert adapter._to_logical((200, 100)) == (100, 50)
        adapter._pixel_ratio()
        assert backend.calls.count(("screenshot", ())) == 1  # ratio is cached
        info = adapter.get_screen_info()
        assert (info.width, info.height, info.scale) == (1600, 1200, 2.0)

    def test_to_logical_adds_region_offset(self):
        backend = FakeBackend(logical=(800, 600), pixels=(1600, 1200))
        adapter = DesktopAdapter(
            "app", command="x", region=(100, 40, 400, 400), backend_factory=lambda: backend
        )
        adapter.connect()
        # pixel (10, 10) inside the region = pixel (110, 50) on screen = logical (55, 25)
        assert adapter._to_logical((10, 10)) == (55, 25)

    def test_black_screenshot_on_macos_mentions_permission(self, monkeypatch, backend):
        monkeypatch.setattr(sys, "platform", "darwin")
        backend.fill = (0, 0, 0)
        adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        adapter.connect()
        with pytest.raises(ScreenshotError, match="Screen Recording"):
            adapter.screenshot()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider -k Observation`
Expected: FAIL with `does not support 'screenshot'` / `AttributeError`.

- [ ] **Step 3: Implement**

Add to `DesktopAdapter` under the observation section (before `get_logs`):

```python
    def _grab(self) -> Image:
        backend = self._require_backend()
        try:
            image = backend.screenshot()
        except Exception as exc:  # noqa: BLE001 - pyautogui/backend specific errors
            raise ScreenshotError(
                f"Desktop screenshot failed: {exc}", remediation=_display_remediation()
            ) from exc
        image = image.convert("RGB")
        if sys.platform == "darwin" and image.getbbox() is None:
            # PIL's getbbox() is None for an all-black image: macOS without Screen Recording
            # permission returns exactly that instead of failing.
            raise ScreenshotError(
                "Desktop screenshot is entirely black.",
                remediation="Grant your terminal Screen Recording permission "
                "(System Settings > Privacy & Security > Screen Recording).",
            )
        return image

    def screenshot(self) -> Image:
        image = self._grab()
        if self._region is None:
            return image
        x, y, width, height = self._region
        if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
            raise ScreenshotError(
                f"Desktop device {self.name!r}: region {self._region} exceeds the "
                f"{image.width}x{image.height} screenshot.",
                remediation="Adjust devices.<name>.region to lie within the screen.",
            )
        return image.crop((x, y, x + width, y + height))

    def _pixel_ratio(self) -> float:
        if self._ratio is None:
            logical_width, _ = self._require_backend().size()
            full = self._grab()
            self._ratio = full.width / logical_width if logical_width > 0 else 1.0
        return self._ratio

    def get_screen_info(self) -> ScreenInfo:
        if self._screen_info is None:
            ratio = self._pixel_ratio()
            image = self.screenshot()
            self._screen_info = ScreenInfo(width=image.width, height=image.height, scale=ratio)
        return self._screen_info

    def _to_logical(self, point: Point) -> tuple[float, float]:
        """Screenshot pixel (inside ``region`` if set) -> pyautogui logical coordinate."""
        ratio = self._pixel_ratio()
        offset_x, offset_y = (self._region[0], self._region[1]) if self._region else (0, 0)
        return ((point[0] + offset_x) / ratio, (point[1] + offset_y) / ratio)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/desktop.py tests/unit/test_desktop_adapter.py
git commit -m "Add desktop screenshots with region crop and HiDPI pixel ratio

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Mouse gestures

**Files:**
- Modify: `src/argus/adapters/desktop.py`
- Test: `tests/unit/test_desktop_adapter.py`

**Interfaces:**
- Consumes: `_to_logical`, `_require_backend`.
- Produces: `tap`, `swipe`, `long_press`, `drag`, `multi_touch` (raises), `pinch` (raises — override so the base-class pinch does not reach `multi_touch` with a misleading message).

- [ ] **Step 1: Write the failing tests**

```python
class TestGestures:
    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        self.sleeps: list[float] = []
        monkeypatch.setattr("argus.adapters.desktop.time.sleep", self.sleeps.append)

    def test_tap_clicks_logical_coordinates(self):
        backend = FakeBackend(logical=(800, 600), pixels=(1600, 1200))
        adapter = DesktopAdapter("app", command="x", backend_factory=lambda: backend)
        adapter.connect()
        adapter.tap(200, 100)
        assert backend.calls[-1] == ("click", (100, 50))

    def test_swipe_is_press_move_release(self, adapter, backend):
        adapter.connect()
        adapter.swipe(0, 0, 100, 50, duration_ms=300)
        assert backend.calls[-3:] == [
            ("mouseDown", (0, 0)),
            ("moveTo", (100, 50, 0.3)),
            ("mouseUp", ()),
        ]
        assert self.sleeps == []

    def test_long_press_holds(self, adapter, backend):
        adapter.connect()
        adapter.long_press(10, 20, duration_ms=1500)
        assert backend.calls[-2:] == [("mouseDown", (10, 20)), ("mouseUp", ())]
        assert self.sleeps == [1.5]

    def test_drag_holds_then_moves(self, adapter, backend):
        adapter.connect()
        adapter.drag(1, 2, 3, 4, hold_ms=250, duration_ms=500)
        assert backend.calls[-3:] == [
            ("mouseDown", (1, 2)),
            ("moveTo", (3, 4, 0.5)),
            ("mouseUp", ()),
        ]
        assert self.sleeps == [0.25]

    def test_multi_touch_and_pinch_unsupported(self, adapter):
        adapter.connect()
        with pytest.raises(DeviceCapabilityError, match="touch injection"):
            adapter.multi_touch([[(0, 0), (1, 1)]])
        with pytest.raises(DeviceCapabilityError, match="Ctrl\\+Plus"):
            adapter.pinch(100, 100, 50, 100)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider -k Gestures`
Expected: FAIL (`does not support 'tap'`; pinch raises the base-class message "does not support 'multi_touch'" without "Ctrl+Plus").

- [ ] **Step 3: Implement**

Add to `DesktopAdapter`:

```python
    # -- input --------------------------------------------------------------------------------

    def _no_touch(self, operation: str) -> DeviceCapabilityError:
        return DeviceCapabilityError(
            f"Desktop device {self.name!r} cannot {operation}: desktop has no touch injection.",
            remediation="Zoom with the keyboard instead, e.g. device.key: Ctrl+Plus "
            "(Cmd+Plus on macOS).",
        )

    def tap(self, x: int, y: int) -> None:
        backend = self._require_backend()
        backend.click(*self._to_logical((x, y)))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        backend = self._require_backend()
        backend.mouseDown(*self._to_logical((x1, y1)))
        backend.moveTo(*self._to_logical((x2, y2)), duration=duration_ms / 1000)
        backend.mouseUp()

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        backend = self._require_backend()
        backend.mouseDown(*self._to_logical((x, y)))
        time.sleep(duration_ms / 1000)
        backend.mouseUp()

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, hold_ms: int = 500, duration_ms: int = 500
    ) -> None:
        backend = self._require_backend()
        backend.mouseDown(*self._to_logical((x1, y1)))
        time.sleep(hold_ms / 1000)
        backend.moveTo(*self._to_logical((x2, y2)), duration=duration_ms / 1000)
        backend.mouseUp()

    def multi_touch(self, fingers: Sequence[Sequence[Point]], duration_ms: int = 500) -> None:
        raise self._no_touch("multi_touch")

    def pinch(
        self, cx: int, cy: int, start_distance: int, end_distance: int, duration_ms: int = 500
    ) -> None:
        raise self._no_touch("pinch")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/desktop.py tests/unit/test_desktop_adapter.py
git commit -m "Add desktop mouse gestures and explicit no-touch errors

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Key input

**Files:**
- Modify: `src/argus/adapters/desktop.py`
- Test: `tests/unit/test_desktop_adapter.py`

**Interfaces:**
- Produces: `press_key(key: str) -> None`; module table `_KEY_MAP`; helper `_chord(key) -> list[str] | None`.

- [ ] **Step 1: Write the failing tests**

```python
class TestKeys:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("ENTER", ("press", ("enter",))),
            ("KEYCODE_BACK", ("press", ("escape",))),
            ("DPAD_LEFT", ("press", ("left",))),
            ("PAGE_DOWN", ("press", ("pagedown",))),
            ("HOME", ("press", ("home",))),
            ("a", ("press", ("a",))),
            ("f5", ("press", ("f5",))),
            ("Ctrl+Plus", ("hotkey", ("ctrl", "+"))),
            ("Cmd+Shift+t", ("hotkey", ("command", "shift", "t"))),
            ("ctrl+alt+delete", ("hotkey", ("ctrl", "alt", "delete"))),
        ],
    )
    def test_press_key_mapping(self, adapter, backend, key, expected):
        adapter.connect()
        adapter.press_key(key)
        assert backend.calls[-1] == expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider -k Keys`
Expected: FAIL with `does not support 'press_key'`.

- [ ] **Step 3: Implement**

Module-level (after `_INSTALL`):

```python
# Android-style names -> pyautogui key names; anything else passes through lower-cased.
_KEY_MAP = {
    "ENTER": "enter",
    "DPAD_CENTER": "enter",
    "BACK": "escape",
    "ESCAPE": "escape",
    "TAB": "tab",
    "SPACE": "space",
    "DEL": "backspace",
    "BACKSPACE": "backspace",
    "DPAD_UP": "up",
    "DPAD_DOWN": "down",
    "DPAD_LEFT": "left",
    "DPAD_RIGHT": "right",
    "UP": "up",
    "DOWN": "down",
    "LEFT": "left",
    "RIGHT": "right",
    "HOME": "home",
    "END": "end",
    "PAGE_UP": "pageup",
    "PAGE_DOWN": "pagedown",
    "PLUS": "+",
    "MINUS": "-",
}
_MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt",
              "shift": "shift", "cmd": "command", "command": "command", "win": "win",
              "super": "win", "meta": "command"}


def _map_key(name: str) -> str:
    stripped = name.removeprefix("KEYCODE_")
    if len(stripped) == 1:
        return stripped
    return _KEY_MAP.get(stripped.upper(), stripped.lower())


def _chord(key: str) -> list[str] | None:
    """``Ctrl+Shift+t`` -> ['ctrl', 'shift', 't']; None when ``key`` is not a chord."""
    if "+" not in key or key == "+":
        return None
    parts = key.split("+")
    # A trailing empty part means the literal '+' key: "Ctrl+Plus" is spelled "Ctrl++" too.
    if parts[-1] == "":
        parts = parts[:-1] + ["+"]
    if len(parts) < 2:
        return None
    return [_MODIFIERS.get(p.lower(), _map_key(p)) for p in parts]
```

Method on `DesktopAdapter`:

```python
    def press_key(self, key: str) -> None:
        backend = self._require_backend()
        chord = _chord(key)
        if chord is not None:
            backend.hotkey(*chord)
        else:
            backend.press(_map_key(key))
```

- [ ] **Step 4: Run the tests and lint**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider && .venv/bin/ruff check src/argus/adapters/desktop.py tests/unit/test_desktop_adapter.py && .venv/bin/mypy src/argus/adapters/desktop.py`
Expected: all pass; ruff and mypy clean (remove any import that ended up unused).

- [ ] **Step 5: Commit**

```bash
git add src/argus/adapters/desktop.py tests/unit/test_desktop_adapter.py
git commit -m "Add desktop key input with chords and Android-style names

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Registry, extra, integration test, docs

**Files:**
- Modify: `src/argus/adapters/registry.py` (`register_builtin_devices`), `pyproject.toml:38-54`
- Create: `tests/integration/test_desktop_adapter_e2e.py`, `docs/desktop.md`
- Modify: `docs/adapters.md` (built-in table, after the `browser` row), `docs/configuration.md:47`, `docs/getting-started.md:48`, `docs/test-authoring.md:83-87`, `README.md` (~line 30 "Supported today" and the docs table near line 101), `CHANGELOG.md` (Unreleased → Added, first bullet)
- Test: `tests/unit/test_desktop_adapter.py`

- [ ] **Step 1: Write the failing registry test**

```python
class TestRegistry:
    def test_registered_as_desktop(self):
        from argus.adapters.registry import DeviceRegistry

        registry = DeviceRegistry()
        assert "desktop" in registry.types()
        device = registry.create(
            "app", DeviceConfig.model_validate({"type": "desktop", "command": "x"})
        )
        assert isinstance(device, DesktopAdapter)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider -k Registry`
Expected: FAIL `assert 'desktop' in [...]`.

- [ ] **Step 3: Register and add the extra**

In `register_builtin_devices`: add `from argus.adapters.desktop import DesktopAdapter` after the `browser` import; add `registry.register("desktop", DesktopAdapter.from_config)` after the `browser` registration. In `pyproject.toml` add `desktop = ["pyautogui>=0.9.54"]` after the `browser` extra and extend `all` to `argus[yocto,ocr,browser,desktop,appletv,esp32]`. Do NOT add pyautogui to `dev`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_desktop_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Integration test**

Create `tests/integration/test_desktop_adapter_e2e.py`:

```python
"""DesktopAdapter against the real display. Skipped without pyautogui or a display."""

from __future__ import annotations

import sys

import pytest

from argus.adapters.desktop import DesktopAdapter
from argus.exceptions import DeviceConnectionError

pytestmark = pytest.mark.integration


@pytest.fixture
def device():
    pytest.importorskip("pyautogui")
    adapter = DesktopAdapter(
        "app", command=sys.executable, args=["-c", "import time; time.sleep(10)"]
    )
    try:
        adapter.connect()
    except DeviceConnectionError as exc:
        pytest.skip(f"no desktop display: {exc}")
    yield adapter
    adapter.disconnect()


def test_screenshot_matches_screen_info(device: DesktopAdapter):
    img = device.screenshot()
    info = device.get_screen_info()
    assert img.size == (info.width, info.height)
    assert info.scale and info.scale >= 1.0
    device.start_application()
    assert device.is_application_running()
```

Run: `.venv/bin/python -m pytest tests/integration/test_desktop_adapter_e2e.py -q -p no:cacheprovider` → expected `1 skipped` (pyautogui absent).

- [ ] **Step 6: Write `docs/desktop.md`**

```markdown
# Desktop (Windows / Linux / macOS)

The `desktop` adapter drives a native application on the machine running
Argus: it launches the app as a process, screenshots the display with
[pyautogui](https://pyautogui.readthedocs.io/), sends mouse and keyboard
input, and captures the process's stdout/stderr as device logs.

| Operation | Implementation |
| --- | --- |
| Connect | import `pyautogui`, read the screen size (fails with an OS-specific remediation when there is no display / permission) |
| Start app | `Popen([command, *args])` with `cwd`/`env`, then `startup_wait` |
| Stop app | `terminate()`, wait `stop_timeout`, then `kill()` |
| Reset app | stop → `reset_command` (shell) → start |
| App running? | process alive |
| Screenshot | `pyautogui.screenshot()`, cropped to `region` if set |
| Screen size | screenshot pixels; `scale` = screenshot width ÷ logical width (HiDPI) |
| Tap | `click` |
| Swipe | `mouseDown` → `moveTo(duration)` → `mouseUp` |
| Long press | `mouseDown` → hold → `mouseUp` |
| Drag | `mouseDown` → hold → `moveTo(duration)` → `mouseUp` |
| Pinch / multi-touch | **unsupported** — no portable touch injection; zoom with `device.key: Ctrl+Plus` (`Cmd+Plus` on macOS) |
| Keys | `press` for single keys (Android names map: `BACK` → `escape`, `DPAD_*` → arrows); `hotkey` for chords like `Ctrl+Shift+t` |
| Logs | process stdout + stderr |

Coordinates in tests are **screenshot pixels** (inside `region` when set),
as on every other adapter; the adapter converts to pyautogui's logical
coordinates on HiDPI displays.

## Prerequisites

- `pip install "argus[desktop]"`.
- **Linux (Ubuntu):** an X11 session with `DISPLAY` set, and
  `sudo apt install scrot python3-tk python3-dev`. Wayland sessions must run
  Argus under XWayland or a virtual display (`xvfb-run argus run ...`).
- **macOS:** grant your terminal **Screen Recording** and **Accessibility**
  permission (System Settings → Privacy & Security). Without Screen
  Recording, screenshots come back black and the adapter reports it.
- **Windows:** nothing extra; run the terminal at the same integrity level
  as the app (an elevated app ignores input from a non-elevated terminal).

## Configuration

```yaml
devices:
  desktop_app:
    type: desktop
    platform: linux                      # windows | linux | macos; defaults to the host OS
    command: ./build/ExampleApp          # required; executable or script
    args: ["--fullscreen"]               # optional
    cwd: ./build                         # optional
    env: {EXAMPLE_ENV: "1"}              # optional, merged over the environment
    startup_wait: 2s                     # optional, sleep after launch
    stop_timeout: 5s                     # optional, terminate → kill grace
    reset_command: ./scripts/reset.sh    # optional, run between stop and start
    region: [0, 0, 1920, 1080]           # optional, screenshot crop [x, y, w, h] in pixels
```

One configuration file can serve all three OSes by using `${VAR}`
placeholders for `command` and setting `platforms: [windows, linux, macos]`
in tests; `platform:` selects which entry a run uses.

## Gestures

`device.tap`, `device.swipe`, `device.long_press` and `device.drag` work
(as mouse actions). `device.pinch` and `device.multi_touch` fail with a
`DeviceCapabilityError`; use keyboard zoom instead:

```yaml
- action: device.key
  key: Ctrl+Plus
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `pyautogui is not installed` | `pip install "argus[desktop]"` |
| `no display available` on Linux | set `DISPLAY`, or run under `xvfb-run` |
| screenshot entirely black (macOS) | grant Screen Recording permission, restart the terminal |
| input ignored (macOS) | grant Accessibility permission |
| input ignored (Windows) | run the terminal with the same elevation as the app |
| `region ... exceeds the screenshot` | the crop must lie inside the screen in **pixels**, not logical points |
| `Application executable not found` | `command` is resolved relative to `cwd` (or the current directory) |
```

- [ ] **Step 7: Cross-references**

- `docs/adapters.md`: after the `browser` row add
  `| \`desktop\` | pyautogui + subprocess | native apps on Windows / Linux / macOS (mouse, keyboard, process logs), see [desktop.md](desktop.md) |`.
- `docs/configuration.md:47`: type comment → `android | ios | desktop | yocto | browser | roku | tvos_sim | appletv | esp32 | fake | plugin-provided`.
- `docs/getting-started.md:48`: add `[desktop.md](desktop.md)` to the list.
- `docs/test-authoring.md:83-87`: change "long press and drag additionally on the browser adapter (mouse-driven)" to "long press and drag additionally on the browser and desktop adapters (mouse-driven)".
- `README.md` "Supported today" paragraph: add **desktop apps** (Windows / Linux / macOS via pyautogui) after web browsers; docs table: `| Desktop setup | [docs/desktop.md](docs/desktop.md) |` after the iOS row.
- `CHANGELOG.md` Unreleased → Added, first bullet:
  `- \`desktop\` device adapter (optional \`argus[desktop]\` extra, pyautogui): native applications on Windows, Linux and macOS — launch/stop/reset as a subprocess with stdout/stderr as logs, screenshots (optional \`region\` crop, HiDPI-aware pixel coordinates), mouse tap/swipe/long-press/drag, keyboard incl. \`Ctrl+Shift+x\` chords. Platform label defaults to the host OS (\`windows\` / \`linux\` / \`macos\`).`

- [ ] **Step 8: Full verification**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | grep -E "^FAILED" | sed 's/::.*//' | sort | uniq -c` → only `6 tests/unit/test_console_reporter.py` and `5 tests/unit/test_text_verifiers.py`.
Run: `.venv/bin/ruff check src tests` → only the 3 pre-existing errors.
Run: `.venv/bin/mypy src` → only the 2 pre-existing errors.
Run: `.venv/bin/python -c "import argus.adapters.desktop"` → no output (pyautogui absent is fine).

- [ ] **Step 9: Commit**

```bash
git add src/argus/adapters/registry.py pyproject.toml tests/unit/test_desktop_adapter.py tests/integration/test_desktop_adapter_e2e.py docs/desktop.md docs/adapters.md docs/configuration.md docs/getting-started.md docs/test-authoring.md README.md CHANGELOG.md
git commit -m "Register the desktop adapter and document it

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review against the spec (section 2)

- Prerequisites per OS in docs — Task 7 (`docs/desktop.md`). ✔
- Configuration keys (`command` required, `args`, `cwd`, `env`, `startup_wait`, `stop_timeout`, `reset_command`, `region`), `platform` derived from `sys.platform`, `ConfigurationError` on missing `command` — Task 2. ✔
- `DesktopBackend` protocol with lazy pyautogui import, `FAILSAFE=False`, `PAUSE=0`, `backend_factory` injection — Task 1/2. ✔
- `_ProcessHandle` with merged stdout/stderr pumped by a daemon thread — Task 1. ✔
- Operation mapping: connect/disconnect/is_available/health_check — Task 2; lifecycle, `reset_command` failure → `DeviceConnectionError`, `is_application_running`, `get_logs` — Task 3; screenshot with `region` crop, black-on-macOS `ScreenshotError`, `get_screen_info`, ratio, `px()` (`_to_logical`) — Task 4; tap/swipe/long_press/drag, multi_touch/pinch → `DeviceCapabilityError` with keyboard-zoom remediation — Task 5; press_key chords + Android names — Task 6. ✔
- Capabilities — Task 2. ✔
- Preflight: existing `DeviceCheck`/`DeviceScreenshotCheck`; remediations in `connect()` — Task 2. ✔
- Tests listed in the spec: fake backend with configurable sizes (2× ratio), real child process lifecycle (logs, running, terminate/kill, reset failure), gesture call sequences, unsupported touch, key mapping, region crop, missing command, missing pyautogui remediation, platform derivation, registry, from_config, integration test — Tasks 1–7. ✔
- Docs: `docs/desktop.md`, adapters table, README, configuration, getting-started, test-authoring, CHANGELOG, `pyproject.toml` extra in `all` — Task 7. ✔
