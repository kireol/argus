"""StepEditorDialog — edit any step (action, parameters, condition, name, notes)."""

from __future__ import annotations

from typing import Any

import yaml
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from argus_test_creator.argus_schema import ACTIONS, CONDITIONS, ParamSpec
from argus_test_creator.argus_schema.actions import PRIMARY_ACTIONS, actions_for
from argus_test_creator.argus_schema.conditions import conditions_for
from argus_test_creator.models.authoring import ConditionDraft, StepDraft
from argus_test_creator.models.capabilities import RecorderCapabilities
from argus_test_creator.models.common import parse_duration


class ParamsForm(QWidget):
    """A form generated from ParamSpecs; values are converted back to their YAML types."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._fields_box = QWidget()
        self._layout = QFormLayout(self._fields_box)
        outer.addWidget(self._fields_box)
        self._fields: dict[str, tuple[ParamSpec, QLineEdit]] = {}
        self.extra = QPlainTextEdit()
        self.extra.setPlaceholderText("Additional parameters as YAML (advanced)")
        self.extra.setMaximumHeight(60)
        self.extra.setAccessibleName("Additional parameters")
        more = QFormLayout()
        more.addRow("More", self.extra)
        outer.addLayout(more)

    def build(self, specs: tuple[ParamSpec, ...], values: dict[str, Any], *,
              skip: set[str] | None = None) -> None:
        while self._layout.rowCount():
            self._layout.removeRow(0)
        self._fields = {}
        known = set()
        for spec in specs:
            if skip and spec.name in skip:
                continue
            known.add(spec.name)
            edit = QLineEdit(_to_text(values.get(spec.name, "")))
            hint = f"{spec.type}" + (" (required)" if spec.required else "")
            if spec.default is not None:
                hint += f", default {spec.default}"
            edit.setPlaceholderText(hint)
            edit.setToolTip(spec.help or hint)
            edit.setAccessibleName(spec.name)
            self._layout.addRow(spec.name + (" *" if spec.required else ""), edit)
            self._fields[spec.name] = (spec, edit)
        extras = {k: v for k, v in values.items() if k not in known and k != "condition"}
        self.extra.setPlainText(yaml.safe_dump(extras, sort_keys=False).strip() if extras else "")

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, (spec, edit) in self._fields.items():
            text = edit.text().strip()
            if text == "":
                continue
            out[name] = _convert(text, spec.type)
        extra = self.extra.toPlainText().strip()
        if extra:
            data = yaml.safe_load(extra)
            if not isinstance(data, dict):
                raise ValueError("Additional parameters must be a YAML mapping.")
            out.update(data)
        return out

    def missing_required(self) -> list[str]:
        return [name for name, (spec, edit) in self._fields.items()
                if spec.required and not edit.text().strip()]


class ConditionEditor(QGroupBox):
    """Leaf-condition editor (composites are shown as YAML for expert editing)."""

    def __init__(self, capabilities: RecorderCapabilities | None,
                 parent: QWidget | None = None) -> None:
        super().__init__("Condition", parent)
        self._capabilities = capabilities
        layout = QVBoxLayout(self)
        self.type = QComboBox()
        self.type.setAccessibleName("Condition type")
        specs = conditions_for(capabilities) if capabilities else list(CONDITIONS.values())
        for spec in specs:
            self.type.addItem(f"{spec.label} ({spec.name})", spec.name)
        self.type.addItem("Composite (all / any / not) — edit as YAML", "__composite__")
        layout.addWidget(self.type)
        self.params = ParamsForm()
        layout.addWidget(self.params)
        self.yaml = QPlainTextEdit()
        self.yaml.setAccessibleName("Condition YAML")
        self.yaml.setVisible(False)
        layout.addWidget(self.yaml)
        self.type.currentIndexChanged.connect(self._type_changed)
        self._condition: ConditionDraft | None = None

    def load(self, condition: ConditionDraft | None) -> None:
        self._condition = condition
        if condition is None:
            self.type.setCurrentIndex(0)
            self._type_changed()
            return
        if condition.is_composite:
            index = self.type.findData("__composite__")
            self.type.setCurrentIndex(index)
            self.yaml.setPlainText(yaml.safe_dump(condition.to_argus(), sort_keys=False))
            return
        index = self.type.findData(condition.type)
        if index < 0:
            self.type.addItem(f"{condition.type} (unknown to Creator)", condition.type)
            index = self.type.count() - 1
        self.type.setCurrentIndex(index)
        self._type_changed()

    def _type_changed(self) -> None:
        name = self.type.currentData()
        composite = name == "__composite__"
        self.params.setVisible(not composite)
        self.yaml.setVisible(composite)
        if composite:
            return
        spec = CONDITIONS.get(name)
        values = self._condition.params if self._condition and self._condition.type == name else {}
        self.params.build(spec.params if spec else (), values)

    def condition(self) -> ConditionDraft:
        name = self.type.currentData()
        if name == "__composite__":
            data = yaml.safe_load(self.yaml.toPlainText())
            return ConditionDraft.from_argus(data)
        return ConditionDraft(type=name, params=self.params.values())

    def missing_required(self) -> list[str]:
        if self.type.currentData() == "__composite__":
            return []
        return self.params.missing_required()


class StepEditorDialog(QDialog):
    def __init__(self, step: StepDraft, capabilities: RecorderCapabilities | None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit step")
        self.setMinimumWidth(520)
        self._step = step
        self._capabilities = capabilities
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.action = QComboBox()
        self.action.setAccessibleName("Action")
        available = actions_for(capabilities) if capabilities else list(ACTIONS.values())
        ordered = sorted(available, key=lambda s: (s.name not in PRIMARY_ACTIONS, s.name))
        for spec in ordered:
            self.action.addItem(f"{spec.label} ({spec.name})", spec.name)
        if step.action not in ACTIONS:
            self.action.addItem(f"{step.action} (custom — kept verbatim)", step.action)
        elif self.action.findData(step.action) < 0:
            self.action.addItem(f"{ACTIONS[step.action].label} ({step.action}) — unsupported "
                                "on this target", step.action)
        self.name = QLineEdit(step.name or "")
        self.name.setPlaceholderText(step.default_name())
        self.name.setAccessibleName("Step name")
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(step.enabled)
        self.notes = QPlainTextEdit(step.notes)
        self.notes.setMaximumHeight(60)
        self.notes.setAccessibleName("Notes")
        form.addRow("Action", self.action)
        form.addRow("Name", self.name)
        form.addRow("", self.enabled)
        layout.addLayout(form)
        self.help = QLabel()
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        self.params = ParamsForm()
        layout.addWidget(self.params)
        self.condition = ConditionEditor(capabilities)
        layout.addWidget(self.condition)
        layout.addWidget(QLabel("Notes"))
        layout.addWidget(self.notes)
        self.warning = QLabel()
        self.warning.setStyleSheet("color: #e0a030")
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.action.setCurrentIndex(max(self.action.findData(step.action), 0))
        self.action.currentIndexChanged.connect(self._action_changed)
        self._action_changed()
        self.condition.load(step.condition)
        self.result_step: StepDraft | None = None

    def _action_changed(self) -> None:
        name = self.action.currentData()
        spec = ACTIONS.get(name)
        self.help.setText(spec.help if spec else "Custom action: parameters are kept verbatim.")
        values = self._step.params if name == self._step.action else {}
        self.params.build(spec.params if spec else (), values, skip={"condition"})
        self.condition.setVisible(name in ("verify", "wait_until"))
        if spec and spec.dangerous:
            self.warning.setText("This step runs a host command. Review it carefully; it "
                                 "executes on the machine running Argus.")
        else:
            self.warning.setText("")

    def _accept(self) -> None:
        name = self.action.currentData()
        missing = self.params.missing_required()
        try:
            params = self.params.values()
            condition = self.condition.condition() if name in ("verify", "wait_until") else None
            if condition is not None:
                missing += self.condition.missing_required()
        except (ValueError, yaml.YAMLError) as exc:
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return
        if missing:
            QMessageBox.warning(self, "Missing parameters",
                                "Please fill in: " + ", ".join(missing))
            return
        spec = ACTIONS.get(name)
        if spec and spec.dangerous:
            answer = QMessageBox.question(
                self, "Confirm host command",
                f"Author a step that runs `{params.get('command', '')}` on the host?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.result_step = self._step.model_copy(update={
            "action": name,
            "name": self.name.text().strip() or None,
            "params": params,
            "condition": condition,
            "enabled": self.enabled.isChecked(),
            "notes": self.notes.toPlainText(),
            "custom": name not in ACTIONS,
        })
        self.accept()


def _to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return yaml.safe_dump(value, default_flow_style=True).strip()
    return "" if value is None else str(value)


def _convert(text: str, type_: str) -> Any:
    if "${" in text:
        return text
    match type_:
        case "int":
            return int(text)
        case "float":
            return float(text)
        case "bool":
            return text.lower() in ("1", "true", "yes", "on")
        case "duration":
            parse_duration(text)
            return text
        case "list" | "mapping" | "region" | "any":
            try:
                return yaml.safe_load(text)
            except yaml.YAMLError:
                return text
        case "color":
            return text
    return text
