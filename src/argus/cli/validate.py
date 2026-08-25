"""Environment validation for ``argus validate`` and ``argus --dry-run``.

Distinguishes required / optional / configured / not-configured / failed —
an optional component that is missing is a warning, never a fatal failure.
"""

from __future__ import annotations

import platform as py_platform
import sys
from dataclasses import dataclass, field
from enum import StrEnum

from rich.console import Console

from argus import __version__
from argus.config.models import AppConfig
from argus.engine.session import RunSession
from argus.exceptions import UTFError
from argus.models.test_definition import TestDefinition


class CheckState(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    NOT_CONFIGURED = "not_configured"


@dataclass
class ValidationItem:
    name: str
    state: CheckState
    detail: str = ""
    required: bool = True


@dataclass
class ValidationSection:
    title: str
    items: list[ValidationItem] = field(default_factory=list)

    def add(
        self,
        name: str,
        state: CheckState,
        detail: str = "",
        *,
        required: bool = True,
    ) -> None:
        self.items.append(ValidationItem(name, state, detail, required))


@dataclass
class ValidationReport:
    sections: list[ValidationSection] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not any(
            item.state == CheckState.FAIL and item.required
            for section in self.sections
            for item in section.items
        )


def validate_environment(
    config: AppConfig, *, framework_only: bool = False
) -> ValidationReport:
    report = ValidationReport()
    tests, load_error = _try_load_tests(config)

    report.sections.append(_framework_section(config, load_error, tests))
    report.sections.append(_visual_section(config))
    if not framework_only:
        report.sections.append(_backend_section(config))
        with RunSession(config) as session:
            for name in sorted(config.devices):
                report.sections.append(_device_section(session, name))
    return report


def _try_load_tests(config: AppConfig) -> tuple[list[TestDefinition] | None, str | None]:
    from argus.engine.loader import load_tests

    try:
        paths = [config.resolve_path(p) for p in config.test_paths]
        return load_tests(paths), None
    except UTFError as exc:
        return None, str(exc)


def _framework_section(
    config: AppConfig, load_error: str | None, tests: list[TestDefinition] | None
) -> ValidationSection:
    section = ValidationSection("Framework")
    section.add("Version", CheckState.OK, __version__)

    python_version = sys.version_info
    if python_version >= (3, 12):
        section.add(
            "Python",
            CheckState.OK,
            f"{py_platform.python_version()} ({sys.executable})",
        )
    else:
        section.add(
            "Python",
            CheckState.FAIL,
            f"{py_platform.python_version()} — Python 3.12+ required",
        )

    section.add(
        "Configuration",
        CheckState.OK,
        config.config_file or "built-in defaults",
    )

    missing: list[str] = []
    for module in ("pydantic", "yaml", "httpx", "cv2", "numpy", "PIL", "typer", "rich"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        section.add("Dependencies", CheckState.FAIL, f"missing: {', '.join(missing)}")
    else:
        section.add("Dependencies", CheckState.OK)

    if load_error is not None:
        section.add("Test definitions", CheckState.FAIL, load_error)
    else:
        assert tests is not None
        section.add("Test definitions", CheckState.OK, f"{len(tests)} tests loaded")

    section.add("Reporting subsystem", _probe_reporting())
    return section


def _probe_reporting() -> CheckState:
    try:
        from argus.reporting import (  # noqa: F401
            write_html_report,
            write_json_report,
            write_junit_report,
        )

        return CheckState.OK
    except Exception:  # noqa: BLE001
        return CheckState.FAIL


def _visual_section(config: AppConfig) -> ValidationSection:
    section = ValidationSection("Visual Testing")
    try:
        import cv2
        import numpy as np

        probe = np.zeros((4, 4), dtype=np.uint8)
        cv2.matchTemplate(probe, probe, cv2.TM_CCOEFF_NORMED)
        section.add("OpenCV", CheckState.OK, cv2.__version__)
    except Exception as exc:  # noqa: BLE001
        section.add("OpenCV", CheckState.FAIL, str(exc))

    asset_dirs = [config.resolve_path(p) for p in config.asset_paths]
    existing = [p for p in asset_dirs if p.is_dir()]
    if existing:
        count = sum(len(list(p.rglob("*.png"))) for p in existing)
        section.add("Image assets", CheckState.OK, f"{count} images")
    else:
        section.add(
            "Image assets",
            CheckState.WARN,
            f"no asset directories found ({', '.join(str(p) for p in asset_dirs)})",
            required=False,
        )

    try:
        from argus.ocr.base import create_ocr_provider

        provider = create_ocr_provider(config.ocr)
        available, reason = provider.is_available()
        if available:
            section.add("OCR", CheckState.OK, provider.name, required=False)
        else:
            section.add("OCR", CheckState.WARN, f"unavailable — {reason}", required=False)
    except Exception as exc:  # noqa: BLE001
        section.add("OCR", CheckState.WARN, str(exc), required=False)
    return section


def _backend_section(config: AppConfig) -> ValidationSection:
    section = ValidationSection("Backend")
    if not config.backend.configured:
        section.add(
            "Configuration",
            CheckState.NOT_CONFIGURED,
            "backend.base_url not set (set BACKEND_URL or edit config)",
            required=False,
        )
        return section
    section.add(
        "Configuration",
        CheckState.OK,
        config.backend.base_url or f"type: {config.backend.type}",
    )
    try:
        from argus.adapters.backend import BackendAdapter

        adapter: BackendAdapter
        if config.backend.type == "fake":
            from argus.adapters.fake import FakeBackend

            adapter = FakeBackend(config.backend.initial_state)
        else:
            adapter = BackendAdapter(config.backend)
        try:
            health = adapter.health_check()
        finally:
            adapter.close()
        if health.healthy:
            section.add("Connectivity", CheckState.OK, health.message)
        else:
            section.add("Connectivity", CheckState.FAIL, health.message)
    except UTFError as exc:
        section.add("Connectivity", CheckState.FAIL, str(exc))
    return section


def _device_section(session: RunSession, name: str) -> ValidationSection:
    config = session.config.devices[name]
    section = ValidationSection(f"Device: {name} ({config.effective_platform})")
    if not config.configured:
        section.add(
            "Configuration",
            CheckState.NOT_CONFIGURED,
            "contains unresolved ${...} values — set the environment variables",
            required=False,
        )
        return section
    section.add("Configuration", CheckState.OK)

    try:
        device = session.device(name)
    except UTFError as exc:
        section.add("Connectivity", CheckState.FAIL, str(exc))
        return section

    health = device.health_check()
    if health.healthy:
        section.add("Connectivity", CheckState.OK, health.message)
    else:
        section.add("Connectivity", CheckState.FAIL, health.message)
        return section

    if device.capabilities.supports_app_lifecycle:
        try:
            running = device.is_application_running()
            section.add(
                "Application",
                CheckState.OK if running else CheckState.WARN,
                "running" if running else "not running",
                required=False,
            )
        except UTFError as exc:
            section.add("Application", CheckState.WARN, str(exc), required=False)

    if device.capabilities.supports_screenshot:
        try:
            image = device.screenshot()
            section.add("Screenshot", CheckState.OK, f"{image.width}x{image.height}")
        except UTFError as exc:
            section.add("Screenshot", CheckState.FAIL, str(exc))
    else:
        section.add("Screenshot", CheckState.WARN, "not supported", required=False)

    try:
        client = session.instrumentation(name)
    except UTFError as exc:
        section.add("Instrumentation", CheckState.FAIL, str(exc))
        return section

    if client is None:
        section.add(
            "Instrumentation", CheckState.NOT_CONFIGURED, "", required=False
        )
    else:
        health = client.health_check()
        if health.healthy:
            try:
                capabilities = ", ".join(client.capabilities()) or "reachable"
            except Exception:  # noqa: BLE001
                capabilities = "reachable"
            section.add("Instrumentation", CheckState.OK, capabilities, required=False)
        else:
            section.add(
                "Instrumentation", CheckState.WARN, health.message, required=False
            )
    return section


_SYMBOLS = {
    CheckState.OK: ("✓", "green"),
    CheckState.WARN: ("⚠", "yellow"),
    CheckState.FAIL: ("✗", "red"),
    CheckState.NOT_CONFIGURED: ("○", "dim"),
}


def print_report(report: ValidationReport, console: Console) -> None:
    console.print("\n[bold]Universal Test Framework[/bold]")
    console.print("Environment Validation")
    console.print("─" * 36)
    for section in report.sections:
        console.print(f"\n[bold]{section.title}[/bold]")
        for item in section.items:
            symbol, style = _SYMBOLS[item.state]
            detail = f"  [dim]{item.detail}[/dim]" if item.detail else ""
            console.print(f"  [{style}]{symbol}[/{style}] {item.name}{detail}")
    console.print("\n[bold]RESULT[/bold]\n")
    if report.ready:
        console.print("Framework: [bold green]READY[/bold green]\n")
    else:
        console.print("Framework: [bold red]NOT READY[/bold red]")
        console.print("Fix the ✗ items above and run 'argus validate' again.\n")
