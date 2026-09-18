"""Window frame."""
from ui.desktop.qt import QWidget, QFrame
from ui.desktop.qt import Qt

class WorkbenchTitleBar(QFrame):
    """Simple Qt-only draggable title bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self.window().frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self.window().isMaximized()
        ):
            self.window().move(
                event.globalPosition().toPoint() - self._drag_offset
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window()._toggle_window_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class WindowResizeHandle(QWidget):
    def __init__(self, edges, cursor, parent=None):
        super().__init__(parent)
        self.edges = edges
        self._start_position = None
        self._start_geometry = None
        self.setCursor(cursor)
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_position = event.globalPosition().toPoint()
            self._start_geometry = self.window().geometry()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._start_position is None
            or not event.buttons() & Qt.MouseButton.LeftButton
        ):
            return super().mouseMoveEvent(event)
        delta = event.globalPosition().toPoint() - self._start_position
        geometry = self._start_geometry
        left, top = geometry.left(), geometry.top()
        right, bottom = geometry.right(), geometry.bottom()
        if self.edges & Qt.Edge.LeftEdge:
            left += delta.x()
        if self.edges & Qt.Edge.RightEdge:
            right += delta.x()
        if self.edges & Qt.Edge.TopEdge:
            top += delta.y()
        if self.edges & Qt.Edge.BottomEdge:
            bottom += delta.y()
        minimum = self.window().minimumSize()
        if right - left + 1 < minimum.width():
            if self.edges & Qt.Edge.LeftEdge:
                left = right - minimum.width() + 1
            else:
                right = left + minimum.width() - 1
        if bottom - top + 1 < minimum.height():
            if self.edges & Qt.Edge.TopEdge:
                top = bottom - minimum.height() + 1
            else:
                bottom = top + minimum.height() - 1
        self.window().setGeometry(left, top, right - left + 1, bottom - top + 1)

    def mouseReleaseEvent(self, event):
        self._start_position = None
        self._start_geometry = None
        super().mouseReleaseEvent(event)

