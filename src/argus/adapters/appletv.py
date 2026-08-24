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

# Android-style key names -> pyatv RemoteControl method names. Only the
# mapped method names are accepted.
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
        thread = threading.Thread(
            target=loop.run_forever, daemon=True, name=f"appletv-{self.name}"
        )
        thread.start()
        self._loop, self._thread = loop, thread
        return loop

    def _stop_loop(self) -> None:
        loop, self._loop = self._loop, None
        thread, self._thread = self._thread, None
        if loop is None:
            return

        def _cancel_pending() -> None:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                gather = asyncio.gather(*pending, return_exceptions=True)
                loop.create_task(_await_then_stop(gather))
            else:
                loop.stop()

        async def _await_then_stop(gather: Any) -> None:
            with contextlib.suppress(Exception):
                await gather
            loop.stop()

        loop.call_soon_threadsafe(_cancel_pending)
        if thread is not None:
            thread.join(timeout=2.0)
        with contextlib.suppress(Exception):
            loop.run_until_complete(loop.shutdown_asyncgens())
        with contextlib.suppress(Exception):
            loop.close()

    def _run(self, coro: Awaitable[Any]) -> Any:
        if self._loop is None:
            raise DeviceConnectionError(
                f"Apple TV device {self.name!r} is not connected.",
                remediation="Call connect() (RunSession does this automatically).",
            )
        future: concurrent.futures.Future[Any] = asyncio.run_coroutine_threadsafe(
            coro, self._loop  # type: ignore[arg-type]
        )
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
                remediation=(
                    "Use DPAD_*, ENTER, BACK/MENU, HOME, MEDIA_*, VOLUME_* or one of "
                    "the pyatv method names: up, down, left, right, select, menu, home, "
                    "play, pause, play_pause, stop, next, previous, volume_up, volume_down"
                ),
            )
        self._run(getattr(atv.remote_control, method)())
