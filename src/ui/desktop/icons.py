import sys
from pathlib import Path

from ui.desktop.qt import QSize, Qt
from ui.desktop.qt import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QIconEngine,
    QPainter,
    QPixmap,
)


ICON_CODEPOINTS = {
    "account": 0xEB99,
    "add": 0xEA60,
    "arrow-right": 0xEA9C,
    "checklist": 0xEAB3,
    "chevron-down": 0xEAB4,
    "chevron-left": 0xEAB5,
    "chevron-right": 0xEAB6,
    "chrome-close": 0xEAB8,
    "chrome-maximize": 0xEAB9,
    "chrome-minimize": 0xEABA,
    "chrome-restore": 0xEABB,
    "circuit-board": 0xEABE,
    "close": 0xEA76,
    "cloud-download": 0xEAC2,
    "code": 0xEAC4,
    "color-mode": 0xEAC6,
    "comment-discussion": 0xEAC7,
    "copy": 0xEBCC,
    "clear-all": 0xEABF,
    "edit": 0xEA73,
    "error": 0xEA87,
    "eye": 0xEA70,
    "eye-closed": 0xEAE7,
    "export": 0xEBAC,
    "file-media": 0xEAEA,
    "file-text": 0xEC5E,
    "files": 0xEAF0,
    "folder-opened": 0xEAF7,
    "gear": 0xEAF8,
    "history": 0xEA82,
    "info": 0xEA74,
    "json": 0xEB0F,
    "key": 0xEB11,
    "new-file": 0xEA7F,
    "output": 0xEB9D,
    "pass": 0xEBA4,
    "play": 0xEB2C,
    "plug": 0xEB2D,
    "paintcan": 0xEB2A,
    "preview": 0xEB2F,
    "open-preview": 0xEB2F,
    "project": 0xEB30,
    "robot": 0xEC20,
    "root-folder-opened": 0xEB45,
    "run-all": 0xEB9E,
    "save": 0xEB4B,
    "screen-full": 0xEB4C,
    "screen-normal": 0xEB4D,
    "send": 0xEC0F,
    "settings-gear": 0xEB51,
    "sparkle": 0xEC10,
    "symbol-field": 0xEB5F,
    "sync": 0xEA77,
    "terminal": 0xEA85,
    "tools": 0xEB6D,
    "trash": 0xEA81,
    "versions": 0xEB78,
    "warning": 0xEA6C,
}

_font_family = None


def resource_path(*parts):
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir).joinpath(*parts)

    source_dir = Path(__file__).resolve().parents[2]
    direct = source_dir.joinpath(*parts)
    if direct.exists():
        return direct
    return source_dir.parent.joinpath("resources", *parts)


def load_codicon_font():
    global _font_family
    if _font_family:
        return _font_family

    font_path = resource_path("assets", "codicons", "codicon.ttf")
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id < 0:
        raise RuntimeError(f"无法加载 Codicons 字体: {font_path}")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise RuntimeError("Codicons 字体已注册，但未返回字体族名称")
    _font_family = families[0]
    return _font_family


def codicon(name):
    # Icons are decorative.  Never let a missing mapping raise from Qt's
    # deferred paint callback: exceptions crossing that native callback can
    # terminate the whole process without a Python traceback.
    codepoint = ICON_CODEPOINTS.get(name, ICON_CODEPOINTS["warning"])
    return chr(codepoint)


def codicon_font(point_size=12):
    font = QFont(load_codicon_font())
    font.setPointSize(point_size)
    return font


class _CodiconEngine(QIconEngine):
    def __init__(self, name, color):
        super().__init__()
        self.name = name
        self.color = QColor(color)

    def clone(self):
        return _CodiconEngine(self.name, self.color)

    def paint(self, painter, rect, mode, state):
        if mode == QIcon.Mode.Disabled:
            color = QColor("#656565")
        elif mode in (QIcon.Mode.Active, QIcon.Mode.Selected):
            color = QColor("#ffffff")
        else:
            color = self.color
        font = codicon_font()
        font.setPixelSize(max(12, round(rect.height() * 0.72)))
        painter.save()
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, codicon(self.name))
        painter.restore()

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap


def codicon_icon(name, color="#cccccc"):
    return QIcon(_CodiconEngine(name, color))


def set_codicon(widget, name, text="", point_size=12, color=None):
    if text and hasattr(widget, "setIcon"):
        icon_color = color or (
            "#ffffff"
            if widget.objectName() == "PrimaryButton"
            else "#cccccc"
        )
        widget.setText(text)
        widget.setIcon(codicon_icon(name, icon_color))
        widget.setIconSize(QSize(point_size + 4, point_size + 4))
    else:
        widget.setText(codicon(name))
        widget.setFont(codicon_font(point_size))
    widget.setProperty("codicon", True)
    if text:
        widget.setAccessibleName(text)
    return widget
