"""Secondary panels: YAML preview, validation, quality, run output, suggestions, remote."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from argus_test_creator.integrations.argus import ArgusRunResult
from argus_test_creator.models.authoring import ValidationIssue
from argus_test_creator.observation import AssertionCandidate
from argus_test_creator.quality import QualityReport

ROLE_DATA = Qt.ItemDataRole.UserRole + 1


def _mono() -> QFont:
    font = QFont("Menlo")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(11)
    return font


class YamlPreview(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(_mono())
        self.setAccessibleName("Generated Argus YAML")
        self.setPlaceholderText("The generated Argus YAML appears here.")

    def show_yaml(self, text: str) -> None:
        if text != self.toPlainText():
            scroll = self.verticalScrollBar().value()
            self.setPlainText(text)
            self.verticalScrollBar().setValue(scroll)


class ValidationPanel(QListWidget):
    issue_activated = Signal(object)  # ValidationIssue

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Validation issues")
        self.itemActivated.connect(lambda item: self.issue_activated.emit(item.data(ROLE_DATA)))

    def show_issues(self, issues: list[ValidationIssue], *, argus_checked: bool = False) -> None:
        self.clear()
        if not issues:
            label = "✓ No problems found" + (" (Argus agrees)" if argus_checked else "")
            self.addItem(QListWidgetItem(label))
            return
        for issue in sorted(issues, key=lambda i: (i.severity != "error", i.code)):
            symbol = {"error": "✗", "warning": "⚠", "info": "ℹ"}[issue.severity]
            text = f"{symbol} {issue.message}"
            if issue.fix:
                text += f"\n    Fix: {issue.fix}"
            item = QListWidgetItem(text)
            item.setData(ROLE_DATA, issue)
            item.setToolTip(f"{issue.code} ({issue.source})")
            self.addItem(item)


class QualityPanel(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAccessibleName("Test quality")

    def show_report(self, report: QualityReport) -> None:
        self.setPlainText(report.render() + f"\n\nScore: {report.score}/100")


class RunPanel(QWidget):
    open_report_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Argus run")
        layout = QVBoxLayout(self)
        self.status = QLabel("Not run yet")
        self.status.setAccessibleName("Run status")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(_mono())
        self.output.setAccessibleName("Argus output")
        self.open_button = QPushButton("Open HTML report")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(
            lambda: self.open_report_requested.emit(self._report or ""))
        layout.addWidget(self.status)
        layout.addWidget(self.output, 1)
        layout.addWidget(self.open_button)
        self._report: str | None = None

    def start(self, test_id: str) -> None:
        self.status.setText(f"Running {test_id} with Argus…")
        self.output.clear()
        self.open_button.setEnabled(False)

    def append(self, line: str) -> None:
        self.output.appendPlainText(line)

    def finish(self, result: ArgusRunResult) -> None:
        colors = {"passed": "#4caf50", "failed": "#e05050"}
        color = colors.get(result.status, "#e0a030")
        self.status.setText(f'<b style="color:{color}">{result.status.upper()}</b> '
                            f"(exit {result.exit_code})")
        self._report = str(result.html_report) if result.html_report else None
        self.open_button.setEnabled(self._report is not None)
        for test in result.test_results():
            self.append(f"{test.get('test_id')}: {test.get('status')} "
                        f"{test.get('error') or ''}".rstrip())


class SuggestionsPanel(QWidget):
    """"Argus Test Creator noticed…" — candidates the user can Add or Ignore."""

    accept_requested = Signal(object)  # AssertionCandidate

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Suggested verifications")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("Suggested verifications")
        self.list = QListWidget()
        self.list.setAccessibleName("Suggestions")
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add")
        self.ignore_button = QPushButton("Ignore")
        self.add_button.setEnabled(False)
        self.ignore_button.setEnabled(False)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.ignore_button)
        layout.addWidget(self.title)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.add_button.clicked.connect(self._accept)
        self.ignore_button.clicked.connect(self._ignore)
        self.list.itemActivated.connect(lambda _i: self._accept())

    def add_candidates(self, candidates: list[AssertionCandidate]) -> None:
        existing = {self.list.item(i).text() for i in range(self.list.count())}
        for candidate in candidates:
            text = f"{candidate.describe()}  —  {candidate.reason}"
            if text in existing:
                continue
            item = QListWidgetItem(text)
            item.setData(ROLE_DATA, candidate)
            self.list.addItem(item)
        self.title.setText(f"Suggested verifications ({self.list.count()})")

    def candidates(self) -> list[AssertionCandidate]:
        return [self.list.item(i).data(ROLE_DATA) for i in range(self.list.count())]

    def _selection_changed(self) -> None:
        has = bool(self.list.selectedItems())
        self.add_button.setEnabled(has)
        self.ignore_button.setEnabled(has)

    def _accept(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        candidate = items[0].data(ROLE_DATA)
        self.list.takeItem(self.list.row(items[0]))
        self.accept_requested.emit(candidate)
        self.title.setText(f"Suggested verifications ({self.list.count()})")

    def _ignore(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.title.setText(f"Suggested verifications ({self.list.count()})")


class RemotePanel(QWidget):
    """On-screen remote for targets whose input the Creator must send (fake, Roku, Android)."""

    key_pressed = Signal(str)
    text_entered = Signal(str)

    KEYS = (("DPAD_UP", 0, 1), ("DPAD_LEFT", 1, 0), ("DPAD_CENTER", 1, 1), ("DPAD_RIGHT", 1, 2),
            ("DPAD_DOWN", 2, 1), ("BACK", 3, 0), ("HOME", 3, 1), ("ENTER", 3, 2))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Remote control")
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        for key, row, col in self.KEYS:
            button = QPushButton(key.replace("DPAD_", "").title())
            button.setAccessibleName(f"Send {key}")
            button.clicked.connect(lambda _c=False, k=key: self.key_pressed.emit(k))
            grid.addWidget(button, row, col)
        layout.addLayout(grid)
        text_row = QHBoxLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText("Type text and press Enter to send")
        self.text.setAccessibleName("Text to send")
        self.text.returnPressed.connect(self._send_text)
        text_row.addWidget(self.text)
        layout.addLayout(text_row)

    def _send_text(self) -> None:
        text = self.text.text()
        if text:
            self.text_entered.emit(text)
            self.text.clear()


def make_button(label: str, handler: Callable[[], Any], *, shortcut: Any = None,
                accessible: str | None = None) -> QPushButton:
    button = QPushButton(label)
    button.clicked.connect(lambda _checked=False: handler())
    if shortcut is not None:
        button.setShortcut(QKeySequence(shortcut))
        button.setToolTip(f"{label} ({button.shortcut().toString()})")
    button.setAccessibleName(accessible or label)
    button.setMinimumHeight(36)
    return button
