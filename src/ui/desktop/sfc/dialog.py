"""Dialog."""
from shared.i18n import tr
from ui.desktop.qt import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QDialog
from ui.desktop.sfc.editor import SFCEditorWidget, sfc_to_text, show_sfc_message
from ui.desktop.theme import get_theme_manager, normalize_theme, theme_tokens
from ui.desktop.chrome import DialogTitleBar, prepare_frameless_dialog, window_chrome_qss
from ui.desktop.icons import set_codicon

class SFCWorkspaceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.generated_text = ""
        self.setWindowTitle(tr('顺序功能图编辑器'))
        prepare_frameless_dialog(self)
        self.setMinimumSize(760, 520)
        self.resize(1120, 760)
        self.setAutoFillBackground(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title_bar = DialogTitleBar(
            self,
            tr('顺序功能图编辑器'),
            icon_name="circuit-board",
        )
        layout.addWidget(self.title_bar)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 10, 10, 10)
        self.editor = SFCEditorWidget()
        self.editor.text_generated.connect(self._accept_text)
        content_layout.addWidget(self.editor, 1)
        actions = QHBoxLayout()
        hint = QLabel(tr('完成流程图后，将其转换为结构化需求并插入当前对话。'))
        hint.setObjectName("SectionCaption")
        cancel = QPushButton(tr('取消'))
        insert = QPushButton(tr('插入到需求'))
        insert.setObjectName("PrimaryButton")
        set_codicon(cancel, "close", tr('取消'), 10)
        set_codicon(insert, "send", tr('插入到需求'), 10)
        cancel.clicked.connect(self.reject)
        insert.clicked.connect(self._convert)
        actions.addWidget(hint)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(insert)
        content_layout.addLayout(actions)
        layout.addWidget(content, 1)
        self.apply_theme(get_theme_manager().current_theme)

    def apply_theme(self, mode):
        selected = normalize_theme(mode)
        colors = theme_tokens(selected)
        self.setStyleSheet("""
            QDialog { background: %(surface)s; color: %(text)s; }
            QDialog > QWidget { background: %(surface)s; color: %(text)s; }
            QLabel { color: %(text)s; background: transparent; }
            QPushButton { color: %(text)s; background: %(surface_alt)s; border: 1px solid %(border)s; border-radius: 2px; min-height: 32px; padding: 0 12px; }
            QPushButton:hover { color: %(text_strong)s; background: %(hover)s; }
            QPushButton#PrimaryButton { color: #ffffff; background: %(accent_button)s; border-color: %(accent)s; }
        """ % colors + window_chrome_qss(selected))
        self.editor.apply_theme(selected)

    def _convert(self):
        text = sfc_to_text(self.editor.scene, self.editor.io_config)
        if not text.strip():
            show_sfc_message(
                self, tr('流程图为空'), tr('请先添加步骤和转移条件。'), "warning"
            )
            return
        self._accept_text(text)

    def _accept_text(self, text):
        self.generated_text = text
        self.accept()

