"""Environment diagnostics shared by the CLI ``doctor`` command and the GUI."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

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


def _android(client: Any = None, *, serial: str | None = None) -> list[Item]:
    """ADB → devices → selected device → version → input devices → touchscreen → getevent →
    screenshot. Every failure carries what to do next."""
    from argus_test_creator.adapters.android import SubprocessAdbClient, select_touchscreen
    from argus_test_creator.core.errors import TargetConnectionError

    adb = client or SubprocessAdbClient(os.environ.get("ADB") or None)
    ok, detail = adb.available()
    if not ok:
        return [("warn", "ADB", f"{detail} — needed only for Android targets")]
    items: list[Item] = [("ok", "ADB", detail)]
    try:
        devices = adb.list_devices()
    except TargetConnectionError as exc:
        return [*items, ("warn", "Devices", f"{exc.message} — {exc.remediation or ''}".strip())]
    usable = [d for d in devices if d.usable]
    if not devices:
        items.append(("warn", "Devices", "no connected devices — enable USB debugging, connect "
                                         "the device, accept the prompt, then run `adb devices`"))
        return items
    described = ", ".join(f"{d.label()} [{d.state}]" for d in devices)
    items.append(("ok" if usable else "fail", "Devices",
                  f"{len(usable)} connected ({described})" if usable else
                  f"{described} — unlock the device and accept 'Allow USB debugging'"))
    if not usable:
        return items
    serial = serial or os.environ.get("ARGUS_ANDROID_SERIAL")
    if serial and serial not in {d.serial for d in usable}:
        items.append(("fail", "Selected device", f"{serial} is not connected/authorized"))
        return items
    if not serial:
        if len(usable) > 1:
            items.append(("warn", "Selected device",
                          "several devices — set ARGUS_ANDROID_SERIAL or the target 'serial' "
                          "setting to pick one"))
            return items
        serial = usable[0].serial
    items.append(("ok", "Selected device", serial))
    try:
        info = adb.get_device_info(serial)
        items.append(("ok", "Android version",
                      f"{info.android_version or '?'} (SDK {info.sdk or '?'}, {info.model or '?'}"
                      f", {info.natural_width}x{info.natural_height}, rotation {info.rotation})"))
    except TargetConnectionError as exc:
        items.append(("warn", "Android version", f"{exc.message} — {exc.remediation or ''}"))
    ok, detail = adb.getevent_available(serial)
    if not ok:
        items.append(("fail", "getevent",
                      f"{detail} — recording needs `adb shell getevent`; check the device is "
                      "unlocked and USB debugging is authorized"))
    else:
        items.append(("ok", "getevent", detail))
        try:
            inputs = adb.get_input_devices(serial)
            items.append(("ok" if inputs else "fail", "Input devices",
                          ", ".join(f"{d.path} ({d.name})" for d in inputs) or
                          "none listed — is the device unlocked?"))
            touch, candidates = select_touchscreen(inputs)
            if touch is None:
                items.append(("warn", "Touchscreen",
                              "not detected — only hardware keys can be recorded; set the "
                              "'input_device' target setting if you know the panel"))
            else:
                extra = (f" (+{len(candidates) - 1} other candidate(s))"
                         if len(candidates) > 1 else "")
                items.append(("ok", "Touchscreen", f"{touch.path} {touch.name}{extra}"))
        except TargetConnectionError as exc:
            items.append(("warn", "Input devices", exc.message))
    try:
        data = adb.screenshot(serial)
        items.append(("ok" if data.startswith(b"\x89PNG") else "fail", "Screenshot",
                      f"{len(data)} bytes" if data.startswith(b"\x89PNG") else
                      "screencap returned no PNG — unlock the device and retry"))
    except TargetConnectionError as exc:
        items.append(("fail", "Screenshot", f"{exc.message} — {exc.remediation or ''}"))
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
