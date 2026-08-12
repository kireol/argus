"""Pre-flight check implementations.

Checks are modular and scoped: only the components the selected tests
actually need are checked as *required*; everything else is informational.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from utf.models.results import PreflightResult
from utf.models.test_definition import ConditionSpec, TestDefinition

if TYPE_CHECKING:
    from utf.engine.session import RunSession


class PreflightCheck(ABC):
    """A single environment check."""

    required: bool = True
    target: str | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def run(self) -> PreflightResult:
        ...

    def _result(
        self,
        passed: bool,
        *,
        error: str | None = None,
        remediation: str | None = None,
        causes: list[str] | None = None,
        **diagnostics: Any,
    ) -> PreflightResult:
        return PreflightResult(
            name=self.name,
            passed=passed,
            required=self.required,
            target=self.target,
            error=error,
            remediation=remediation,
            causes=causes or [],
            diagnostics=diagnostics,
        )


class ConfigurationCheck(PreflightCheck):
    def __init__(self, session: RunSession) -> None:
        self._session = session

    @property
    def name(self) -> str:
        return "Configuration"

    def run(self) -> PreflightResult:
        config = self._session.config
        return self._result(
            True,
            config_file=config.config_file or "<built-in defaults>",
            devices=sorted(config.devices),
        )


class TestAssetsCheck(PreflightCheck):
    """Every reference image used by the selected tests must exist."""

    def __init__(self, session: RunSession, tests: list[TestDefinition]) -> None:
        self._session = session
        self._tests = tests

    @property
    def name(self) -> str:
        return "Test assets"

    def run(self) -> PreflightResult:
        missing: dict[str, list[str]] = {}
        for test in self._tests:
            for image in referenced_images(test):
                if not self._session.assets.exists(image):
                    missing.setdefault(image, []).append(test.id)
        if not missing:
            return self._result(True, checked_tests=len(self._tests))
        detail = "; ".join(
            f"{image} (used by {', '.join(ids)})" for image, ids in missing.items()
        )
        paths = ", ".join(str(p) for p in self._session.asset_paths)
        return self._result(
            False,
            error=f"Missing reference images: {detail}",
            remediation=f"Add the missing files under one of: {paths}",
            causes=["Image asset not committed", "Wrong filename in test definition"],
            missing=sorted(missing),
        )


class BackendConnectivityCheck(PreflightCheck):
    def __init__(self, session: RunSession, *, required: bool) -> None:
        self._session = session
        self.required = required
        self.target = session.config.backend.base_url

    @property
    def name(self) -> str:
        return "Backend API"

    def run(self) -> PreflightResult:
        config = self._session.config.backend
        if not config.configured:
            return self._result(
                not self.required,
                error="Backend is not configured.",
                remediation="Set backend.base_url (e.g. via BACKEND_URL).",
                causes=["backend.base_url missing or contains an unresolved ${...}"],
            )
        try:
            health = self._session.backend.health_check()
        except Exception as exc:  # noqa: BLE001 - report, don't crash preflight
            return self._result(
                False,
                error=f"Backend health check raised: {exc}",
                remediation="Check backend.base_url and that the backend is running.",
                causes=["Backend down", "Wrong URL", "Network/TLS issue"],
            )
        if health.healthy:
            return self._result(True, **health.details)
        return self._result(
            False,
            error=health.message,
            remediation="Check that the backend test environment is running.",
            causes=["Backend down", "Health endpoint path wrong", "Auth failure"],
        )


class DeviceCheck(PreflightCheck):
    """Connectivity + health for one configured device."""

    def __init__(self, session: RunSession, device_name: str, *, required: bool) -> None:
        self._session = session
        self._device_name = device_name
        self.required = required
        self.target = device_name

    @property
    def name(self) -> str:
        return f"Device: {self._device_name}"

    def run(self) -> PreflightResult:
        try:
            device = self._session.device(self._device_name)
        except Exception as exc:  # noqa: BLE001
            return self._result(
                False,
                error=str(exc),
                remediation="Check the device configuration and connectivity.",
                causes=["Device unreachable", "Bad configuration", "Driver/tool missing"],
            )
        health = device.health_check()
        if health.healthy:
            return self._result(True, **health.details)
        return self._result(
            False,
            error=health.message,
            remediation="Check that the device is powered, connected, and reachable.",
            causes=["Device offline", "Application not installed", "Transport failure"],
            **health.details,
        )


class DeviceScreenshotCheck(PreflightCheck):
    def __init__(self, session: RunSession, device_name: str, *, required: bool) -> None:
        self._session = session
        self._device_name = device_name
        self.required = required
        self.target = device_name

    @property
    def name(self) -> str:
        return f"Screenshot: {self._device_name}"

    def run(self) -> PreflightResult:
        try:
            device = self._session.device(self._device_name)
            if not device.capabilities.supports_screenshot:
                return self._result(
                    False,
                    error=f"Device {self._device_name!r} does not support screenshots.",
                    remediation="Visual tests need a screenshot-capable device.",
                )
            image = device.screenshot()
        except Exception as exc:  # noqa: BLE001
            return self._result(
                False,
                error=f"Unable to capture display: {exc}",
                remediation="Check the screenshot provider configuration for this device.",
                causes=[
                    "Device unreachable",
                    "Display server unavailable",
                    "Screenshot provider misconfigured",
                ],
            )
        return self._result(True, size=f"{image.width}x{image.height}")


class InstrumentationCheck(PreflightCheck):
    def __init__(self, session: RunSession, device_name: str, *, required: bool) -> None:
        self._session = session
        self._device_name = device_name
        self.required = required
        self.target = device_name

    @property
    def name(self) -> str:
        return f"Instrumentation: {self._device_name}"

    def run(self) -> PreflightResult:
        client = self._session.instrumentation(self._device_name)
        if client is None:
            return self._result(
                not self.required,
                error="Instrumentation not configured for this device.",
                remediation="Set devices.<name>.instrumentation.base_url.",
            )
        health = client.health_check()
        if not health.healthy:
            return self._result(
                False,
                error=health.message,
                remediation="Check the application is running with instrumentation enabled.",
                causes=["Application not running", "Wrong port", "Instrumentation disabled"],
            )
        try:
            capabilities = client.capabilities()
        except Exception:  # noqa: BLE001 - capabilities are optional metadata
            capabilities = []
        return self._result(True, capabilities=capabilities)


class ImageSubsystemCheck(PreflightCheck):
    @property
    def name(self) -> str:
        return "Image verification subsystem"

    def run(self) -> PreflightResult:
        try:
            import cv2
            import numpy as np

            probe = np.zeros((4, 4), dtype=np.uint8)
            cv2.matchTemplate(probe, probe, cv2.TM_CCOEFF_NORMED)
        except Exception as exc:  # noqa: BLE001
            return self._result(
                False,
                error=f"OpenCV unavailable: {exc}",
                remediation='Reinstall the framework: pip install -e "." '
                "(opencv-python-headless is a core dependency).",
            )
        return self._result(True, opencv=cv2.__version__)


class OCRCheck(PreflightCheck):
    def __init__(self, session: RunSession, *, required: bool) -> None:
        self._session = session
        self.required = required

    @property
    def name(self) -> str:
        return "OCR subsystem"

    def run(self) -> PreflightResult:
        try:
            from utf.ocr.base import create_ocr_provider

            provider = create_ocr_provider(self._session.config.ocr)
            available, reason = provider.is_available()
        except Exception as exc:  # noqa: BLE001
            return self._result(False, error=str(exc))
        if available:
            return self._result(True, provider=provider.name)
        return self._result(
            False,
            error=f"OCR unavailable: {reason}",
            remediation='Install OCR support: pip install "utf[ocr]" plus the '
            "tesseract binary.",
        )


# -- helpers -------------------------------------------------------------------------


def _condition_specs(spec_source: dict[str, Any]) -> list[ConditionSpec]:
    condition = spec_source.get("condition")
    if condition is None:
        return []
    try:
        return [ConditionSpec.model_validate(condition)]
    except Exception:  # noqa: BLE001 - invalid specs surface at execution time
        return []


def _walk_conditions(spec: ConditionSpec) -> list[ConditionSpec]:
    if spec.all is not None:
        return [c for child in spec.all for c in _walk_conditions(child)]
    if spec.any is not None:
        return [c for child in spec.any for c in _walk_conditions(child)]
    if spec.not_ is not None:
        return _walk_conditions(spec.not_)
    return [spec]


def _leaf_conditions(test: TestDefinition) -> list[ConditionSpec]:
    leaves: list[ConditionSpec] = []
    for step in [*test.setup, *test.steps, *test.teardown]:
        for spec in _condition_specs(step.params):
            leaves.extend(_walk_conditions(spec))
    return leaves


def referenced_images(test: TestDefinition) -> list[str]:
    """All reference-image names used by a test (ignores ${var} references)."""
    images: list[str] = []
    for leaf in _leaf_conditions(test):
        image = leaf.params.get("image")
        if isinstance(image, str) and "${" not in image:
            images.append(image)
        else:
            expanded = _expand_with_params(image, test)
            if expanded:
                images.append(expanded)
    return images


def _expand_with_params(image: Any, test: TestDefinition) -> str | None:
    if not isinstance(image, str):
        return None
    from utf.utilities.variables import expand_variables

    try:
        expanded = expand_variables(image, test.parameters, strict=True)
    except Exception:  # noqa: BLE001 - runtime-only variables can't be checked here
        return None
    return expanded if isinstance(expanded, str) else None


def uses_ocr(tests: list[TestDefinition]) -> bool:
    return any(
        leaf.type in ("text_present", "text_not_present")
        for test in tests
        for leaf in _leaf_conditions(test)
    )


def uses_backend(tests: list[TestDefinition]) -> bool:
    return any(
        step.action.startswith("backend.")
        for test in tests
        for step in [*test.setup, *test.steps, *test.teardown]
    ) or any(
        leaf.type == "backend_value" for test in tests for leaf in _leaf_conditions(test)
    )


def uses_instrumentation(tests: list[TestDefinition]) -> bool:
    return any(
        leaf.type in ("instrumentation_value", "application_state")
        for test in tests
        for leaf in _leaf_conditions(test)
    )


def build_preflight_checks(
    session: RunSession,
    tests: list[TestDefinition],
    device_names: list[str],
) -> list[PreflightCheck]:
    """Assemble the check list for this run's actual needs."""
    checks: list[PreflightCheck] = [
        ConfigurationCheck(session),
        ImageSubsystemCheck(),
        TestAssetsCheck(session, tests),
    ]
    backend_required = uses_backend(tests)
    if backend_required or session.config.backend.configured:
        checks.append(BackendConnectivityCheck(session, required=backend_required))

    instrumentation_required = uses_instrumentation(tests)
    for name in device_names:
        checks.append(DeviceCheck(session, name, required=True))
        checks.append(DeviceScreenshotCheck(session, name, required=True))
        device_config = session.config.devices.get(name)
        if device_config is not None and device_config.instrumentation is not None:
            checks.append(
                InstrumentationCheck(session, name, required=instrumentation_required)
            )

    if uses_ocr(tests):
        checks.append(OCRCheck(session, required=True))
    return checks
