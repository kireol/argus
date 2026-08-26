"""MainWindow — layout, menus, and the glue between the app layer and the widgets.

No business logic lives here: every user action calls a CreatorApp use-case and
every state change arrives as an event through the EventBridge.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Any

from PIL.Image import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from argus_test_creator.app import CreatorApp
from argus_test_creator.app.context import (
    RunFinished,
    RunOutput,
    RunStarted,
    TargetConnected,
    TargetDisconnected,
    ValidationCompleted,
)
from argus_test_creator.authoring.service import DocumentChanged, DocumentReplaced
from argus_test_creator.core.errors import CreatorError
from argus_test_creator.models.authoring import StepDraft, ValidationIssue
from argus_test_creator.models.common import Rect
from argus_test_creator.models.recording import ScreenCapture
from argus_test_creator.recording.session import (
    ActionObserved,
    ActionUpdated,
    AssertionSuggested,
    RecordingFailed,
    RecordingPaused,
    RecordingResumed,
    RecordingStarted,
    RecordingStopped,
    ScreenshotCaptured,
)
from argus_test_creator.serialization import document_to_yaml
from argus_test_creator.ui.bridge import EventBridge, watch
from argus_test_creator.ui.dialogs.common import (
    ProvenanceDialog,
    TargetSettingsDialog,
    about,
    show_error,
    show_recording_failure,
)
from argus_test_creator.ui.dialogs.step_editor import StepEditorDialog
from argus_test_creator.ui.dialogs.verification import AddVerificationDialog
from argus_test_creator.ui.widgets.image_view import ImageView
from argus_test_creator.ui.widgets.metadata_editor import MetadataEditor
from argus_test_creator.ui.widgets.panels import (
    QualityPanel,
    RemotePanel,
    RunPanel,
    SuggestionsPanel,
    ValidationPanel,
    YamlPreview,
    make_button,
)
from argus_test_creator.ui.widgets.step_list import StepListWidget


class MainWindow(QMainWindow):
    def __init__(self, app: CreatorApp) -> None:
        super().__init__()
        self.app = app
        self.setWindowTitle("Argus Test Creator")
        self.resize(1400, 860)
        self._issues: list[ValidationIssue] = []
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._refresh_live)
        self._preview_busy = False
        self._last_capture: ScreenCapture | None = None
        #: Ask before closing with unsaved changes (tests turn this off).
        self.prompt_on_close = True
        self._build_ui()
        self._build_menus()
        self._bridge = EventBridge(app.events, self)
        self._wire_events()
        self._refresh_all()

    # -- construction ---------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Target / Device</b>"))
        self.target_combo = QComboBox()
        self.target_combo.setAccessibleName("Target")
        self.target_combo.setMinimumWidth(320)
        for target in self.app.targets.all():
            self.target_combo.addItem(target.name, target.id)
        self.target_combo.currentIndexChanged.connect(self._target_chosen)
        header.addWidget(self.target_combo)
        header.addWidget(make_button("Settings…", self._target_settings))
        self.connect_button = make_button("Connect", self._toggle_connect, shortcut="Ctrl+K")
        header.addWidget(self.connect_button)
        header.addStretch(1)
        self.connection_label = QLabel("○ Disconnected")
        self.connection_label.setAccessibleName("Connection status")
        header.addWidget(self.connection_label)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        live = QWidget()
        live_layout = QVBoxLayout(live)
        live_layout.addWidget(QLabel("<b>LIVE TARGET</b> (click to tap when recording)"))
        self.live_view = ImageView()
        self.live_view.clicked.connect(self._live_clicked)
        live_layout.addWidget(self.live_view, 1)
        self.remote = RemotePanel()
        self.remote.key_pressed.connect(self._remote_key)
        self.remote.text_entered.connect(self._remote_text)
        live_layout.addWidget(self.remote)
        splitter.addWidget(live)

        steps = QWidget()
        steps_layout = QVBoxLayout(steps)
        steps_layout.addWidget(QLabel("<b>TEST STEPS</b>"))
        self.step_list = StepListWidget()
        self.step_list.edit_requested.connect(self._edit_step)
        self.step_list.delete_requested.connect(self._delete_step)
        self.step_list.duplicate_requested.connect(self._duplicate_step)
        self.step_list.toggle_requested.connect(self._toggle_step)
        self.step_list.convert_requested.connect(self._convert_step)
        self.step_list.provenance_requested.connect(self._show_provenance)
        self.step_list.rename_requested.connect(self._rename_step)
        self.step_list.reordered.connect(self._reordered)
        steps_layout.addWidget(self.step_list, 1)
        self.suggestions = SuggestionsPanel()
        self.suggestions.accept_requested.connect(self._accept_suggestion)
        steps_layout.addWidget(self.suggestions)
        splitter.addWidget(steps)
        splitter.setSizes([760, 560])
        root.addWidget(splitter, 1)

        toolbar = QHBoxLayout()
        self.record_button = make_button("● Record", self._record, shortcut="Ctrl+R")
        self.pause_button = make_button("Pause", self._pause, shortcut="Ctrl+P")
        self.stop_button = make_button("■ Stop", self._stop, shortcut="Ctrl+.")
        self.add_step_button = make_button("Add Step", self._add_step, shortcut="Ctrl+N")
        self.verify_button = make_button("Add Verification", self._add_verification,
                                         shortcut="Ctrl+Shift+V")
        self.undo_button = make_button("Undo", self._undo, shortcut=QKeySequence.StandardKey.Undo)
        self.redo_button = make_button("Redo", self._redo, shortcut=QKeySequence.StandardKey.Redo)
        for button in (self.record_button, self.pause_button, self.stop_button,
                       self.add_step_button, self.verify_button, self.undo_button,
                       self.redo_button):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        toolbar.addWidget(make_button("Save", self._save, shortcut="Ctrl+S"))
        toolbar.addWidget(make_button("Validate", self._validate, shortcut="Ctrl+Shift+L"))
        toolbar.addWidget(make_button("Run with Argus", self._run, shortcut="Ctrl+Return"))
        root.addLayout(toolbar)
        self.setCentralWidget(central)

        self.tabs = QTabWidget()
        self.metadata = MetadataEditor()
        self.metadata.changed.connect(self._metadata_changed)
        self.yaml = YamlPreview()
        self.validation = ValidationPanel()
        self.validation.issue_activated.connect(self._focus_issue)
        self.quality = QualityPanel()
        self.run_panel = RunPanel()
        self.run_panel.open_report_requested.connect(lambda p: webbrowser.open(Path(p).as_uri()))
        self.tabs.addTab(self.metadata, "Test details")
        self.tabs.addTab(self.yaml, "YAML")
        self.tabs.addTab(self.validation, "Validation")
        self.tabs.addTab(self.quality, "Quality")
        self.tabs.addTab(self.run_panel, "Run")
        dock = QDockWidget("Details", self)
        dock.setWidget(self.tabs)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        dock.setMinimumHeight(240)
        self.statusBar().showMessage("Status: Idle")

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        self._action(file_menu, "New Project…", self._new_project, "Ctrl+Shift+N")
        self._action(file_menu, "Open Project…", self._open_project, "Ctrl+O")
        file_menu.addSeparator()
        self._action(file_menu, "New Test", self._new_test)
        self._action(file_menu, "Open Test…", self._open_test)
        self._action(file_menu, "Import Argus YAML…", self._import_yaml)
        file_menu.addSeparator()
        self._action(file_menu, "Save", self._save, "Ctrl+S")
        self._action(file_menu, "Export YAML…", self._export)
        file_menu.addSeparator()
        self._action(file_menu, "Quit", self.close, "Ctrl+Q")
        edit_menu = bar.addMenu("&Edit")
        self._action(edit_menu, "Undo", self._undo, QKeySequence.StandardKey.Undo)
        self._action(edit_menu, "Redo", self._redo, QKeySequence.StandardKey.Redo)
        target_menu = bar.addMenu("&Target")
        self._action(target_menu, "Connect / Disconnect", self._toggle_connect, "Ctrl+K")
        self._action(target_menu, "Recover interrupted recording…", self._recover)
        run_menu = bar.addMenu("&Run")
        self._action(run_menu, "Validate", self._validate)
        self._action(run_menu, "Validate with Argus", lambda: self._validate(with_argus=True))
        self._action(run_menu, "Run with Argus", self._run)
        self._action(run_menu, "Doctor", self._doctor)
        help_menu = bar.addMenu("&Help")
        self._action(help_menu, "About", lambda: about(self))

    def _action(self, menu, label: str, handler, shortcut: Any = None) -> QAction:
        action = QAction(label, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(handler)
        menu.addAction(action)
        return action

    def _wire_events(self) -> None:
        b = self._bridge
        b.on(DocumentChanged, lambda _e: self._refresh_document())
        b.on(DocumentReplaced, lambda _e: self._refresh_all())
        b.on(TargetConnected, lambda _e: self._connection_changed(True))
        b.on(TargetDisconnected, lambda _e: self._connection_changed(False))
        b.on(RecordingStarted, lambda _e: self._set_status("Recording"))
        b.on(RecordingPaused, lambda _e: self._set_status("Paused"))
        b.on(RecordingResumed, lambda _e: self._set_status("Recording"))
        b.on(RecordingStopped, lambda e: self._set_status(
            f"Stopped — {e.action_count} actions from {e.event_count} events"))
        b.on(ActionObserved, lambda e: self._set_status(f"Recording — {e.action.describe()}"))
        b.on(ActionUpdated, lambda e: self._set_status(f"Recording — {e.action.describe()}"))
        b.on(ScreenshotCaptured, self._screenshot_captured)
        b.on(AssertionSuggested, lambda e: self.suggestions.add_candidates(e.candidates))
        b.on(RecordingFailed, self._recording_failed)
        b.on(ValidationCompleted, lambda e: self._show_issues(e.issues, e.argus_checked))
        b.on(RunStarted, lambda e: self.run_panel.start(e.test_id))
        b.on(RunOutput, lambda e: self.run_panel.append(e.line))
        b.on(RunFinished, lambda e: self.run_panel.finish(e.result))

    # -- refresh ---------------------------------------------------------------------

    def _refresh_all(self) -> None:
        document = self.app.authoring.document
        self.metadata.load(document.metadata)
        if document.target is not None:
            index = self.target_combo.findData(document.target.id)
            if index >= 0 and index != self.target_combo.currentIndex():
                self.target_combo.blockSignals(True)
                self.target_combo.setCurrentIndex(index)
                self.target_combo.blockSignals(False)
        self._refresh_document()
        self._update_buttons()

    def _refresh_document(self) -> None:
        document = self.app.authoring.document
        pristine = not document.steps and not document.metadata.id and not document.metadata.name
        self._issues = [] if pristine and self.app.authoring.revision == 0 else self.app.validate()
        self.step_list.refresh(document, self._issues)
        self.metadata.load(document.metadata)
        self.metadata.show_issues(self._issues)
        try:
            self.yaml.show_yaml(document_to_yaml(document))
        except Exception as exc:  # noqa: BLE001 - never let preview break the UI
            self.yaml.show_yaml(f"# YAML preview unavailable: {exc}")
        self.validation.show_issues(self._issues)
        self.quality.show_report(self.app.quality())
        title = document.metadata.id or "untitled"
        dirty = "*" if self.app.authoring.dirty else ""
        project = self.app.project.info().name if self.app.project else "no project"
        self.setWindowTitle(f"Argus Test Creator — {title}{dirty} ({project})")
        self._update_buttons()

    def _update_buttons(self) -> None:
        recording = self.app.session is not None and self.app.session.state.value in (
            "recording", "paused")
        connected = self.app.recorder is not None and self.app.recorder.connected
        caps = self.app.recorder.capabilities if self.app.recorder else None
        self.record_button.setEnabled(not recording and self.app.project is not None)
        self.pause_button.setEnabled(recording)
        self.pause_button.setText("Resume" if recording and self.app.session.state.value == "paused"  # type: ignore[union-attr]
                                  else "Pause")
        self.stop_button.setEnabled(recording)
        self.verify_button.setEnabled(connected and bool(caps and caps.supports_screenshot))
        self.undo_button.setEnabled(self.app.authoring.can_undo)
        self.redo_button.setEnabled(self.app.authoring.can_redo)
        self.undo_button.setToolTip(f"Undo {self.app.authoring.undo_label or ''}".strip())
        self.redo_button.setToolTip(f"Redo {self.app.authoring.redo_label or ''}".strip())
        controllable = hasattr(self.app.recorder, "send_key")
        self.remote.setVisible(controllable and bool(caps and caps.supports_keyboard))
        self.connect_button.setText("Disconnect" if connected else "Connect")

    def _set_status(self, text: str) -> None:
        self.statusBar().showMessage(f"Status: {text}")
        self._update_buttons()

    def _connection_changed(self, connected: bool) -> None:
        name = self.app.recorder.target.name if self.app.recorder else ""
        self.connection_label.setText(f"● Connected — {name}" if connected else "○ Disconnected")
        if connected:
            fps = self.app.config.recording.live_preview_fps
            self._preview_timer.start(int(1000 / fps))
            limitations = self.app.recorder.describe_limitations() if self.app.recorder else []
            if limitations:
                self.statusBar().showMessage("Limitations: " + " ".join(limitations), 8000)
        else:
            self._preview_timer.stop()
        self._update_buttons()

    def _refresh_live(self) -> None:
        recorder = self.app.recorder
        if recorder is None or not recorder.connected or self._preview_busy:
            return
        self._preview_busy = True
        job = self.app.workers.submit("live-preview", recorder.screenshot)
        watch(job, self._live_frame, self._live_error, self)

    def _live_frame(self, image: Image) -> None:
        self._preview_busy = False
        self.live_view.set_image(image)

    def _live_error(self, exc: BaseException) -> None:
        self._preview_busy = False
        self._preview_timer.stop()
        self.statusBar().showMessage(f"Live preview stopped: {exc}", 6000)

    def _screenshot_captured(self, event: ScreenshotCaptured) -> None:
        self._last_capture = event.capture

    def _show_issues(self, issues: list[ValidationIssue], argus_checked: bool) -> None:
        self._issues = issues
        self.validation.show_issues(issues, argus_checked=argus_checked)
        self.step_list.refresh(self.app.authoring.document, issues)
        self.metadata.show_issues(issues)

    def _recording_failed(self, event: RecordingFailed) -> None:
        target = self.app.recorder.target.name if self.app.recorder else "?"
        if self.app.config.diagnostic or event.operation != "screenshot":
            show_recording_failure(self, target, event.operation, event.message,
                                   event.remediation, event.details)
        else:
            self.statusBar().showMessage(f"{event.operation} failed: {event.message}", 8000)

    # -- target -----------------------------------------------------------------------

    def _target_chosen(self) -> None:
        target_id = self.target_combo.currentData()
        if not target_id:
            return
        try:
            self.app.select_target(target_id)
        except CreatorError as exc:
            show_error(self, "Target", exc)
        self._connection_changed(False)
        self._refresh_document()

    def _target_settings(self) -> None:
        if self.app.recorder is None:
            self._target_chosen()
        if self.app.recorder is None:
            return
        dialog = TargetSettingsDialog(self.app.recorder.target, self)
        if dialog.exec():
            try:
                self.app.select_target(self.app.recorder.target.id, dialog.settings())
            except CreatorError as exc:
                show_error(self, "Target settings", exc)
            self._connection_changed(False)

    def _toggle_connect(self) -> None:
        if self.app.recorder is None:
            self._target_chosen()
        if self.app.recorder is None:
            return
        try:
            if self.app.recorder.connected:
                self.app.disconnect_target()
            else:
                self.app.connect_target()
        except CreatorError as exc:
            show_error(self, "Connection", exc)
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Connection", exc)

    def _recover(self) -> None:
        dirs = self.app.recoverable_sessions()
        if not dirs:
            QMessageBox.information(self, "Recover", "No interrupted recordings found.")
            return
        names = [d.name for d in dirs]
        name, ok = QInputDialog.getItem(self, "Recover recording", "Session:", names, 0, False)
        if not ok:
            return
        try:
            if self.app.recorder is None:
                self._target_chosen()
            session = self.app.recover_session(dirs[names.index(name)])
            steps = self.app.append_actions(session.actions)
            QMessageBox.information(self, "Recovered", f"Recovered {len(steps)} steps.")
        except CreatorError as exc:
            show_error(self, "Recover", exc)

    # -- recording -------------------------------------------------------------------------

    def _record(self) -> None:
        if self.app.project is None:
            QMessageBox.information(self, "Record", "Create or open a project first (File menu).")
            return
        if self.app.recorder is None:
            self._target_chosen()
        try:
            self.app.start_recording()
        except CreatorError as exc:
            show_error(self, "Record", exc)
        self._update_buttons()

    def _pause(self) -> None:
        session = self.app.session
        if session is None:
            return
        if session.state.value == "paused":
            self.app.resume_recording()
        else:
            self.app.pause_recording()
        self._update_buttons()

    def _stop(self) -> None:
        try:
            steps = self.app.stop_recording()
        except CreatorError as exc:
            show_error(self, "Stop", exc)
            return
        self.statusBar().showMessage(f"Added {len(steps)} steps from the recording")
        self._refresh_document()

    def _live_clicked(self, x: int, y: int) -> None:
        recorder = self.app.recorder
        if recorder is None or not hasattr(recorder, "send_tap"):
            return
        if self.app.session is None or self.app.session.state.value != "recording":
            self.statusBar().showMessage("Press Record to send taps to the target", 3000)
            return
        try:
            recorder.send_tap(x, y)
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Tap", exc)

    def _remote_key(self, key: str) -> None:
        recorder = self.app.recorder
        if recorder is not None and hasattr(recorder, "send_key"):
            try:
                recorder.send_key(key)
            except Exception as exc:  # noqa: BLE001
                show_error(self, "Key", exc)

    def _remote_text(self, text: str) -> None:
        recorder = self.app.recorder
        if recorder is not None and hasattr(recorder, "send_text"):
            recorder.send_text(text)

    # -- steps ------------------------------------------------------------------------------

    def _selected_index(self) -> int | None:
        step_id = self.step_list.selected_step_id()
        if step_id is None:
            return None
        return self.app.authoring.document.step_index(step_id) + 1

    def _add_step(self) -> None:
        caps = self.app.recorder.capabilities if self.app.recorder else None
        draft = StepDraft(action="device.tap")
        dialog = StepEditorDialog(draft, caps, self)
        dialog.setWindowTitle("Add step")
        if dialog.exec() and dialog.result_step is not None:
            step = dialog.result_step
            self.app.authoring.add_step(step.action, step.params, name=step.name,
                                        condition=step.condition, index=self._selected_index(),
                                        notes=step.notes)

    def _edit_step(self, step_id: str) -> None:
        step = self.app.authoring.document.find_step(step_id)
        caps = self.app.recorder.capabilities if self.app.recorder else None
        dialog = StepEditorDialog(step, caps, self)
        if dialog.exec() and dialog.result_step is not None:
            new = dialog.result_step
            self.app.authoring.edit_step(step_id, action=new.action, name=new.name,
                                         params=new.params, condition=new.condition,
                                         enabled=new.enabled, notes=new.notes, custom=new.custom)

    def _delete_step(self, step_id: str) -> None:
        self.app.authoring.delete_step(step_id)

    def _duplicate_step(self, step_id: str) -> None:
        self.app.authoring.duplicate_step(step_id)

    def _toggle_step(self, step_id: str) -> None:
        step = self.app.authoring.document.find_step(step_id)
        self.app.authoring.set_step_enabled(step_id, not step.enabled)

    def _convert_step(self, step_id: str) -> None:
        self.app.authoring.convert_to_wait_until(step_id)

    def _rename_step(self, step_id: str) -> None:
        step = self.app.authoring.document.find_step(step_id)
        name, ok = QInputDialog.getText(self, "Rename step", "Name:", text=step.display_name())
        if ok:
            self.app.authoring.rename_step(step_id, name.strip())

    def _show_provenance(self, step_id: str) -> None:
        ProvenanceDialog(self.app.authoring.document.find_step(step_id), self).exec()

    def _reordered(self, step_id: str, index: int) -> None:
        self.app.authoring.move_step(step_id, index)

    def _metadata_changed(self, field: str, value: object) -> None:
        self.app.authoring.set_metadata(**{field: value})

    def _undo(self) -> None:
        self.app.authoring.undo()

    def _redo(self) -> None:
        self.app.authoring.redo()

    # -- verification ------------------------------------------------------------------------

    def _add_verification(self) -> None:
        try:
            job = self.app.capture_screen()
        except CreatorError as exc:
            show_error(self, "Add Verification", exc)
            return
        self.statusBar().showMessage("Capturing screen…")
        watch(job, self._open_verification, lambda exc: show_error(self, "Capture", exc), self)

    def _open_verification(self, capture: ScreenCapture) -> None:
        self.statusBar().showMessage("Status: Ready")
        image = self.app.load_capture(capture)
        caps = self.app.recorder.capabilities if self.app.recorder else None

        def run_ocr(callback) -> None:
            if not caps or not caps.supports_ocr or self.app.ocr is None:
                callback(None)
                return
            watch(self.app.run_ocr(capture), callback, lambda _e: callback(None), self)

        dialog = AddVerificationDialog(capture, image, caps, run_ocr=run_ocr, parent=self)
        if dialog.exec() and dialog.choice is not None:
            self.apply_verification_choice(dialog.choice)

    def apply_verification_choice(self, choice) -> None:
        index = self._selected_index()
        try:
            if choice.condition_type in ("text_present", "text_not_present"):
                self.app.add_text_verification(
                    choice.text or "", region=choice.region if choice.include_region else None,
                    negated=choice.condition_type == "text_not_present",
                    case_sensitive=choice.case_sensitive, wait=choice.wait,
                    timeout=choice.timeout, capture=choice.capture, index=index,
                )
            elif choice.condition_type == "pixel_matches":
                region = choice.region or Rect(x=0, y=0, width=1, height=1)
                image = self.app.load_capture(choice.capture)
                pixel = image.convert("RGB").getpixel((region.x, region.y))
                assert isinstance(pixel, tuple)
                r, g, b = pixel[:3]
                from argus_test_creator.models.authoring import ConditionDraft

                self.app.authoring.add_verification(
                    ConditionDraft(type="pixel_matches",
                                   params={"x": region.x, "y": region.y,
                                           "color": f"#{r:02x}{g:02x}{b:02x}"}),
                    wait=choice.wait, timeout=choice.timeout, index=index,
                )
            else:
                assert choice.region is not None
                self.app.add_image_verification(
                    choice.capture, choice.region, label=choice.label,
                    condition_type=choice.condition_type, threshold=choice.threshold,
                    wait=choice.wait, timeout=choice.timeout,
                    include_region=choice.include_region, index=index,
                )
        except CreatorError as exc:
            show_error(self, "Add Verification", exc)

    def _accept_suggestion(self, candidate) -> None:
        try:
            self.app.accept_suggestion(candidate, index=self._selected_index())
        except CreatorError as exc:
            show_error(self, "Suggestion", exc)

    # -- project / files ------------------------------------------------------------------------

    def _new_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose an empty folder for the project")
        if not directory:
            return
        try:
            self.app.create_project(Path(directory))
            self.app.new_test()
        except CreatorError as exc:
            show_error(self, "New project", exc)
        self._refresh_all()

    def _open_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open project folder")
        if not directory:
            return
        self.open_project(Path(directory))

    def open_project(self, root: Path, test_id: str | None = None) -> None:
        try:
            project = self.app.open_project(root)
            ids = project.list_test_ids()
            if test_id or ids:
                self.app.open_test(test_id or ids[0])
            else:
                self.app.new_test()
        except CreatorError as exc:
            show_error(self, "Open project", exc)
        self._refresh_all()
        if self.app.recoverable_sessions():
            self.statusBar().showMessage(
                "An interrupted recording can be recovered (Target → Recover).", 10000)

    def _new_test(self) -> None:
        if not self._confirm_discard():
            return
        self.app.new_test()
        self._refresh_all()

    def _open_test(self) -> None:
        if self.app.project is None:
            QMessageBox.information(self, "Open test", "Open a project first.")
            return
        ids = self.app.project.list_test_ids()
        if not ids:
            QMessageBox.information(self, "Open test", "The project has no tests yet.")
            return
        test_id, ok = QInputDialog.getItem(self, "Open test", "Test:", ids, 0, False)
        if ok and self._confirm_discard():
            try:
                self.app.open_test(test_id)
            except CreatorError as exc:
                show_error(self, "Open test", exc)
            self._refresh_all()

    def _import_yaml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Argus test", "", "YAML (*.yaml *.yml)")
        if path and self._confirm_discard():
            try:
                self.app.import_yaml(Path(path))
            except CreatorError as exc:
                show_error(self, "Import", exc)
            self._refresh_all()

    def _save(self) -> bool:
        if self.app.project is None:
            self._new_project()
            if self.app.project is None:
                return False
        try:
            path = self.app.save_test()
        except CreatorError as exc:
            show_error(self, "Save", exc)
            return False
        self.statusBar().showMessage(f"Saved {path}", 5000)
        self._refresh_document()
        return True

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export YAML", "", "YAML (*.yaml)")
        if path:
            try:
                self.app.export_yaml(Path(path))
            except CreatorError as exc:
                show_error(self, "Export", exc)

    def _confirm_discard(self) -> bool:
        if not self.app.authoring.dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes", "Discard unsaved changes to the current test?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    # -- validate / run --------------------------------------------------------------------------

    def _validate(self, with_argus: bool = False) -> None:
        self.tabs.setCurrentWidget(self.validation)
        if not with_argus:
            self.app.validate()
            return
        try:
            job = self.app.workers.submit("argus-validate", self.app.validate, with_argus=True)
        except CreatorError as exc:
            show_error(self, "Validate", exc)
            return
        watch(job, lambda _issues: None, lambda exc: show_error(self, "Validate", exc), self)

    def _run(self) -> None:
        self.tabs.setCurrentWidget(self.run_panel)
        try:
            job = self.app.run_with_argus()
        except CreatorError as exc:
            show_error(self, "Run with Argus", exc)
            return
        watch(job, lambda _r: None, lambda exc: show_error(self, "Run with Argus", exc), self)

    def _doctor(self) -> None:
        from argus_test_creator.cli.doctor import run_doctor

        report = run_doctor(self.app.project.root if self.app.project else None)
        lines = []
        for section, items in report.items():
            lines.append(section)
            for state, name, detail in items:
                symbol = {"ok": "✓", "warn": "⚠", "fail": "✗"}[state]
                lines.append(f"  {symbol} {name}  {detail}")
            lines.append("")
        QMessageBox.information(self, "Doctor", "\n".join(lines))

    def _focus_issue(self, issue: ValidationIssue) -> None:
        if issue.step_id:
            self.step_list.refresh(self.app.authoring.document, self._issues, select=issue.step_id)
            self.step_list.setFocus()
        else:
            self.tabs.setCurrentWidget(self.metadata)

    # -- lifecycle ---------------------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.app.authoring.dirty and self.prompt_on_close:
            answer = QMessageBox.question(
                self, "Unsaved changes", "Save before closing?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save and not self._save():
                event.ignore()
                return
        self._preview_timer.stop()
        self._bridge.close()
        self.app.shutdown()
        event.accept()
