"""AddVerificationDialog — capture the screen, pick a region or OCR text, insert an assertion."""

from __future__ import annotations

from collections.abc import Callable

from PIL.Image import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QRadioButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from argus_test_creator.argus_schema import CONDITIONS
from argus_test_creator.argus_schema.conditions import conditions_for
from argus_test_creator.models.capabilities import RecorderCapabilities
from argus_test_creator.models.common import Rect
from argus_test_creator.models.recording import OCRObservation, ScreenCapture
from argus_test_creator.observation.ocr import group_lines
from argus_test_creator.ui.widgets.image_view import ImageView

VISUAL_TYPES = ("text_present", "text_not_present", "image_present", "image_not_present",
                "screenshot_matches", "pixel_matches")
ROLE_RECT = Qt.ItemDataRole.UserRole + 1


class VerificationChoice:
    """What the user decided; the app layer turns it into a step."""

    def __init__(self, *, condition_type: str, text: str | None, region: Rect | None,
                 threshold: float, wait: bool, timeout: str, label: str, case_sensitive: bool,
                 include_region: bool, capture: ScreenCapture) -> None:
        self.condition_type = condition_type
        self.text = text
        self.region = region
        self.threshold = threshold
        self.wait = wait
        self.timeout = timeout
        self.label = label
        self.case_sensitive = case_sensitive
        self.include_region = include_region
        self.capture = capture


class AddVerificationDialog(QDialog):
    def __init__(
        self, capture: ScreenCapture, image: Image, capabilities: RecorderCapabilities | None,
        *, run_ocr: Callable[[Callable[[OCRObservation | None], None]], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Verification")
        self.resize(1100, 640)
        self._capture = capture
        self._image = image
        self.choice: VerificationChoice | None = None

        root = QVBoxLayout(self)
        splitter = QSplitter()
        root.addWidget(splitter, 1)

        # Left: screenshot with region selection
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.view = ImageView(selectable=True)
        self.view.set_image(image)
        self.view.region_selected.connect(self._region_changed)
        left_layout.addWidget(QLabel("Drag a rectangle to select a region (arrow keys nudge, "
                                     "Ctrl+arrows resize)."))
        left_layout.addWidget(self.view, 1)
        self.region_label = QLabel("Region: whole screen")
        left_layout.addWidget(self.region_label)
        splitter.addWidget(left)

        # Right: type, OCR text, options
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("<b>Verification type</b>"))
        self._radios: dict[str, QRadioButton] = {}
        specs = conditions_for(capabilities) if capabilities else list(CONDITIONS.values())
        for spec in specs:
            if spec.name not in VISUAL_TYPES:
                continue
            radio = QRadioButton(spec.label)
            radio.setAccessibleName(spec.label)
            radio.setToolTip(spec.help or spec.name)
            radio.toggled.connect(self._type_changed)
            self._radios[spec.name] = radio
            right_layout.addWidget(radio)
        if not self._radios:
            right_layout.addWidget(QLabel("This target supports no visual verifications."))
        right_layout.addWidget(QLabel("<b>Detected text</b> (select to use)"))
        self.ocr_list = QListWidget()
        self.ocr_list.setAccessibleName("Detected text")
        self.ocr_list.itemSelectionChanged.connect(self._ocr_selected)
        right_layout.addWidget(self.ocr_list, 1)
        self.ocr_status = QLabel("Running OCR…" if run_ocr else "OCR not available")
        right_layout.addWidget(self.ocr_status)

        form = QFormLayout()
        self.text = QLineEdit()
        self.text.setAccessibleName("Text")
        self.text.setPlaceholderText("Text that must be visible")
        self.case_sensitive = QCheckBox("Case sensitive")
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.0, 1.0)
        self.threshold.setSingleStep(0.05)
        self.threshold.setValue(0.90)
        self.threshold.setAccessibleName("Threshold")
        self.label = QLineEdit()
        self.label.setPlaceholderText("Asset name, e.g. batman title")
        self.label.setAccessibleName("Asset name")
        self.include_region = QCheckBox("Restrict search to the selected region")
        self.wait = QCheckBox("Wait for it (wait_until) instead of checking once")
        self.wait.setChecked(True)
        self.timeout = QLineEdit("10s")
        self.timeout.setAccessibleName("Timeout")
        form.addRow("Text", self.text)
        form.addRow("", self.case_sensitive)
        form.addRow("Threshold", self.threshold)
        form.addRow("Asset name", self.label)
        form.addRow("", self.include_region)
        form.addRow("", self.wait)
        form.addRow("Timeout", self.timeout)
        right_layout.addLayout(form)
        self.preview = QLabel("Crop preview")
        self.preview.setMinimumHeight(60)
        right_layout.addWidget(self.preview)
        splitter.addWidget(right)
        splitter.setSizes([700, 400])

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Insert verification")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        first = "text_present" if "text_present" in self._radios else next(iter(self._radios), None)
        if first:
            self._radios[first].setChecked(True)
        self._type_changed()
        if run_ocr is not None:
            run_ocr(self.show_ocr)

    # -- state ----------------------------------------------------------------------

    def selected_type(self) -> str | None:
        for name, radio in self._radios.items():
            if radio.isChecked():
                return name
        return None

    def select_type(self, name: str) -> None:
        self._radios[name].setChecked(True)

    def show_ocr(self, observation: OCRObservation | None) -> None:
        self.ocr_list.clear()
        if observation is None:
            self.ocr_status.setText("OCR unavailable on this target")
            return
        lines = group_lines(observation)
        overlays = []
        for text, rect in lines:
            item = QListWidgetItem(text)
            item.setData(ROLE_RECT, rect)
            self.ocr_list.addItem(item)
            if rect is not None:
                overlays.append((rect, QColor(80, 160, 255)))
        self.view.set_overlays(overlays)
        self.ocr_status.setText(f"{len(lines)} text lines detected ({observation.provider})")

    def _ocr_selected(self) -> None:
        items = self.ocr_list.selectedItems()
        if not items:
            return
        self.text.setText(items[0].text())
        rect = items[0].data(ROLE_RECT)
        if rect is not None and self.selected_type() in ("image_present", "image_not_present",
                                                         "screenshot_matches"):
            self.view.set_selection(rect)
        elif self.selected_type() not in ("text_present", "text_not_present"):
            self.select_type("text_present")

    def _type_changed(self) -> None:
        kind = self.selected_type() or ""
        is_text = kind.startswith("text")
        is_image = kind in ("image_present", "image_not_present", "screenshot_matches")
        self.text.setEnabled(is_text)
        self.case_sensitive.setEnabled(is_text)
        self.threshold.setEnabled(is_image)
        self.label.setEnabled(is_image)
        self.include_region.setEnabled(kind in (
            "image_present", "image_not_present", "text_present", "text_not_present"))
        self.wait.setEnabled(not kind.endswith("not_present"))
        if kind.endswith("not_present"):
            self.wait.setChecked(False)

    def _region_changed(self, region: Rect | None) -> None:
        if region is None:
            self.region_label.setText("Region: whole screen")
            self.preview.setText("Crop preview")
            return
        self.region_label.setText(f"Region: x={region.x} y={region.y} "
                                  f"{region.width}×{region.height}")
        from PIL.ImageQt import ImageQt
        from PySide6.QtGui import QPixmap

        crop = self._image.crop(region.as_box())
        crop.thumbnail((240, 120))
        self.preview.setPixmap(QPixmap.fromImage(ImageQt(crop)))

    def _accept(self) -> None:
        kind = self.selected_type()
        if kind is None:
            QMessageBox.warning(self, "Choose a type", "Select a verification type.")
            return
        region = self.view.selection
        text = self.text.text().strip() or None
        if kind.startswith("text") and not text:
            QMessageBox.warning(self, "Text required", "Enter or select the text to verify.")
            return
        if kind in ("image_present", "image_not_present", "screenshot_matches") and region is None:
            if kind == "screenshot_matches":
                w, h = self.view.image_size
                region = Rect(x=0, y=0, width=w, height=h)
            else:
                QMessageBox.warning(self, "Region required",
                                    "Drag a rectangle around the image to verify.")
                return
        if kind == "pixel_matches" and region is None:
            QMessageBox.warning(self, "Pixel required", "Select the pixel (drag a tiny box).")
            return
        self.choice = VerificationChoice(
            condition_type=kind, text=text, region=region, threshold=self.threshold.value(),
            wait=self.wait.isChecked() and self.wait.isEnabled(),
            timeout=self.timeout.text().strip() or "10s",
            label=self.label.text().strip() or (text or "region"),
            case_sensitive=self.case_sensitive.isChecked(),
            include_region=self.include_region.isChecked() and self.include_region.isEnabled(),
            capture=self._capture,
        )
        self.accept()

