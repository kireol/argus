"""TestContext — everything a test needs, dependency-injected.

No globals, no singletons: the runner builds one context per test from the
session's shared services (device pool, backend, asset cache) plus per-test
state (variables, artifacts, logger).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from argus.adapters.backend import BackendAdapter
from argus.adapters.base import Device
from argus.artifacts.manager import TestArtifacts
from argus.config.models import AppConfig
from argus.events.bus import EventBus
from argus.exceptions import ConfigurationError, TestExecutionError
from argus.instrumentation.client import InstrumentationClient
from argus.logging import ContextLogger, get_logger
from argus.models.common import ScreenInfo
from argus.models.observation import Observation
from argus.models.test_definition import TestDefinition
from argus.utilities.variables import expand_variables

if TYPE_CHECKING:
    from argus.conditions.base import ConditionFactory
    from argus.ocr.base import OCRProvider
    from argus.verifiers.assets import AssetStore
    from argus.verifiers.base import Verifier


@dataclass
class VerifierBundle:
    """Lazily-constructed verifier instances shared per session."""

    assets: AssetStore
    config: AppConfig
    _ocr: OCRProvider | None = None
    _cache: dict[str, Verifier] = field(default_factory=dict)

    @property
    def ocr(self) -> OCRProvider:
        if self._ocr is None:
            from argus.ocr.base import create_ocr_provider

            self._ocr = create_ocr_provider(self.config.ocr)
        return self._ocr

    @property
    def image_present(self) -> Verifier:
        return self._get("image_present")

    @property
    def image_absent(self) -> Verifier:
        return self._get("image_absent")

    @property
    def screenshot_match(self) -> Verifier:
        return self._get("screenshot_match")

    @property
    def text_present(self) -> Verifier:
        return self._get("text_present")

    @property
    def text_absent(self) -> Verifier:
        return self._get("text_absent")

    def _get(self, name: str) -> Verifier:
        verifier = self._cache.get(name)
        if verifier is not None:
            return verifier

        image_cfg = self.config.verification.image
        if name == "image_present":
            from argus.verifiers.image import ImagePresentVerifier

            verifier = ImagePresentVerifier(self.assets, image_cfg)
        elif name == "image_absent":
            from argus.verifiers.image import ImageAbsentVerifier

            verifier = ImageAbsentVerifier(self.assets, image_cfg)
        elif name == "screenshot_match":
            from argus.verifiers.image import ScreenshotMatchVerifier

            verifier = ScreenshotMatchVerifier(self.assets, image_cfg)
        elif name == "text_present":
            from argus.verifiers.text import TextPresentVerifier

            verifier = TextPresentVerifier(self.ocr)
        elif name == "text_absent":
            from argus.verifiers.text import TextAbsentVerifier

            verifier = TextAbsentVerifier(self.ocr)
        else:  # pragma: no cover - defensive
            raise ConfigurationError(f"Unknown verifier {name!r}")

        self._cache[name] = verifier
        return verifier


@dataclass
class TestContext:
    """Per-test execution context."""

    config: AppConfig
    test: TestDefinition
    conditions: ConditionFactory
    verifiers: VerifierBundle
    events: EventBus
    artifacts: TestArtifacts
    logger: ContextLogger = field(default_factory=lambda: get_logger("argus.test"))
    platform: str | None = None
    device: Device | None = None
    backend: BackendAdapter | None = None
    instrumentation: InstrumentationClient | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)

    # -- dependency access with clear errors ------------------------------------

    def require_device(self) -> Device:
        if self.device is None:
            raise TestExecutionError(
                f"Test {self.test.id} needs a device but none is available "
                f"for platform {self.platform or '<any>'}.",
                remediation="Configure a device for this platform in configuration.",
            )
        return self.device

    def require_backend(self) -> BackendAdapter:
        if self.backend is None:
            raise TestExecutionError(
                f"Test {self.test.id} uses backend actions but the backend "
                "is not configured.",
                remediation="Set backend.base_url in configuration.",
            )
        return self.backend

    def require_instrumentation(self) -> InstrumentationClient:
        if self.instrumentation is None:
            raise TestExecutionError(
                f"Test {self.test.id} uses instrumentation but the device "
                f"{self.device.name if self.device else '<none>'} has no "
                "instrumentation configured.",
                remediation="Set devices.<name>.instrumentation.base_url in configuration.",
            )
        return self.instrumentation

    # -- observation --------------------------------------------------------------

    def observe(self, *, with_screen_info: bool = True) -> Observation:
        """Capture a fresh observation (screenshot + optional metadata).

        ``with_screen_info=False`` skips ``wm size`` / density probes — use
        this in ``wait_until`` poll loops where only pixels matter.
        """
        device = self.require_device()
        image = device.screenshot()
        screen: ScreenInfo | None = None
        if with_screen_info:
            try:
                screen = device.get_screen_info()
            except Exception:  # noqa: BLE001 - screen info is best-effort metadata
                screen = None
        observation = Observation(image=image, device=device.name, screen=screen)
        self.state["last_observation"] = observation
        return observation

    @property
    def last_observation(self) -> Observation | None:
        return self.state.get("last_observation")

    # -- variables ------------------------------------------------------------------

    def expand(self, value: Any) -> Any:
        """Expand ${var} references using test parameters + config variables."""
        return expand_variables(
            value, self.variables, strict=True, source=f"test {self.test.id}"
        )
