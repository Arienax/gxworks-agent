from shared.i18n import tr
from ui.desktop.qt import QEvent, QObject, Qt
from ui.desktop.qt import QFont
from ui.desktop.qt import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from ui.desktop.icons import codicon, codicon_font, set_codicon
from ui.desktop.theme import ThemeMode, normalize_theme, theme_tokens


def window_chrome_qss(mode=ThemeMode.DARK):
    colors = theme_tokens(normalize_theme(mode))
    return """
QFrame#DialogTitleBar {
    background: %(shell)s;
    border: none;
    border-bottom: 1px solid %(border)s;
}
QLabel#DialogAppIcon {
    color: %(accent)s;
    background: transparent;
}
QLabel#DialogWindowTitle {
    color: %(text_strong)s;
    background: transparent;
}
QPushButton#DialogWindowMinButton,
QPushButton#DialogWindowMaxButton,
QPushButton#DialogWindowCloseButton {
    min-width: 46px;
    max-width: 46px;
    min-height: 35px;
    max-height: 35px;
    padding: 0;
    color: %(text)s;
    background: transparent;
    border: none;
    border-radius: 0;
}
QPushButton#DialogWindowMinButton:hover,
QPushButton#DialogWindowMaxButton:hover {
    color: %(text_strong)s;
    background: %(hover)s;
}
QPushButton#DialogWindowCloseButton:hover {
    color: #ffffff;
    background: #c42b1c;
}
""" % colors


WINDOW_CHROME_QSS = window_chrome_qss(ThemeMode.DARK)


class DialogTitleBar(QFrame):
    def __init__(
        self,
        window,
        title,
        icon_name="circuit-board",
        allow_minimize=True,
        allow_maximize=True,
    ):
        super().__init__(window)
        self._window = window
        self._drag_offset = None
        self._allow_maximize = allow_maximize
        self.setObjectName("DialogTitleBar")
        self.setFixedHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(5)

        icon = QLabel(codicon(icon_name))
        icon.setObjectName("DialogAppIcon")
        icon.setFont(codicon_font(15))
        icon.setFixedWidth(24)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("DialogWindowTitle")
        title_font = QFont("Segoe UI", 9)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet(
            'font-family: "Segoe UI", "Microsoft YaHei"; '
            "font-size: 9pt; font-weight: 600;"
        )
        self.title_label.setMinimumWidth(max(100, len(title) * 14))
        layout.addWidget(self.title_label)
        layout.addStretch()

        self.minimize_button = None
        if allow_minimize:
            self.minimize_button = self._window_button(
                "chrome-minimize",
                "DialogWindowMinButton",
                tr('最小化'),
            )
            self.minimize_button.clicked.connect(window.showMinimized)
            layout.addWidget(self.minimize_button)

        self.maximize_button = None
        if allow_maximize:
            self.maximize_button = self._window_button(
                "chrome-maximize",
                "DialogWindowMaxButton",
                tr('最大化'),
            )
            self.maximize_button.clicked.connect(self._toggle_maximized)
            layout.addWidget(self.maximize_button)

        self.close_button = self._window_button(
            "chrome-close",
            "DialogWindowCloseButton",
            tr('关闭'),
        )
        self.close_button.clicked.connect(window.close)
        layout.addWidget(self.close_button)
        window.installEventFilter(self)

    @staticmethod
    def _window_button(icon_name, object_name, tooltip):
        button = QPushButton()
        button.setObjectName(object_name)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        set_codicon(button, icon_name, point_size=10)
        return button

    def _toggle_maximized(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self._update_maximize_button()

    def _update_maximize_button(self):
        if self.maximize_button is None:
            return
        maximized = self._window.isMaximized()
        set_codicon(
            self.maximize_button,
            "chrome-restore" if maximized else "chrome-maximize",
            point_size=10,
        )
        tooltip = tr('还原') if maximized else tr('最大化')
        self.maximize_button.setToolTip(tooltip)
        self.maximize_button.setAccessibleName(tooltip)

    def eventFilter(self, watched, event):
        if (
            watched is self._window
            and event.type() == QEvent.Type.WindowStateChange
        ):
            self._update_maximize_button()
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self._window.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._window.isMaximized()
        ):
            self._window.move(
                event.globalPosition().toPoint() - self._drag_offset
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if (
            self._allow_maximize
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _DialogResizeHandle(QWidget):
    def __init__(self, edges, cursor, parent):
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
        event.accept()

    def mouseReleaseEvent(self, event):
        self._start_position = None
        self._start_geometry = None
        super().mouseReleaseEvent(event)


class _DialogResizeFrame(QObject):
    MARGIN = 5

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        left = Qt.Edge.LeftEdge
        right = Qt.Edge.RightEdge
        top = Qt.Edge.TopEdge
        bottom = Qt.Edge.BottomEdge
        self.handles = {
            "left": _DialogResizeHandle(
                left, Qt.CursorShape.SizeHorCursor, dialog
            ),
            "right": _DialogResizeHandle(
                right, Qt.CursorShape.SizeHorCursor, dialog
            ),
            "top": _DialogResizeHandle(
                top, Qt.CursorShape.SizeVerCursor, dialog
            ),
            "bottom": _DialogResizeHandle(
                bottom, Qt.CursorShape.SizeVerCursor, dialog
            ),
            "top_left": _DialogResizeHandle(
                top | left, Qt.CursorShape.SizeFDiagCursor, dialog
            ),
            "top_right": _DialogResizeHandle(
                top | right, Qt.CursorShape.SizeBDiagCursor, dialog
            ),
            "bottom_left": _DialogResizeHandle(
                bottom | left, Qt.CursorShape.SizeBDiagCursor, dialog
            ),
            "bottom_right": _DialogResizeHandle(
                bottom | right, Qt.CursorShape.SizeFDiagCursor, dialog
            ),
        }
        dialog.installEventFilter(self)
        self.position_handles()

    def eventFilter(self, watched, event):
        if watched is self.dialog and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.WindowStateChange,
        }:
            self.position_handles()
        return False

    def position_handles(self):
        margin = self.MARGIN
        width = self.dialog.width()
        height = self.dialog.height()
        edge_width = max(0, width - margin * 2)
        edge_height = max(0, height - margin * 2)
        geometry = {
            "left": (0, margin, margin, edge_height),
            "right": (width - margin, margin, margin, edge_height),
            "top": (margin, 0, edge_width, margin),
            "bottom": (margin, height - margin, edge_width, margin),
            "top_left": (0, 0, margin, margin),
            "top_right": (width - margin, 0, margin, margin),
            "bottom_left": (0, height - margin, margin, margin),
            "bottom_right": (
                width - margin,
                height - margin,
                margin,
                margin,
            ),
        }
        visible = not self.dialog.isMaximized()
        for name, handle in self.handles.items():
            handle.setGeometry(*geometry[name])
            handle.setVisible(visible)
            handle.raise_()


def prepare_frameless_dialog(dialog):
    dialog.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    dialog.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
    dialog.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
    dialog.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
    dialog._dialog_resize_frame = _DialogResizeFrame(dialog)
