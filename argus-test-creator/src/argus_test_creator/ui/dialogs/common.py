"""Small dialogs: errors with expandable details, provenance, target settings, about."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from argus_test_creator import __version__
from argus_test_creator.core.errors import CreatorError
from argus_test_creator.models.authoring import StepDraft
from argus_test_creator.models.capabilities import TargetProfile


def show_error(parent: QWidget | None, title: str, error: BaseException) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    if isinstance(error, CreatorError):
        box.setText(error.message)
        if error.remediation:
            box.setInformativeText(f"Suggested action: {error.remediation}")
        if error.details:
            box.setDetailedText(error.details)
    else:
        box.setText("Something went wrong.")
        box.setInformativeText(str(error))
        box.setDetailedText(repr(error))
    box.exec()


def show_recording_failure(parent: QWidget | None, target: str, operation: str, message: str,
                           remediation: str | None, details: str | None) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Recording failed")
    box.setText(f"Target: {target}\nOperation: {operation}\n\n{message}")
    if remediation:
        box.setInformativeText(f"Suggested action: {remediation}")
    if details:
        box.setDetailedText(details)
    box.exec()


class ProvenanceDialog(QDialog):
    def __init__(self, step: StepDraft, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Step provenance")
        layout = QVBoxLayout(self)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        prov = step.provenance
        lines = [
            f"Step: {step.display_name()}",
            f"Action: {step.action}",
            f"Source: {prov.describe()}",
            f"Session: {prov.session_id or '-'}",
            f"Events: {', '.join(prov.event_ids) or '-'}",
            f"Action id: {prov.action_id or '-'}",
            f"Capture: {prov.capture_id or '-'}",
            f"Notes: {step.notes or '-'}",
        ]
        text.setPlainText("\n".join(lines))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class TargetSettingsDialog(QDialog):
    """Edit a target's adapter settings (URL, package, serial...). Generated from the profile."""

    LABELS = {"url": "URL", "browser": "Browser", "app_package": "App package",
              "app_activity": "App activity", "adb_path": "adb path", "serial": "Serial",
              "command": "Launch command", "monitor": "Monitor", "scenario": "Demo scenario",
              "screen_size": "Screen size", "viewport": "Viewport", "headless": "Headless",
              "loading_frames": "Loading frames"}

    def __init__(self, target: TargetProfile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{target.name} settings")
        self._target = target
        self._fields: dict[str, QLineEdit] = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for key, value in target.settings.items():
            edit = QLineEdit(_to_text(value))
            edit.setAccessibleName(self.LABELS.get(key, key))
            form.addRow(self.LABELS.get(key, key), edit)
            self._fields[key] = edit
        layout.addLayout(form)
        if target.capabilities.limitations:
            note = QPlainTextEdit("\n".join(f"• {t}" for t in target.capabilities.limitations))
            note.setReadOnly(True)
            note.setMaximumHeight(90)
            layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def settings(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, edit in self._fields.items():
            out[key] = _from_text(edit.text(), self._target.settings.get(key))
        return out


def _to_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return "" if value is None else str(value)


def _from_text(text: str, previous: Any) -> Any:
    text = text.strip()
    if isinstance(previous, bool):
        return text.lower() in ("1", "true", "yes", "on")
    if isinstance(previous, (list, tuple)):
        parts = [p.strip() for p in text.split(",") if p.strip()]
        return [int(p) if p.lstrip("-").isdigit() else p for p in parts]
    if isinstance(previous, int) and text.lstrip("-").isdigit():
        return int(text)
    return text


def about(parent: QWidget | None) -> None:
    QMessageBox.about(
        parent, "Argus Test Creator",
        f"<b>Argus Test Creator</b> {__version__}<br>"
        "Visual authoring of Argus YAML tests.<br><br>"
        "Argus is the engine. The Creator is the authoring experience.",
    )
