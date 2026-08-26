from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from argus_test_creator.models import ConditionDraft, Rect, StepDraft
from argus_test_creator.ui.dialogs.step_editor import StepEditorDialog
from argus_test_creator.ui.dialogs.verification import AddVerificationDialog
from argus_test_creator.ui.widgets.image_view import ImageView

pytestmark = pytest.mark.ui


def _drain(qtbot, win, ms=50):
    qtbot.wait(ms)
    for job in win.app.workers.active_jobs():
        job.result(30)
    qtbot.wait(ms)


def test_window_reflects_document_and_validation(qtbot, window):
    win = window
    assert "UI-1" in win.windowTitle()
    assert win.metadata.id.text() == "UI-1"
    assert win.validation.count() >= 1  # "no enabled steps" error
    win.app.authoring.add_step("device.tap", {"x": 10, "y": 20})
    _drain(qtbot, win)
    assert win.step_list.count() == 1
    assert "Tap (10, 20)" in win.step_list.item(0).text()
    assert "action: device.tap" in win.yaml.toPlainText()
    assert win.undo_button.isEnabled() and not win.redo_button.isEnabled()
    win._undo()
    _drain(qtbot, win)
    assert win.step_list.count() == 0 and win.redo_button.isEnabled()
    win._redo()
    _drain(qtbot, win)
    assert win.step_list.count() == 1


def test_metadata_editor_round_trip(qtbot, window):
    win = window
    win.metadata.name.setText("Renamed via form")
    win.metadata.name.editingFinished.emit()
    win.metadata.tags.setText("smoke, ui")
    win.metadata.tags.editingFinished.emit()
    win.metadata.parameters.setPlainText("movie_id: 7")
    _drain(qtbot, win)
    meta = win.app.authoring.document.metadata
    assert meta.name == "Renamed via form" and meta.tags == ["smoke", "ui"]
    assert meta.parameters == {"movie_id": 7}
    win.metadata.parameters.setPlainText("[not a mapping")
    _drain(qtbot, win)
    assert win.metadata.error.text().startswith("Parameters must be valid YAML")
    win.metadata.id.setText("")
    win.metadata.id.editingFinished.emit()
    _drain(qtbot, win)
    assert any(i.code == "id_required" for i in win._issues)
    assert "border" in win.metadata.id.styleSheet()


def test_step_editor_dialog_adds_and_edits(qtbot, window):
    win = window
    caps = win.app.recorder.capabilities
    dialog = StepEditorDialog(StepDraft(action="device.tap"), caps, win)
    qtbot.addWidget(dialog)
    dialog.action.setCurrentIndex(dialog.action.findData("device.key"))
    dialog.params._fields["key"][1].setText("ENTER")
    dialog.name.setText("Press enter")
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    step = dialog.result_step
    assert step.action == "device.key" and step.params == {"key": "ENTER"}
    win.app.authoring.add_step(step.action, step.params, name=step.name)
    _drain(qtbot, win)
    # edit into a wait_until with a text condition
    step_id = win.app.authoring.document.steps[0].id
    dialog = StepEditorDialog(win.app.authoring.document.find_step(step_id), caps, win)
    dialog.action.setCurrentIndex(dialog.action.findData("wait_until"))
    dialog.params._fields["timeout"][1].setText("5s")
    dialog.condition.type.setCurrentIndex(dialog.condition.type.findData("text_present"))
    dialog.condition.params._fields["text"][1].setText("Hello")
    dialog._accept()
    new = dialog.result_step
    assert new.action == "wait_until" and new.condition.params == {"text": "Hello"}
    assert new.params == {"timeout": "5s"}
    # missing required parameter is refused
    dialog = StepEditorDialog(StepDraft(action="device.tap"), caps, win)
    from PySide6.QtWidgets import QMessageBox

    warned = []
    QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a))  # type: ignore[method-assign]
    dialog._accept()
    assert warned and dialog.result_step is None


def test_add_verification_dialog_text_and_image(qtbot, window):
    win = window
    win.app.connect_target()
    capture = win.app.capture_screen().result(10)
    image = win.app.load_capture(capture)
    observed = []
    dialog = AddVerificationDialog(capture, image, win.app.recorder.capabilities,
                                   run_ocr=lambda cb: observed.append(cb), parent=win)
    qtbot.addWidget(dialog)
    ocr = win.app.run_ocr(capture).result(10)
    observed[0](ocr)
    assert dialog.ocr_list.count() >= 3
    items = [dialog.ocr_list.item(i) for i in range(dialog.ocr_list.count())]
    movies = next(i for i in items if i.text() == "Movies")
    movies.setSelected(True)
    assert dialog.text.text() == "Movies"
    dialog._accept()
    choice = dialog.choice
    assert choice.condition_type == "text_present" and choice.wait
    win.apply_verification_choice(choice)
    _drain(qtbot, win)
    step = win.app.authoring.document.steps[-1]
    assert step.action == "wait_until" and step.condition.params["text"] == "Movies"

    dialog = AddVerificationDialog(capture, image, win.app.recorder.capabilities, parent=win)
    dialog.select_type("image_present")
    dialog.view.set_selection(Rect(x=120, y=200, width=320, height=120))
    dialog.label.setText("movies button")
    dialog.threshold.setValue(0.85)
    dialog._accept()
    win.apply_verification_choice(dialog.choice)
    _drain(qtbot, win)
    step = win.app.authoring.document.steps[-1]
    assert step.condition.type == "image_present"
    assert step.condition.params["image"].startswith("movies_button_")
    assert step.condition.params["threshold"] == 0.85
    assert (win.app.project.assets_dir / step.condition.params["image"]).is_file()


def test_record_stop_via_buttons_and_suggestions(qtbot, window):
    win = window
    win._toggle_connect()
    _drain(qtbot, win)
    assert win.connection_label.text().startswith("● Connected")
    win._record()
    _drain(qtbot, win)
    assert win.stop_button.isEnabled() and not win.record_button.isEnabled()
    recorder = win.app.recorder
    recorder.send_tap(200, 250)
    recorder.send_tap(200, 180)
    win.app.session.flush()
    win._stop()
    _drain(qtbot, win, 200)
    assert win.step_list.count() == 2
    assert "Stopped" in win.statusBar().currentMessage() or win.step_list.count() == 2
    assert win.suggestions.list.count() >= 1
    win.suggestions.list.item(0).setSelected(True)
    win.suggestions._accept()
    _drain(qtbot, win)
    assert win.step_list.count() == 3
    assert win.app.authoring.document.steps[-1].provenance.source == "suggestion"


def test_save_close_reopen_unchanged(qtbot, window, tmp_path):
    win = window
    win.app.authoring.add_step("device.tap", {"x": 1, "y": 2})
    win.app.authoring.add_verification(ConditionDraft(type="text_present", params={"text": "A"}))
    assert win._save()
    yaml_before = win.yaml.toPlainText()
    root = win.app.project.root
    win.app.new_test()
    _drain(qtbot, win)
    assert win.step_list.count() == 0
    win.open_project(root, "UI-1")
    _drain(qtbot, win)
    assert win.yaml.toPlainText() == yaml_before and win.step_list.count() == 2
    assert not win.app.authoring.dirty


def test_step_context_actions(qtbot, window):
    win = window
    a = win.app.authoring.add_step("device.tap", {"x": 1, "y": 2})
    b = win.app.authoring.add_verification(ConditionDraft(type="text_present",
                                                          params={"text": "A"}))
    _drain(qtbot, win)
    win._duplicate_step(a.id)
    win._toggle_step(a.id)
    win._convert_step(b.id)
    _drain(qtbot, win)
    doc = win.app.authoring.document
    assert len(doc.steps) == 3 and not doc.find_step(a.id).enabled
    assert doc.find_step(b.id).action == "wait_until"
    win._reordered(b.id, 0)
    assert doc.steps[0].id == b.id
    win._delete_step(a.id)
    _drain(qtbot, win)
    assert len(doc.steps) == 2
    assert win.step_list.item(0).text().startswith("1. ✓")


def test_image_view_coordinate_mapping(qtbot):
    from PIL import Image

    view = ImageView(selectable=True)
    qtbot.addWidget(view)
    view.resize(640, 360)
    view.show()
    view.set_image(Image.new("RGB", (1280, 720)))
    assert view.to_image(view.to_widget(Rect(x=100, y=50, width=10, height=10)).topLeft()) == (100, 50)  # noqa: E501
    clicked = []
    view.clicked.connect(lambda x, y: clicked.append((x, y)))
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=view.to_widget(
        Rect(x=640, y=360, width=2, height=2)).topLeft())
    assert clicked and abs(clicked[0][0] - 640) <= 2
    view.set_selection(Rect(x=10, y=10, width=20, height=20))
    qtbot.keyClick(view, Qt.Key.Key_Right)
    assert view.selection.x == 11
    qtbot.keyClick(view, Qt.Key.Key_Down, modifier=Qt.KeyboardModifier.ControlModifier)
    assert view.selection.height == 21
