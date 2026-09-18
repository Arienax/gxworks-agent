from enum import Enum

from ui.desktop.qt import QObject, QSettings, pyqtSignal
from ui.desktop.qt import QColor, QPalette
from ui.desktop.qt import QApplication


class ThemeMode(str, Enum):
    DARK = "dark"
    LIGHT = "light"


THEME_TOKENS = {
    ThemeMode.DARK: {
        "shell": "#181818",
        "surface": "#1f1f1f",
        "surface_alt": "#252526",
        "input": "#313131",
        "text": "#cccccc",
        "text_strong": "#f0f0f0",
        "text_muted": "#9d9d9d",
        "border": "#3c3c3c",
        "hover": "#2a2d2e",
        "selection": "#04395e",
        "accent": "#0078d4",
        "accent_button": "#0e639c",
        "canvas": "#181818",
        "canvas_stroke": "#cccccc",
        "canvas_note": "#9cdcfe",
    },
    ThemeMode.LIGHT: {
        "shell": "#f5f5f5",
        "surface": "#ffffff",
        "surface_alt": "#f3f3f3",
        "input": "#ffffff",
        "text": "#1e1e1e",
        "text_strong": "#1e1e1e",
        "text_muted": "#616161",
        "border": "#cccedb",
        "hover": "#e5f1fb",
        "selection": "#cde8ff",
        "accent": "#0078d4",
        "accent_button": "#0078d4",
        "canvas": "#ffffff",
        "canvas_stroke": "#1e1e1e",
        "canvas_note": "#0066b8",
    },
}


def normalize_theme(mode):
    if isinstance(mode, ThemeMode):
        return mode
    try:
        return ThemeMode(str(mode).lower())
    except ValueError:
        return ThemeMode.DARK


def theme_tokens(mode=None):
    selected = normalize_theme(mode or get_theme_manager().current_theme)
    return THEME_TOKENS[selected]


class ThemeManager(QObject):
    theme_changed = pyqtSignal(object)
    SETTINGS_KEY = "ui/theme"

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings or QSettings(
            "PLC AI Studio", "PLC AI Workbench"
        )
        self.current_theme = normalize_theme(
            self.settings.value(self.SETTINGS_KEY, ThemeMode.LIGHT.value)
        )

    @property
    def is_dark(self):
        return self.current_theme == ThemeMode.DARK

    def set_theme(self, mode, persist=True):
        selected = normalize_theme(mode)
        changed = selected != self.current_theme
        self.current_theme = selected
        if persist:
            self.settings.setValue(self.SETTINGS_KEY, selected.value)
            self.settings.sync()
        self.apply_application_palette()
        if changed:
            self.theme_changed.emit(selected)
        return selected

    def toggle_theme(self):
        target = ThemeMode.LIGHT if self.is_dark else ThemeMode.DARK
        return self.set_theme(target)

    def apply_application_palette(self):
        app = QApplication.instance()
        if app is None:
            return
        colors = theme_tokens(self.current_theme)
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["shell"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["input"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["surface_alt"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(colors["surface_alt"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["selection"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["text_strong"]))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["surface_alt"]))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text"]))
        app.setPalette(palette)
        app.setProperty("uiTheme", self.current_theme.value)


_theme_manager = None


def get_theme_manager():
    global _theme_manager
    if _theme_manager is None:
        _theme_manager = ThemeManager()
    return _theme_manager
