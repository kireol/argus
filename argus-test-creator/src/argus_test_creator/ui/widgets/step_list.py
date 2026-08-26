"""StepListWidget — the ordered test steps with drag reorder and a context menu."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QMenu, QWidget

from argus_test_creator.models.authoring import AuthoringDocument, StepDraft, ValidationIssue

ROLE_STEP_ID = Qt.ItemDataRole.UserRole + 1


class StepListWidget(QListWidget):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    duplicate_requested = Signal(str)
    toggle_requested = Signal(str)
    convert_requested = Signal(str)
    provenance_requested = Signal(str)
    rename_requested = Signal(str)
    reordered = Signal(str, int)  # step_id, new index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Test steps")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(
            lambda item: self.edit_requested.emit(item.data(ROLE_STEP_ID)))
        self.setAlternatingRowColors(True)
        self._issues: dict[str, list[ValidationIssue]] = {}
        self.model().rowsMoved.connect(self._rows_moved)

    def selected_step_id(self) -> str | None:
        items = self.selectedItems()
        return items[0].data(ROLE_STEP_ID) if items else None

    def refresh(self, document: AuthoringDocument, issues: list[ValidationIssue] | None = None,
                *, select: str | None = None) -> None:
        selected = select or self.selected_step_id()
        self._issues = {}
        for issue in issues or []:
            if issue.step_id:
                self._issues.setdefault(issue.step_id, []).append(issue)
        self.blockSignals(True)
        self.clear()
        for index, step in enumerate(document.steps, start=1):
            item = QListWidgetItem(self._label(index, step))
            item.setData(ROLE_STEP_ID, step.id)
            item.setToolTip(self._tooltip(step))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            if not step.enabled:
                item.setForeground(QColor(140, 140, 150))
                font = QFont()
                font.setStrikeOut(True)
                item.setFont(font)
            step_issues = self._issues.get(step.id, [])
            if any(i.is_error for i in step_issues):
                item.setForeground(QColor(220, 80, 80))
            elif step_issues:
                item.setForeground(QColor(220, 170, 60))
            if step.is_assertion:
                item.setBackground(QColor(40, 60, 48))
            self.addItem(item)
            if step.id == selected:
                item.setSelected(True)
                self.setCurrentItem(item)
        self.blockSignals(False)

    @staticmethod
    def _label(index: int, step: StepDraft) -> str:
        marker = "✓ " if step.is_assertion else ""
        return f"{index}. {marker}{step.display_name()}"

    def _tooltip(self, step: StepDraft) -> str:
        lines = [f"action: {step.action}"]
        for key, value in step.params.items():
            lines.append(f"{key}: {value}")
        if step.condition is not None:
            lines.append(f"condition: {step.condition.describe()}")
        lines.append(step.provenance.describe())
        for issue in self._issues.get(step.id, []):
            lines.append(f"{issue.severity}: {issue.message}")
        return "\n".join(lines)

    def _rows_moved(self, _parent, start: int, _end: int, _dest, row: int) -> None:
        item = self.item(row if row < start else row - 1)
        if item is not None:
            self.reordered.emit(item.data(ROLE_STEP_ID), self.row(item))

    def _menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        step_id = item.data(ROLE_STEP_ID)
        menu = QMenu(self)

        def add(label: str, signal, shortcut: str | None = None) -> None:
            action = QAction(label, menu)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(lambda: signal.emit(step_id))
            menu.addAction(action)

        add("Edit…", self.edit_requested, "Return")
        add("Rename…", self.rename_requested, "F2")
        add("Duplicate", self.duplicate_requested, "Ctrl+D")
        add("Enable/Disable", self.toggle_requested, "Ctrl+E")
        add("Convert verify ⇄ wait_until", self.convert_requested)
        add("Where did this come from?", self.provenance_requested)
        menu.addSeparator()
        add("Delete", self.delete_requested, "Delete")
        menu.exec(self.mapToGlobal(pos))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        step_id = self.selected_step_id()
        key = event.key()
        if step_id and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.edit_requested.emit(step_id)
        elif step_id and key == Qt.Key.Key_Delete:
            self.delete_requested.emit(step_id)
        elif step_id and key == Qt.Key.Key_F2:
            self.rename_requested.emit(step_id)
        else:
            super().keyPressEvent(event)


def bind(signal, handler: Callable[[str], None]) -> None:
    signal.connect(handler)
