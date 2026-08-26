"""ImageView — shows a screenshot scaled to fit, with optional rubber-band region selection.

Coordinates reported to callers are always *image* pixels, never widget pixels.
"""

from __future__ import annotations

from PIL.Image import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from argus_test_creator.models.common import Rect


class ImageView(QWidget):
    clicked = Signal(int, int)             # image coordinates
    region_selected = Signal(object)       # Rect | None

    def __init__(self, parent: QWidget | None = None, *, selectable: bool = False) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._image_size = (0, 0)
        self._selectable = selectable
        self._drag_start: QPoint | None = None
        self._selection: Rect | None = None
        self._overlays: list[tuple[Rect, QColor]] = []
        self.setMinimumSize(240, 135)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Screen view")

    # -- content ------------------------------------------------------------------

    def set_image(self, image: Image | None) -> None:
        if image is None:
            self._pixmap = None
            self._image_size = (0, 0)
        else:
            self._pixmap = QPixmap.fromImage(ImageQt(image.convert("RGB")))
            self._image_size = image.size
        self.update()

    def set_overlays(self, overlays: list[tuple[Rect, QColor]]) -> None:
        self._overlays = overlays
        self.update()

    @property
    def selection(self) -> Rect | None:
        return self._selection

    def set_selection(self, region: Rect | None) -> None:
        self._selection = region
        self.update()
        self.region_selected.emit(region)

    @property
    def image_size(self) -> tuple[int, int]:
        return self._image_size

    # -- geometry -----------------------------------------------------------------

    def _target_rect(self) -> QRect:
        if self._pixmap is None or self._pixmap.isNull():
            return QRect()
        w, h = self._image_size
        scale = min(self.width() / w, self.height() / h)
        tw, th = max(int(w * scale), 1), max(int(h * scale), 1)
        return QRect((self.width() - tw) // 2, (self.height() - th) // 2, tw, th)

    def to_image(self, point: QPoint) -> tuple[int, int] | None:
        target = self._target_rect()
        if target.isNull():
            return None
        w, h = self._image_size
        x = int((point.x() - target.x()) * w / target.width())
        y = int((point.y() - target.y()) * h / target.height())
        return max(0, min(x, w - 1)), max(0, min(y, h - 1))

    def to_widget(self, region: Rect) -> QRect:
        target = self._target_rect()
        w, h = self._image_size
        sx, sy = target.width() / w, target.height() / h
        return QRect(int(target.x() + region.x * sx), int(target.y() + region.y * sy),
                     max(int(region.width * sx), 1), max(int(region.height * sy), 1))

    # -- events -------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 28))
        if self._pixmap is None:
            painter.setPen(QColor(150, 150, 160))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No screen captured yet")
            return
        target = self._target_rect()
        painter.drawPixmap(target, self._pixmap)
        for region, color in self._overlays:
            painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
            painter.drawRect(self.to_widget(region))
        if self._selection is not None:
            painter.setPen(QPen(QColor(250, 190, 40), 2))
            painter.setBrush(QColor(250, 190, 40, 40))
            painter.drawRect(self.to_widget(self._selection))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._pixmap is None:
            return
        pos = event.position().toPoint()
        if self._selectable:
            self._drag_start = pos
            self._selection = None
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_start is None:
            return
        self._update_selection(event.position().toPoint())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._pixmap is None:
            return
        pos = event.position().toPoint()
        if self._drag_start is not None:
            moved = (pos - self._drag_start).manhattanLength() > 4
            start = self._drag_start
            self._drag_start = None
            if moved:
                self._update_selection(pos)
                self.region_selected.emit(self._selection)
                return
            pos = start
        image_pos = self.to_image(pos)
        if image_pos is not None:
            self.clicked.emit(*image_pos)

    def _update_selection(self, pos: QPoint) -> None:
        assert self._drag_start is not None
        a = self.to_image(self._drag_start)
        b = self.to_image(pos)
        if a is None or b is None:
            return
        self._selection = Rect.from_points(a[0], a[1], b[0], b[1])
        self.update()

    # keyboard: arrows nudge/resize selection so the region tool works without a mouse
    def keyPressEvent(self, event) -> None:  # noqa: N802
        sel = self._selection
        if sel is None or self._pixmap is None:
            super().keyPressEvent(event)
            return
        dx = dy = dw = dh = 0
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        key = event.key()
        resize = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Left:
            dw, dx = (-step, 0) if resize else (0, -step)
        elif key == Qt.Key.Key_Right:
            dw, dx = (step, 0) if resize else (0, step)
        elif key == Qt.Key.Key_Up:
            dh, dy = (-step, 0) if resize else (0, -step)
        elif key == Qt.Key.Key_Down:
            dh, dy = (step, 0) if resize else (0, step)
        else:
            super().keyPressEvent(event)
            return
        w, h = self._image_size
        new = Rect(x=max(0, min(sel.x + dx, w - 1)), y=max(0, min(sel.y + dy, h - 1)),
                   width=max(1, min(sel.width + dw, w - sel.x)),
                   height=max(1, min(sel.height + dh, h - sel.y)))
        self.set_selection(new)
