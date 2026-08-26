"""Environment diagnostics shared by the CLI ``doctor`` command and the GUI."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from argus_test_creator import __version__

Item = tuple[str, str, str]  # state, name, detail


def run_doctor(project_dir: Path | None = None) -> dict[str, list[Item]]:
    report: dict[str, list[Item]] = {}
    report["Creator"] = _creator()
    report["Argus"] = _argus(project_dir)
    report["Recording"] = _recording()
    report["OCR"] = _ocr()
    report["Android"] = _android()
    if project_dir is not None:
        report["Project"] = _project(project_dir)
    return report


def _creator() -> list[Item]:
    items: list[Item] = [("ok", "Version", __version__)]
    py = sys.version_info
    items.append(("ok" if py >= (3, 12) else "fail", "Python",
                  f"{platform.python_version()} ({sys.executable})"))
    try:
        import PySide6  # noqa: F401

        items.append(("ok", "GUI (PySide6)", PySide6.__version__))
    except ImportError:
        items.append(("warn", "GUI (PySide6)", "not installed — pip install 'argus-test-creator[ui]'"))  # noqa: E501
    return items


def _argus(project_dir: Path | None) -> list[Item]:
    from argus_test_creator.integrations.argus import INSTALL_HINT, discover_argus

    info = discover_argus(project_root=project_dir)
    if info is None:
        return [("fail", "Executable", INSTALL_HINT)]
    return [("ok", "Executable", f"{info.executable} (via {info.source})"),
            ("ok", "Version", info.version)]


def _recording() -> list[Item]:
    items: list[Item] = []
    try:
        import playwright  # noqa: F401

        items.append(("ok", "Playwright", "installed"))
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                path = p.chromium.executable_path
            items.append(("ok" if Path(path).exists() else "warn", "Chromium",
                          path if Path(path).exists() else "run: playwright install chromium"))
        except Exception as exc:  # noqa: BLE001
            items.append(("warn", "Chromium", f"not available ({exc})"))
    except ImportError:
        items.append(("warn", "Playwright", "not installed — pip install 'argus-test-creator[browser]'"))  # noqa: E501
    try:
        import mss  # noqa: F401
        import pynput  # noqa: F401

        items.append(("ok", "Desktop capture/input", "mss + pynput installed"))
    except ImportError:
        items.append(("warn", "Desktop capture/input", "pip install 'argus-test-creator[desktop]'"))
    if sys.platform == "darwin":
        items.append(("warn", "macOS permissions",
                      "Desktop recording needs Screen Recording and Input Monitoring "
                      "permission for your terminal/app (System Settings → Privacy)."))
    return items


def _ocr() -> list[Item]:
    from argus_test_creator.observation import TesseractOCRProvider

    available, reason = TesseractOCRProvider().is_available()
    if available:
        return [("ok", "Tesseract", shutil.which("tesseract") or "available")]
    return [("warn", "Tesseract", reason)]


def _android() -> list[Item]:
    adb = shutil.which("adb") or os.environ.get("ADB")
    if not adb:
        return [("warn", "ADB", "not found on PATH (needed for Android targets)")]
    items: list[Item] = [("ok", "ADB", adb)]
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=15,
                             check=False).stdout
        devices = [line.split()[0] for line in out.splitlines()[1:] if "\tdevice" in line]
        items.append(("ok" if devices else "warn", "Devices",
                      ", ".join(devices) if devices else "no connected devices"))
    except (OSError, subprocess.TimeoutExpired) as exc:
        items.append(("warn", "Devices", f"adb devices failed: {exc}"))
    return items


def _project(project_dir: Path) -> list[Item]:
    from argus_test_creator.project import CreatorProject

    project = CreatorProject(project_dir)
    if not project.exists:
        return [("fail", "Project", f"{project_dir} is not a Creator project")]
    items: list[Item] = [("ok", "Project", str(project.root))]
    writable = os.access(project.root, os.W_OK)
    items.append(("ok" if writable else "fail", "Writable", "yes" if writable else "no"))
    items.append(("ok", "Tests", ", ".join(project.list_test_ids()) or "none"))
    return items
