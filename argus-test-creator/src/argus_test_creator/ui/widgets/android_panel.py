"""AndroidPanel — device selection, connection status and recording counters.

The panel never sees raw input events: it reads a
:class:`DiagnosticsSnapshot` on a throttled timer (owned by the main window)
and shows semantic numbers only. The developer diagnostics view is a separate
dialog that renders the same snapshot in full.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from argus_test_creator.adapters.android.diagnostics import DiagnosticsSnapshot
from argus_test_creator.adapters.android.models import AndroidDevice

#: Minimum interval between counter refreshes (ms) — raw streams never drive the UI.
REFRESH_INTERVAL_MS = 250


class AndroidPanel(QWidget):
    device_selected = Signal(str)  # serial
    refresh_requested = Signal()
    reconnect_requested = Signal()
    diagnostics_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Android recorder")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("<b>Android Recorder</b>"))
        form = QFormLayout()
        self._form = form
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setAccessibleName("Android device")
        self.device_combo.setMinimumWidth(220)
        self.device_combo.currentIndexChanged.connect(self._device_changed)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setAccessibleName("Refresh Android devices")
        self.refresh_button.clicked.connect(lambda _c=False: self.refresh_requested.emit())
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.refresh_button)
        form.addRow("Device:", device_row)
        self.status_label = QLabel("○ Disconnected")
        self.status_label.setAccessibleName("Android status")
        form.addRow("Status:", self.status_label)
        self.input_label = QLabel("—")
        self.input_label.setAccessibleName("Android input device")
        form.addRow("Input device:", self.input_label)
        self.resolution_label = QLabel("—")
        self.resolution_label.setAccessibleName("Android resolution")
        form.addRow("Resolution:", self.resolution_label)
        self.raw_label = QLabel("0")
        self.raw_label.setAccessibleName("Raw events")
        form.addRow("Raw events:", self.raw_label)
        self.actions_label = QLabel("0")
        self.actions_label.setAccessibleName("Recognized actions")
        form.addRow("Actions:", self.actions_label)
        self.current_label = QLabel("—")
        self.current_label.setAccessibleName("Current action")
        form.addRow("Current action:", self.current_label)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.reconnect_button = QPushButton("Reconnect")
        self.reconnect_button.setAccessibleName("Reconnect Android device")
        self.reconnect_button.clicked.connect(lambda _c=False: self.reconnect_requested.emit())
        self.reconnect_button.setVisible(False)
        self.diagnostics_button = QPushButton("Diagnostics…")
        self.diagnostics_button.setAccessibleName("Android diagnostics")
        self.diagnostics_button.clicked.connect(
            lambda _c=False: self.diagnostics_requested.emit())
        buttons.addWidget(self.reconnect_button)
        buttons.addStretch(1)
        buttons.addWidget(self.diagnostics_button)
        layout.addLayout(buttons)
        self._selected = ""
        self._counters_visible(False)

    # -- devices ----------------------------------------------------------------------------

    def set_devices(self, devices: list[AndroidDevice], selected: str = "") -> None:
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        if not devices:
            self.device_combo.addItem("No Android device found", "")
        for device in devices:
            suffix = "" if device.usable else f" [{device.state}]"
            self.device_combo.addItem(device.label() + suffix, device.serial)
        index = self.device_combo.findData(selected) if selected else -1
        if index < 0 and len(devices) == 1 and devices[0].usable:
            index = 0
        elif index < 0 and len(devices) > 1:
            self.device_combo.insertItem(0, "Choose a device…", "")
            index = 0
        self.device_combo.setCurrentIndex(max(index, 0))
        self._selected = str(self.device_combo.currentData() or "")
        self.device_combo.blockSignals(False)

    def selected_serial(self) -> str:
        return str(self.device_combo.currentData() or "")

    def _device_changed(self) -> None:
        serial = self.selected_serial()
        if serial != self._selected:
            self._selected = serial
            self.device_selected.emit(serial)

    # -- status -------------------------------------------------------------------------------

    def show_connected(self, snapshot: DiagnosticsSnapshot) -> None:
        self.status_label.setText("● Connected")
        name = snapshot.input_device_name or "—"
        self.input_label.setText(
            f"{name} ({'touchscreen' if snapshot.touchscreen else 'keys only'})"
            if snapshot.input_device else "no touchscreen found")
        w, h = snapshot.screen_size
        self.resolution_label.setText(f"{w} × {h}" + (
            f"  (rotated {snapshot.rotation * 90}°)" if snapshot.rotation else ""))
        self.reconnect_button.setVisible(False)
        self.device_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)

    def show_disconnected(self, *, lost: bool = False) -> None:
        self.status_label.setText("⚠ Device disconnected — recording paused" if lost
                                  else "○ Disconnected")
        self.reconnect_button.setVisible(lost)
        self.device_combo.setEnabled(not lost)
        self.refresh_button.setEnabled(not lost)
        if not lost:
            self.input_label.setText("—")
            self.resolution_label.setText("—")
            self._counters_visible(False)

    def show_recording(self, snapshot: DiagnosticsSnapshot, action_count: int,
                       *, paused: bool = False) -> None:
        self._counters_visible(True)
        self.status_label.setText("‖ Paused" if paused else "● Recording")
        self.raw_label.setText(f"{snapshot.raw_events:,}")
        self.actions_label.setText(f"{action_count:,}")
        self.current_label.setText(snapshot.current_action or "—")
        self.device_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)

    def _counters_visible(self, visible: bool) -> None:
        for label in (self.raw_label, self.actions_label, self.current_label):
            label.setVisible(visible)
            caption = self._form.labelForField(label)
            if caption is not None:
                caption.setVisible(visible)


class AndroidDiagnosticsDialog(QDialog):
    """Developer view: the whole diagnostics snapshot, refreshed while open."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Android Recording Diagnostics")
        self.resize(520, 420)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setAccessibleName("Android diagnostics")
        layout.addWidget(self.text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def show_snapshot(self, snapshot: DiagnosticsSnapshot, *, action_count: int | None = None,
                      dropped: int | None = None) -> None:
        text = snapshot.render()
        if action_count is not None:
            text += f"\nSession actions: {action_count:,}"
        if snapshot.recent_gestures:
            text += "\n\nRecent actions:\n  " + "\n  ".join(snapshot.recent_gestures)
        scroll = self.text.verticalScrollBar().value()
        self.text.setPlainText(text)
        self.text.verticalScrollBar().setValue(scroll)


__all__ = ["REFRESH_INTERVAL_MS", "AndroidDiagnosticsDialog", "AndroidPanel"]
