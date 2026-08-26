"""Test metadata form (id, name, description, feature, tags, platforms, priority, timeout...)."""

from __future__ import annotations

from typing import Any

import yaml
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from argus_test_creator.models.authoring import TestMetadata, ValidationIssue


class MetadataEditor(QWidget):
    changed = Signal(str, object)  # field, value

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Test details")
        layout = QFormLayout(self)
        self.id = QLineEdit()
        self.id.setPlaceholderText("MOV-001")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Search shows Batman Begins")
        self.description = QPlainTextEdit()
        self.description.setMaximumHeight(70)
        self.feature = QLineEdit()
        self.feature.setPlaceholderText("Movies")
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("smoke, movies")
        self.platforms = QLineEdit()
        self.platforms.setPlaceholderText("android, web")
        self.priority = QComboBox()
        self.priority.addItems(["", "low", "medium", "high", "critical"])
        self.priority.setEditable(True)
        self.timeout = QLineEdit()
        self.timeout.setPlaceholderText("60s")
        self.devices = QLineEdit()
        self.devices.setPlaceholderText("device names (requires.devices)")
        self.parameters = QPlainTextEdit()
        self.parameters.setPlaceholderText("YAML mapping, e.g.\nmovie_id: 123")
        self.parameters.setMaximumHeight(70)
        self.retry_count = QSpinBox()
        self.retry_count.setRange(0, 10)
        self.retry_only = QLineEdit()
        self.retry_only.setPlaceholderText("timeout, device_connection")
        self.error = QLabel()
        self.error.setStyleSheet("color: #e05050")
        self.error.setWordWrap(True)
        for label, widget in (
            ("ID", self.id), ("Name", self.name), ("Description", self.description),
            ("Feature", self.feature), ("Tags", self.tags), ("Platforms", self.platforms),
            ("Priority", self.priority), ("Timeout", self.timeout), ("Devices", self.devices),
            ("Parameters", self.parameters), ("Retry count", self.retry_count),
            ("Retry only", self.retry_only), ("", self.error),
        ):
            widget.setAccessibleName(label or "Validation message")
            layout.addRow(label, widget)
        self._wire()
        self._loading = False

    def _wire(self) -> None:
        self.id.editingFinished.connect(lambda: self._emit("id", self.id.text().strip()))
        self.name.editingFinished.connect(lambda: self._emit("name", self.name.text().strip()))
        self.feature.editingFinished.connect(
            lambda: self._emit("feature", self.feature.text().strip()))
        self.description.textChanged.connect(
            lambda: self._emit("description", self.description.toPlainText()))
        self.tags.editingFinished.connect(lambda: self._emit("tags", _csv(self.tags.text())))
        self.platforms.editingFinished.connect(
            lambda: self._emit("platforms", _csv(self.platforms.text())))
        self.priority.currentTextChanged.connect(
            lambda text: self._emit("priority", text.strip() or None))
        self.timeout.editingFinished.connect(
            lambda: self._emit("timeout", self.timeout.text().strip() or None))
        self.devices.editingFinished.connect(self._emit_devices)
        self.parameters.textChanged.connect(self._emit_parameters)
        self.retry_count.valueChanged.connect(lambda v: self._emit("retry_count", int(v)))
        self.retry_only.editingFinished.connect(
            lambda: self._emit("retry_only", _csv(self.retry_only.text())))

    def _emit(self, field: str, value: Any) -> None:
        if not self._loading:
            self.changed.emit(field, value)

    def _emit_devices(self) -> None:
        devices = _csv(self.devices.text())
        self._emit("requires", {"devices": devices} if devices else {})

    def _emit_parameters(self) -> None:
        text = self.parameters.toPlainText().strip()
        if not text:
            self._emit("parameters", {})
            self.error.setText("")
            return
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            self.error.setText(f"Parameters must be valid YAML: {exc}")
            return
        if not isinstance(data, dict):
            self.error.setText("Parameters must be a mapping (name: value).")
            return
        self.error.setText("")
        self._emit("parameters", data)

    def load(self, meta: TestMetadata) -> None:
        self._loading = True
        try:
            self.id.setText(meta.id)
            self.name.setText(meta.name)
            if self.description.toPlainText() != meta.description:
                self.description.setPlainText(meta.description)
            self.feature.setText(meta.feature)
            self.tags.setText(", ".join(meta.tags))
            self.platforms.setText(", ".join(meta.platforms))
            self.priority.setCurrentText(meta.priority or "")
            self.timeout.setText(meta.timeout or "")
            devices = meta.requires.get("devices", [])
            self.devices.setText(", ".join(devices) if isinstance(devices, list) else str(devices))
            params = yaml.safe_dump(meta.parameters, sort_keys=False).strip() if meta.parameters else ""  # noqa: E501
            if self.parameters.toPlainText().strip() != params:
                self.parameters.setPlainText(params)
            self.retry_count.setValue(meta.retry_count)
            self.retry_only.setText(", ".join(meta.retry_only))
        finally:
            self._loading = False

    def show_issues(self, issues: list[ValidationIssue]) -> None:
        field_issues = [i for i in issues if i.step_id is None and i.field]
        self.error.setText("\n".join(f"{i.message} {i.fix or ''}".strip() for i in field_issues))
        for name in ("id", "name", "feature", "timeout"):
            widget = getattr(self, name)
            bad = any(i.field == name for i in field_issues)
            widget.setStyleSheet("border: 1px solid #e05050" if bad else "")


def _csv(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]
