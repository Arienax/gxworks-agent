"""Application settings: model profiles and presentation language."""
from shared.i18n import tr
import copy
import json
from ui.desktop.qt import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QComboBox, QPlainTextEdit, QPushButton,
                             QMessageBox, QInputDialog, QWidget, QTabWidget,
                             QSlider)
from ui.desktop.qt import QFont, QPalette, QColor
from ui.desktop.qt import Qt, QThread, pyqtSignal
from storage.config import (
    BUILTIN_MODEL_PROFILE_IDS,
    DEFAULT_MODEL_PROFILES,
    get_api_key,
    get_model_profile,
    load_full_config,
    save_config,
)
from storage.credentials import (
    credential_target_for_profile,
    delete_api_key,
    write_api_key,
)
from model_runtime.provider import test_model_profile
from shared.display_names import naturalize_display_text
from ui.desktop.controls import BorderedComboBox
from ui.desktop.icons import set_codicon
from ui.desktop.chrome import (
    DialogTitleBar,
    prepare_frameless_dialog,
    window_chrome_qss,
)
from ui.desktop.theme import ThemeMode, get_theme_manager, normalize_theme, theme_tokens


DIALOG_LIGHT_QSS = """
QDialog {
    background-color: #ffffff;
}
QLabel {
    font-family: "Segoe UI", "Microsoft YaHei";
    color: #151c4b;
    font-size: 13px;
    font-weight: 500;
}
QLineEdit {
    background-color: #ffffff;
    border: 1.5px solid #5a7a9a;
    border-radius: 6px;
    padding: 8px 10px;
    color: #151c4b;
    font-family: "Consolas", "Courier New", "Microsoft YaHei";
    font-size: 13px;
}
QLineEdit:focus {
    border: 1.5px solid #69bfef;
}
QComboBox {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    font-size: 13px;
    font-family: "Microsoft YaHei";
    padding: 0 40px 0 11px;
    min-height: 32px;
}
QComboBox:hover {
    border-color: #94a3b8;
}
QComboBox:focus,
QComboBox[popupOpen="true"] {
    border: 2px solid #2563eb;
    padding-left: 10px;
}
QComboBox:disabled {
    color: #94a3b8;
    background-color: #f1f5f9;
    border-color: #e2e8f0;
}
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 32px;
    margin: 0;
    background-color: transparent;
    border: none;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    padding: 4px;
    color: #1f2937;
    selection-background-color: #dbeafe;
    selection-color: #1d4ed8;
    outline: none;
}
QComboBox QAbstractItemView::item {
    min-height: 30px;
    padding: 3px 9px;
    color: #1f2937;
    background-color: #ffffff;
}
QComboBox QAbstractItemView::item:selected {
    color: #1d4ed8;
    background-color: #dbeafe;
}
QSlider::groove:horizontal {
    height: 4px;
    border-radius: 2px;
    background: #dbe4ee;
}
QSlider::sub-page:horizontal {
    border-radius: 2px;
    background: #0078d4;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border: 2px solid #0078d4;
    border-radius: 8px;
    background: #ffffff;
}
QSlider::handle:horizontal:hover { background: #dbeafe; }
QPlainTextEdit {
    background-color: #ffffff;
    border: 1.5px solid #5a7a9a;
    border-radius: 6px;
    padding: 10px;
    color: #151c4b;
    font-family: "Consolas", "Courier New";
    font-size: 13px;
}
QPlainTextEdit:focus {
    border: 1.5px solid #69bfef;
}
QPushButton {
    background-color: #151c4b;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    font-family: "Microsoft YaHei";
    font-size: 14px;
    font-weight: bold;
    padding: 0 20px;
}
QPushButton:hover {
    background-color: #5a7a9a;
}
QPushButton:pressed {
    background-color: #69bfef;
}
#CancelBtn {
    background-color: #ffffff;
    color: #5a7a9a;
    border: 1px solid #5a7a9a;
}
#CancelBtn:hover {
    background-color: #eff0f0;
    color: #151c4b;
}
#HintLabel {
    color: #5a7a9a;
    font-size: 12px;
    font-weight: normal;
}
#TitleLabel {
    color: #69bfef;
    font-size: 16px;
    font-weight: bold;
}
QTabWidget::pane { border: 1px solid #cccedb; background: #ffffff; }
QTabBar::tab { min-width: 110px; min-height: 32px; padding: 0 14px; }
QTabBar::tab:selected { color: #0078d4; background: #ffffff; }
QPushButton#IconButton { padding: 0; font-weight: normal; }
QLabel#StatusLabel { color: #616161; font-size: 12px; font-weight: normal; }
"""

DIALOG_DARK_QSS = """
QDialog { background-color: #1f1f1f; color: #cccccc; }
QLabel { color: #cccccc; }
QLabel#TitleLabel { color: #f0f0f0; font-size: 17px; font-weight: 600; }
QLabel#HintLabel { color: #9d9d9d; }
QLineEdit, QPlainTextEdit, QComboBox {
    color: #cccccc;
    background-color: #313131;
    border: 1px solid #3c3c3c;
    border-radius: 2px;
    selection-color: #ffffff;
    selection-background-color: #264f78;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QComboBox[popupOpen="true"] { border: 1px solid #0078d4; }
QComboBox QAbstractItemView {
    color: #cccccc;
    background: #252526;
    border: 1px solid #454545;
    selection-color: #ffffff;
    selection-background-color: #04395e;
}
QComboBox QAbstractItemView::item { color: #cccccc; background: #252526; }
QComboBox QAbstractItemView::item:selected { color: #ffffff; background: #04395e; }
QSlider::groove:horizontal { height: 4px; border-radius: 2px; background: #4a4a4a; }
QSlider::sub-page:horizontal { border-radius: 2px; background: #0e639c; }
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border: 2px solid #75beff;
    border-radius: 8px;
    background: #252526;
}
QSlider::handle:horizontal:hover { background: #04395e; }
QPushButton {
    color: #ffffff;
    background: #0e639c;
    border: 1px solid #0e639c;
    border-radius: 2px;
}
QPushButton:hover { background: #1177bb; }
QPushButton#CancelBtn {
    color: #cccccc;
    background: #313131;
    border-color: #3c3c3c;
}
QPushButton#CancelBtn:hover { background: #3c3c3c; }
QTabWidget::pane { border: 1px solid #3c3c3c; background: #1f1f1f; }
QTabBar::tab { color: #cccccc; background: #252526; min-width: 110px; min-height: 32px; padding: 0 14px; }
QTabBar::tab:selected { color: #ffffff; background: #1f1f1f; border-bottom: 2px solid #0078d4; }
QPushButton#IconButton { padding: 0; font-weight: normal; }
QLabel#StatusLabel { color: #9d9d9d; font-size: 12px; font-weight: normal; }
"""

DIALOG_GEOMETRY_QSS = """
QLabel { font-family: "Segoe UI", "Microsoft YaHei"; font-size: 13px; font-weight: 500; }
QLabel#TitleLabel { font-size: 17px; font-weight: 600; }
QLabel#HintLabel { font-size: 12px; font-weight: normal; }
QLineEdit, QPlainTextEdit, QComboBox {
    border-width: 1px;
    border-radius: 2px;
}
QLineEdit { padding: 8px 10px; font-size: 13px; }
QPlainTextEdit { padding: 10px; font-size: 13px; }
QComboBox { min-height: 32px; padding: 0 40px 0 11px; font-size: 13px; }
QPushButton { border-width: 1px; border-radius: 2px; padding: 0 20px; font-size: 14px; font-weight: 700; }
"""

DIALOG_LIGHT_QSS += DIALOG_GEOMETRY_QSS
DIALOG_DARK_QSS += DIALOG_GEOMETRY_QSS


_TUNING_KEYS = frozenset({"temperature", "top_p", "reasoning_effort"})
_REASONING_EFFORT_VALUES = (None, "low", "medium", "high", "max")
_REASONING_EFFORT_LABELS = (tr('服务默认'), tr('低'), tr('中'), tr('高'), tr('最高'))
_PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "zhipu": tr('智谱'),
    "custom": tr('自定义'),
}


class ApiConnectionTestThread(QThread):
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, api_key, profile, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.profile = copy.deepcopy(dict(profile))

    def run(self):
        try:
            self.succeeded.emit(test_model_profile(self.profile, self.api_key))
        except Exception as error:
            message = str(error).replace(self.api_key, "***")
            self.failed.emit(message)


from shared.i18n import LANGUAGES, normalize_language, set_language


class RequestTemplateConfigDialog(QDialog):
    """API 请求格式配置对话框"""

    def __init__(self, parent=None, initial_setup=False):
        super().__init__(parent)
        self.initial_setup = bool(initial_setup)
        self.api_key_configured = False
        self._test_thread = None
        self._config = {}
        self._profile_drafts = {}
        self._profile_advanced_text = {}
        self._profile_tuning_values = {}
        self._profile_keys = {}
        self._credential_dirty = set()
        self._credential_delete = set()
        self._current_profile_id = ""
        self._loading_profile = False
        window_title = tr('首次使用设置') if self.initial_setup else tr('设置')
        self.setWindowTitle(window_title)
        prepare_frameless_dialog(self)
        self.resize(790, 700)
        self.setModal(True)

        self._saved_template_text = ""
        self._theme = get_theme_manager().current_theme

        self._init_ui()
        self.apply_theme(self._theme)
        self._load_state()

    # ------------------------------------------------
    # UI 构建
    # ------------------------------------------------
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title_bar = DialogTitleBar(
            self,
            tr('首次使用设置') if self.initial_setup else tr('设置'),
            icon_name="settings-gear",
        )
        layout.addWidget(self.title_bar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(12)
        content_layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel(tr('应用设置'))
        title.setObjectName("TitleLabel")
        content_layout.addWidget(title)

        self.tabs = QTabWidget()
        self.basic_tab = QWidget()
        basic_layout = QVBoxLayout(self.basic_tab)
        basic_layout.setSpacing(10)
        basic_layout.setContentsMargins(18, 18, 18, 18)

        key_label = QLabel(tr("API Key:"))
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText(tr('请输入 API Key'))
        self.api_key_input.textEdited.connect(self._api_key_edited)
        key_row.addWidget(self.api_key_input, 1)
        self.toggle_key_btn = QPushButton()
        self.toggle_key_btn.setObjectName("IconButton")
        self.toggle_key_btn.setFixedSize(36, 36)
        self.toggle_key_btn.setToolTip(tr('显示 API Key'))
        set_codicon(self.toggle_key_btn, "eye", point_size=12)
        self.toggle_key_btn.clicked.connect(self._toggle_api_key_visibility)
        key_row.addWidget(self.toggle_key_btn)
        self.clear_key_btn = QPushButton()
        self.clear_key_btn.setObjectName("IconButton")
        self.clear_key_btn.setFixedSize(36, 36)
        self.clear_key_btn.setToolTip(tr('清除已保存的 API Key'))
        set_codicon(self.clear_key_btn, "close", point_size=12)
        self.clear_key_btn.clicked.connect(self._clear_api_key)
        key_row.addWidget(self.clear_key_btn)
        basic_layout.addWidget(key_label)
        basic_layout.addLayout(key_row)

        credential_hint = QLabel(tr('API Key 将安全保存到当前用户的 Windows 凭据管理器。'))
        credential_hint.setObjectName("HintLabel")
        basic_layout.addWidget(credential_hint)

        url_label = QLabel(tr("Base URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://api.deepseek.com")
        basic_layout.addWidget(url_label)
        basic_layout.addWidget(self.url_input)

        model_label = QLabel(tr('默认模型:'))
        basic_layout.addWidget(model_label)

        model_selector_row = QHBoxLayout()
        model_selector_row.setSpacing(8)
        provider_column = QVBoxLayout()
        provider_column.setSpacing(4)
        provider_column.addWidget(QLabel(tr('模型提供商')))
        self.provider_combo = BorderedComboBox()
        self.provider_combo.setMinimumWidth(170)
        self.provider_combo.setFixedHeight(36)
        self.provider_combo.setToolTip(tr('选择模型服务提供商'))
        self.provider_combo.currentIndexChanged.connect(
            self._on_provider_changed
        )
        provider_column.addWidget(self.provider_combo)
        model_selector_row.addLayout(provider_column, 2)

        model_column = QVBoxLayout()
        model_column.setSpacing(4)
        model_column.addWidget(QLabel(tr('具体模型')))
        self.preset_combo = BorderedComboBox()
        self.preset_combo.setMinimumWidth(270)
        self.preset_combo.setFixedHeight(36)
        self.preset_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.preset_combo.setToolTip(tr('选择当前提供商下的具体模型'))
        self.preset_combo.currentIndexChanged.connect(
            self._on_profile_changed
        )
        self.preset_combo.editTextChanged.connect(self._on_model_text_edited)
        model_column.addWidget(self.preset_combo)
        model_selector_row.addLayout(model_column, 3)
        basic_layout.addLayout(model_selector_row)

        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)
        self.add_profile_btn = QPushButton(tr('添加模型'))
        self.add_profile_btn.setToolTip(tr('添加 OpenAI-compatible 自定义模型'))
        self.add_profile_btn.setFixedHeight(34)
        self.add_profile_btn.setMinimumWidth(104)
        set_codicon(self.add_profile_btn, "add", tr('添加模型'), 10)
        self.add_profile_btn.clicked.connect(self._add_custom_profile)
        self.delete_profile_btn = QPushButton(tr('删除'))
        self.delete_profile_btn.setObjectName("CancelBtn")
        self.delete_profile_btn.setToolTip(tr('删除当前自定义模型配置'))
        self.delete_profile_btn.setFixedHeight(34)
        self.delete_profile_btn.setMinimumWidth(82)
        set_codicon(self.delete_profile_btn, "trash", tr('删除'), 10)
        self.delete_profile_btn.clicked.connect(self._delete_current_profile)
        profile_row.addWidget(self.add_profile_btn)
        profile_row.addWidget(self.delete_profile_btn)
        profile_row.addStretch()
        basic_layout.addLayout(profile_row)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton(tr('测试连接'))
        set_codicon(self.test_btn, "plug", tr('测试连接'), 10)
        self.test_btn.setFixedHeight(36)
        self.test_btn.setMinimumWidth(118)
        self.test_btn.clicked.connect(self._test_connection)
        self.connection_status = QLabel(tr('尚未测试'))
        self.connection_status.setObjectName("StatusLabel")
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.connection_status, 1)
        basic_layout.addLayout(test_row)
        basic_layout.addStretch()

        self.advanced_tab = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_tab)
        advanced_layout.setSpacing(10)
        advanced_layout.setContentsMargins(18, 18, 18, 18)

        self.hint_label = QLabel()
        self.hint_label.setObjectName("HintLabel")
        advanced_layout.addWidget(self.hint_label)

        tuning_title = QLabel(tr('生成参数微调'))
        tuning_title.setObjectName("TitleLabel")
        advanced_layout.addWidget(tuning_title)

        self.temperature_slider, self.temperature_value_label = (
            self._add_tuning_slider(
                advanced_layout,
                "Temperature",
                tr('控制输出随机性；最左侧使用模型服务默认值。'),
                -1,
                40,
            )
        )
        self.top_p_slider, self.top_p_value_label = self._add_tuning_slider(
            advanced_layout,
            "Top P",
            tr('控制候选词采样范围；通常只需调整 Temperature 或 Top P 之一。'),
            -1,
            100,
        )
        (
            self.reasoning_effort_slider,
            self.reasoning_effort_value_label,
        ) = self._add_tuning_slider(
            advanced_layout,
            tr('推理强度'),
            tr('从服务默认、低、中、高到最高共五档。'),
            0,
            len(_REASONING_EFFORT_VALUES) - 1,
        )
        for slider in (
            self.temperature_slider,
            self.top_p_slider,
            self.reasoning_effort_slider,
        ):
            slider.valueChanged.connect(self._on_tuning_controls_changed)

        editor_label = QLabel(tr('兼容性与厂商扩展（JSON，可选）:'))
        advanced_layout.addWidget(editor_label)

        self.template_edit = QPlainTextEdit()
        self.template_edit.setFont(QFont("Consolas", 11))
        self.template_edit.setPlaceholderText(
            tr('编辑 capabilities、其他 generationDefaults 和 requestOverrides...')
        )
        self.template_edit.setMinimumHeight(130)
        advanced_layout.addWidget(self.template_edit, stretch=1)

        self.provider_label = QLabel()
        self.provider_label.setObjectName("HintLabel")
        advanced_layout.addWidget(self.provider_label)

        self.tabs.addTab(self.basic_tab, tr('API 设置'))
        self.tabs.addTab(self.advanced_tab, tr('API 高级设置'))
        self.language_tab = QWidget()
        language_layout = QVBoxLayout(self.language_tab)
        language_layout.setContentsMargins(18, 18, 18, 18)
        language_layout.addWidget(QLabel(tr('界面与输出语言')))
        self.language_combo = BorderedComboBox()
        self.language_combo.setObjectName("LanguageCombo")
        for code, native_name in LANGUAGES:
            self.language_combo.addItem(native_name, code)
        language_layout.addWidget(self.language_combo)
        language_hint = QLabel(tr('保存后立即更新界面。新的 AI 回复、推理摘要和运行提示将使用所选语言。用户原文、已有程序和历史回复保持原样。'))
        language_hint.setWordWrap(True)
        language_layout.addWidget(language_hint)
        language_layout.addStretch()
        self.tabs.addTab(self.language_tab, tr('语言'))
        content_layout.addWidget(self.tabs, 1)

        # ---- 按钮 ----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.save_btn = QPushButton(tr('保存并应用'))
        set_codicon(self.save_btn, "pass", tr('保存并应用'), 10)
        self.save_btn.setFixedHeight(38)
        self.save_btn.setMinimumWidth(128)
        self.save_btn.clicked.connect(self._on_save)

        self.cancel_btn = QPushButton(tr('稍后设置') if self.initial_setup else tr('取消'))
        self.cancel_btn.setObjectName("CancelBtn")
        set_codicon(
            self.cancel_btn,
            "close",
            tr('稍后设置') if self.initial_setup else tr('取消'),
            10,
        )
        self.cancel_btn.setFixedHeight(38)
        self.cancel_btn.setMinimumWidth(110)
        self.cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.cancel_btn)
        content_layout.addLayout(btn_layout)
        layout.addWidget(content, 1)

    def _add_tuning_slider(
        self,
        layout,
        title,
        description,
        minimum,
        maximum,
    ):
        header = QHBoxLayout()
        header.setSpacing(8)
        label = QLabel(title)
        value_label = QLabel(tr('服务默认'))
        value_label.setObjectName("HintLabel")
        value_label.setMinimumWidth(76)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        header.addWidget(label)
        header.addStretch()
        header.addWidget(value_label)
        layout.addLayout(header)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setSingleStep(1)
        slider.setPageStep(1)
        slider.setFixedHeight(22)
        slider.setToolTip(description)
        layout.addWidget(slider)
        hint = QLabel(description)
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return slider, value_label

    def apply_theme(self, mode):
        self._theme = normalize_theme(mode)
        qss = (
            DIALOG_DARK_QSS
            if self._theme == ThemeMode.DARK
            else DIALOG_LIGHT_QSS
        )
        self.setStyleSheet(qss + window_chrome_qss(self._theme))
        if not hasattr(self, "preset_combo"):
            return
        colors = theme_tokens(self._theme)
        for combo in (self.provider_combo, self.preset_combo):
            combo.setProperty("darkTheme", self._theme == ThemeMode.DARK)
            popup_palette = combo.view().palette()
            popup_palette.setColor(
                QPalette.ColorRole.Base, QColor(colors["surface_alt"])
            )
            popup_palette.setColor(
                QPalette.ColorRole.Window, QColor(colors["surface_alt"])
            )
            popup_palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
            popup_palette.setColor(
                QPalette.ColorRole.WindowText, QColor(colors["text"])
            )
            popup_palette.setColor(
                QPalette.ColorRole.Highlight, QColor(colors["selection"])
            )
            popup_palette.setColor(
                QPalette.ColorRole.HighlightedText,
                QColor(colors["text_strong"]),
            )
            combo.view().setPalette(popup_palette)
            combo._refresh_style()

    def _api_key_edited(self):
        profile_id = self._current_profile_id
        if profile_id:
            self._profile_keys[profile_id] = self.api_key_input.text().strip()
            self._credential_dirty.add(profile_id)
            self._credential_delete.discard(profile_id)
        self._mark_connection_dirty()

    def _mark_connection_dirty(self):
        if hasattr(self, "connection_status"):
            self.connection_status.setText(tr('配置已修改，尚未测试'))

    def _toggle_api_key_visibility(self):
        is_hidden = (
            self.api_key_input.echoMode() == QLineEdit.EchoMode.Password
        )
        self.api_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal
            if is_hidden
            else QLineEdit.EchoMode.Password
        )
        icon = "eye-closed" if is_hidden else "eye"
        tooltip = tr('隐藏 API Key') if is_hidden else tr('显示 API Key')
        set_codicon(self.toggle_key_btn, icon, point_size=12)
        self.toggle_key_btn.setToolTip(tooltip)

    def _clear_api_key(self):
        self.api_key_input.clear()
        profile_id = self._current_profile_id
        if profile_id:
            self._profile_keys[profile_id] = ""
            self._credential_dirty.add(profile_id)
            self._credential_delete.add(profile_id)
        self.connection_status.setText(tr('保存后将清除已存储的 API Key'))

    def _test_connection(self):
        try:
            profile = self._current_profile_from_fields()
        except ValueError as error:
            QMessageBox.warning(self, tr('配置不完整'), str(error))
            return
        api_key = self.api_key_input.text().strip()
        if not api_key:
            QMessageBox.warning(self, tr('缺少 API Key'), tr('请先输入 API Key。'))
            return
        if self._test_thread and self._test_thread.isRunning():
            return

        self.test_btn.setEnabled(False)
        self.connection_status.setText(tr('正在测试连接...'))
        self._test_thread = ApiConnectionTestThread(
            api_key,
            profile,
            self,
        )
        self._test_thread.succeeded.connect(self._connection_test_succeeded)
        self._test_thread.failed.connect(self._connection_test_failed)
        self._test_thread.finished.connect(self._connection_test_finished)
        self._test_thread.start()

    def _connection_test_succeeded(self, message):
        self.connection_status.setText(naturalize_display_text(message))

    def _connection_test_failed(self, message):
        self.connection_status.setText(
            tr('连接失败，可保存后实际调用：')
            + naturalize_display_text(str(message)[:180])
        )

    def _connection_test_finished(self):
        self.test_btn.setEnabled(True)

    def reject(self):
        if self._test_thread and self._test_thread.isRunning():
            self.connection_status.setText(tr('请等待连接测试完成后关闭窗口。'))
            return
        super().reject()

    def closeEvent(self, event):
        if self._test_thread and self._test_thread.isRunning():
            self.connection_status.setText(tr('请等待连接测试完成后关闭窗口。'))
            event.ignore()
            return
        super().closeEvent(event)

    # ------------------------------------------------
    # 数据加载 / Profile 切换
    # ------------------------------------------------
    def _load_state(self):
        """从 config.json 加载当前配置到 UI"""
        try:
            config = load_full_config()
        except Exception:
            QMessageBox.warning(self, tr('警告'), tr('无法读取配置文件，将使用默认值。'))
            config = {
                "activeModelProfileId": "deepseek-default",
                "modelProfiles": copy.deepcopy(list(DEFAULT_MODEL_PROFILES)),
            }
        self._config = copy.deepcopy(config)
        self.language_combo.setCurrentIndex(
            self.language_combo.findData(normalize_language(config.get("language")))
        )
        self._profile_drafts = {
            str(item.get("id") or ""): copy.deepcopy(item)
            for item in config.get("modelProfiles", [])
            if isinstance(item, dict) and item.get("id")
        }
        self._profile_tuning_values = {
            profile_id: self._tuning_for_profile(profile)
            for profile_id, profile in self._profile_drafts.items()
        }
        active_id = str(config.get("activeModelProfileId") or "")
        self._rebuild_profile_combo(active_id)
        self.url_input.textEdited.connect(self._mark_connection_dirty)

    @staticmethod
    def _provider_key(profile):
        explicit = str(profile.get("provider") or "").strip().lower()
        if explicit in _PROVIDER_LABELS:
            return explicit
        marker = " ".join(
            str(profile.get(key) or "").lower()
            for key in ("id", "name", "baseUrl", "model")
        )
        if "deepseek" in marker:
            return "deepseek"
        if "zhipu" in marker or "bigmodel.cn" in marker or "glm-" in marker:
            return "zhipu"
        return "custom"

    def _profiles_for_provider(self, provider_key):
        return [
            profile
            for profile in self._profile_drafts.values()
            if self._provider_key(profile) == provider_key
        ]

    def _rebuild_profile_combo(self, selected_id):
        selected_profile = self._profile_drafts.get(str(selected_id or ""))
        if not isinstance(selected_profile, dict) and self._profile_drafts:
            selected_profile = next(iter(self._profile_drafts.values()))
            selected_id = str(selected_profile.get("id") or "")
        selected_provider = (
            self._provider_key(selected_profile) if selected_profile else ""
        )
        present = {
            self._provider_key(profile)
            for profile in self._profile_drafts.values()
        }
        provider_keys = [
            key for key in ("deepseek", "zhipu", "custom") if key in present
        ]

        self._loading_profile = True
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for provider_key in provider_keys:
            self.provider_combo.addItem(
                _PROVIDER_LABELS[provider_key], provider_key
            )
        provider_index = self.provider_combo.findData(selected_provider)
        self.provider_combo.setCurrentIndex(
            provider_index if provider_index >= 0 else 0
        )
        self.provider_combo.blockSignals(False)
        self._loading_profile = False

        provider_key = str(self.provider_combo.currentData() or "")
        self._populate_model_combo(provider_key, str(selected_id or ""))
        self._update_profile_actions()

    def _populate_model_combo(self, provider_key, selected_id=""):
        profiles = self._profiles_for_provider(provider_key)
        self._loading_profile = True
        self.preset_combo.blockSignals(True)
        self.preset_combo.setEditable(False)
        self.preset_combo.clear()
        active_index = 0
        for index, profile in enumerate(profiles):
            profile_id = str(profile.get("id") or "")
            model_name = str(profile.get("model") or "").strip()
            self.preset_combo.addItem(
                model_name or str(profile.get("name") or profile_id),
                profile_id,
            )
            self.preset_combo.setItemData(
                index,
                str(profile.get("name") or model_name or profile_id),
                Qt.ItemDataRole.ToolTipRole,
            )
            if profile_id == selected_id:
                active_index = index
        if profiles:
            self.preset_combo.setCurrentIndex(active_index)
        self.preset_combo.blockSignals(False)
        self._loading_profile = False
        if profiles:
            profile_id = str(self.preset_combo.currentData() or "")
            self._load_profile(profile_id)

    def _configure_model_editor(self, profile):
        profile_id = str(profile.get("id") or "")
        editable = profile_id not in BUILTIN_MODEL_PROFILE_IDS
        self.preset_combo.blockSignals(True)
        self.preset_combo.setEditable(editable)
        if editable:
            self.preset_combo.setEditText(str(profile.get("model") or ""))
            line_edit = self.preset_combo.lineEdit()
            if line_edit is not None:
                line_edit.setPlaceholderText(tr('输入模型名称，例如 company-model'))
        self.preset_combo.blockSignals(False)

    def _on_provider_changed(self, _index):
        if self._loading_profile:
            return
        self._capture_current_profile()
        provider_key = str(self.provider_combo.currentData() or "")
        self._populate_model_combo(provider_key)
        self._mark_connection_dirty()

    def _next_custom_profile_id(self):
        index = 1
        while f"custom-{index}" in self._profile_drafts:
            index += 1
        return f"custom-{index}"

    def _add_custom_profile(self):
        profile_id = self._next_custom_profile_id()
        suggested_name = tr('自定义模型 {v0}', v0=profile_id.rsplit('-', 1)[-1])
        name, accepted = QInputDialog.getText(
            self,
            tr('添加模型'),
            tr('配置名称：'),
            text=suggested_name,
        )
        if not accepted:
            return
        name = str(name or "").strip()
        if not name:
            QMessageBox.warning(self, tr('配置不完整'), tr('配置名称不能为空。'))
            return

        self._capture_current_profile()
        current = self._profile_drafts.get(self._current_profile_id) or {}
        profile = {
            "id": profile_id,
            "name": name,
            "provider": "custom",
            "adapter": "openai_compatible",
            "baseUrl": str(current.get("baseUrl") or "https://api.openai.com/v1"),
            "model": "",
            "capabilities": {
                "reasoning": True,
                "tools": True,
                "structured_output": True,
            },
            "generationDefaults": {
                "response_format": {"type": "json_object"},
            },
            "requestOverrides": {},
            "credentialTarget": credential_target_for_profile(profile_id),
        }
        self._profile_drafts[profile_id] = profile
        self._profile_tuning_values[profile_id] = self._tuning_for_profile(profile)
        self._profile_advanced_text[profile_id] = json.dumps(
            self._advanced_for_profile(profile), indent=2, ensure_ascii=False
        )
        self._profile_keys[profile_id] = ""
        self._credential_delete.discard(profile_id)
        self._rebuild_profile_combo(profile_id)
        self.tabs.setCurrentWidget(self.basic_tab)
        if self.preset_combo.lineEdit() is not None:
            self.preset_combo.lineEdit().setFocus()
        self._mark_connection_dirty()

    def _delete_current_profile(self):
        profile_id = self._current_profile_id
        if not profile_id or profile_id in BUILTIN_MODEL_PROFILE_IDS:
            return
        profile = self._profile_drafts.get(profile_id) or {}
        name = str(profile.get("name") or profile_id)
        answer = QMessageBox.question(
            self,
            tr('删除自定义模型'),
            tr('确定删除“{v0}”吗？\n保存后将同时删除该配置保存的 API Key。', v0=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._profile_drafts.pop(profile_id, None)
        self._profile_advanced_text.pop(profile_id, None)
        self._profile_tuning_values.pop(profile_id, None)
        self._profile_keys.pop(profile_id, None)
        self._credential_dirty.add(profile_id)
        self._credential_delete.add(profile_id)
        self._current_profile_id = ""
        self._rebuild_profile_combo(next(iter(self._profile_drafts), ""))
        self._mark_connection_dirty()

    def _update_profile_actions(self):
        if hasattr(self, "delete_profile_btn"):
            self.delete_profile_btn.setEnabled(
                bool(self._current_profile_id)
                and self._current_profile_id not in BUILTIN_MODEL_PROFILE_IDS
            )

    @staticmethod
    def _advanced_for_profile(profile):
        generation_defaults = copy.deepcopy(
            profile.get("generationDefaults") or {}
        )
        for key in _TUNING_KEYS:
            generation_defaults.pop(key, None)
        return {
            "capabilities": copy.deepcopy(profile.get("capabilities") or {}),
            "generationDefaults": generation_defaults,
            "requestOverrides": copy.deepcopy(profile.get("requestOverrides") or {}),
        }

    @staticmethod
    def _tuning_for_profile(profile):
        defaults = profile.get("generationDefaults") or {}
        temperature = defaults.get("temperature")
        top_p = defaults.get("top_p")
        effort = str(defaults.get("reasoning_effort") or "").strip().lower()
        return {
            "temperature": (
                float(temperature)
                if isinstance(temperature, (int, float))
                and not isinstance(temperature, bool)
                and 0 <= float(temperature) <= 2
                else None
            ),
            "top_p": (
                float(top_p)
                if isinstance(top_p, (int, float))
                and not isinstance(top_p, bool)
                and 0 <= float(top_p) <= 1
                else None
            ),
            "reasoning_effort": (
                effort if effort in _REASONING_EFFORT_VALUES else None
            ),
        }

    @staticmethod
    def _apply_tuning_to_profile(profile, tuning):
        profile = copy.deepcopy(profile)
        defaults = copy.deepcopy(profile.get("generationDefaults") or {})
        for key in _TUNING_KEYS:
            defaults.pop(key, None)
        for key, value in (tuning or {}).items():
            if key in _TUNING_KEYS and value is not None:
                defaults[key] = value
        profile["generationDefaults"] = defaults
        return profile

    def _tuning_from_controls(self):
        temperature_position = self.temperature_slider.value()
        top_p_position = self.top_p_slider.value()
        effort_position = self.reasoning_effort_slider.value()
        return {
            "temperature": (
                None
                if temperature_position < 0
                else round(temperature_position / 20.0, 2)
            ),
            "top_p": (
                None
                if top_p_position < 0
                else round(top_p_position / 100.0, 2)
            ),
            "reasoning_effort": _REASONING_EFFORT_VALUES[effort_position],
        }

    def _update_tuning_labels(self):
        temperature = self.temperature_slider.value()
        top_p = self.top_p_slider.value()
        effort = self.reasoning_effort_slider.value()
        self.temperature_value_label.setText(
            tr('服务默认') if temperature < 0 else f"{temperature / 20.0:.2f}"
        )
        self.top_p_value_label.setText(
            tr('服务默认') if top_p < 0 else f"{top_p / 100.0:.2f}"
        )
        self.reasoning_effort_value_label.setText(
            _REASONING_EFFORT_LABELS[effort]
        )

    def _load_tuning_controls(self, profile_id):
        tuning = self._profile_tuning_values.get(profile_id) or {
            "temperature": None,
            "top_p": None,
            "reasoning_effort": None,
        }
        temperature = tuning.get("temperature")
        top_p = tuning.get("top_p")
        effort = tuning.get("reasoning_effort")
        positions = (
            -1 if temperature is None else round(float(temperature) * 20),
            -1 if top_p is None else round(float(top_p) * 100),
            (
                _REASONING_EFFORT_VALUES.index(effort)
                if effort in _REASONING_EFFORT_VALUES
                else 0
            ),
        )
        for slider, position in zip(
            (
                self.temperature_slider,
                self.top_p_slider,
                self.reasoning_effort_slider,
            ),
            positions,
        ):
            slider.blockSignals(True)
            slider.setValue(position)
            slider.blockSignals(False)
        self._update_tuning_labels()

    def _on_tuning_controls_changed(self, _value):
        self._update_tuning_labels()
        if self._loading_profile or not self._current_profile_id:
            return
        self._profile_tuning_values[
            self._current_profile_id
        ] = self._tuning_from_controls()
        self._mark_connection_dirty()

    def _capture_current_profile(self):
        profile_id = self._current_profile_id
        if not profile_id or profile_id not in self._profile_drafts:
            return
        profile = self._profile_drafts[profile_id]
        profile["baseUrl"] = self.url_input.text().strip()
        self._profile_advanced_text[profile_id] = self.template_edit.toPlainText()
        self._profile_keys[profile_id] = self.api_key_input.text().strip()

    def _load_profile(self, profile_id):
        profile = self._profile_drafts.get(profile_id)
        if not isinstance(profile, dict):
            return
        self._loading_profile = True
        self._current_profile_id = profile_id
        self.url_input.setText(str(profile.get("baseUrl") or ""))
        self._configure_model_editor(profile)
        if profile_id not in self._profile_keys:
            try:
                self._profile_keys[profile_id] = get_api_key(self._config, profile_id)
            except Exception:
                self._profile_keys[profile_id] = ""
        api_key = self._profile_keys[profile_id]
        self.api_key_input.setText(api_key)
        self.api_key_configured = bool(api_key)
        self.clear_key_btn.setEnabled(bool(api_key))
        template_text = self._profile_advanced_text.get(profile_id)
        if template_text is None:
            template_text = json.dumps(
                self._advanced_for_profile(profile), indent=2, ensure_ascii=False
            )
            self._profile_advanced_text[profile_id] = template_text
        self.template_edit.setPlainText(template_text)
        self._saved_template_text = template_text
        self._load_tuning_controls(profile_id)
        self._loading_profile = False
        self._update_hints()

    def _on_profile_changed(self, _index):
        if self._loading_profile:
            return
        self._capture_current_profile()
        profile_id = str(self.preset_combo.currentData() or "")
        if not profile_id:
            return
        self._load_profile(profile_id)
        self._mark_connection_dirty()

    def _on_model_text_edited(self, text):
        if self._loading_profile:
            return
        profile_id = self._current_profile_id
        selected_id = str(self.preset_combo.currentData() or "")
        if selected_id and selected_id != profile_id:
            return
        if not profile_id or profile_id in BUILTIN_MODEL_PROFILE_IDS:
            return
        profile = self._profile_drafts.get(profile_id)
        if isinstance(profile, dict):
            profile["model"] = str(text or "").strip()
            self._mark_connection_dirty()

    def _current_profile_from_fields(self):
        profile_id = self._current_profile_id
        profile = copy.deepcopy(self._profile_drafts.get(profile_id) or {})
        if not profile:
            raise ValueError(tr('请选择模型 Profile。'))
        profile["baseUrl"] = self.url_input.text().strip()
        profile = self._profile_from_advanced_text(
            profile,
            self.template_edit.toPlainText(),
        )
        return self._apply_tuning_to_profile(
            profile,
            self._profile_tuning_values.get(profile_id),
        )

    @staticmethod
    def _profile_from_advanced_text(profile, text):
        profile = copy.deepcopy(profile)
        if not profile["baseUrl"]:
            raise ValueError(tr('{v0} 的 Base URL 不能为空。', v0=profile.get('name') or profile.get('id')))
        if not profile["model"]:
            raise ValueError(tr('{v0} 的模型名不能为空。', v0=profile.get('name') or profile.get('id')))
        try:
            advanced = json.loads(str(text or "").strip() or "{}")
        except json.JSONDecodeError as error:
            raise ValueError(tr('高级配置 JSON 格式错误：{v0}', v0=error)) from error
        if not isinstance(advanced, dict):
            raise ValueError(tr('高级配置必须是 JSON 对象。'))
        allowed = {"capabilities", "generationDefaults", "requestOverrides"}
        unknown = sorted(set(advanced).difference(allowed))
        if unknown:
            raise ValueError(tr('高级配置包含未知字段：') + "、".join(unknown))
        for key in allowed:
            value = advanced.get(key, {})
            if not isinstance(value, dict):
                raise ValueError(tr('{v0} 必须是 JSON 对象。', v0=key))
            profile[key] = copy.deepcopy(value)
        return profile

    def _update_hints(self):
        """显示当前 Profile 的适配器和参数优先级。"""
        profile = self._profile_drafts.get(self._current_profile_id) or {}
        input_mode = (
            tr('支持文字与图片输入')
            if (profile.get("capabilities") or {}).get("multimodal")
            else tr('仅支持文字输入')
        )
        self.hint_label.setText(
            tr('参数优先级：适配器默认值 → Profile 默认值 → 工作流参数 → 能力约束 → 厂商扩展字段')
        )
        self.provider_label.setText(
            tr('提供商：')
            + _PROVIDER_LABELS.get(
                self._provider_key(profile), tr('自定义')
            )
            + tr('；适配器：')
            + str(profile.get("adapter") or "openai_compatible")
            + tr('；{v0}；每个 Profile 使用独立的 Windows 凭据。', v0=input_mode)
        )
        self._update_profile_actions()

    # ------------------------------------------------
    # 保存
    # ------------------------------------------------
    def _on_save(self):
        try:
            selected_profile = self._current_profile_from_fields()
        except ValueError as error:
            QMessageBox.critical(self, tr('配置错误'), str(error))
            return
        api_key = self.api_key_input.text().strip()
        self._profile_drafts[self._current_profile_id] = selected_profile
        self._profile_keys[self._current_profile_id] = api_key
        try:
            normalized_profiles = []
            for profile_id, profile in self._profile_drafts.items():
                text = self._profile_advanced_text.get(profile_id)
                if profile_id == self._current_profile_id:
                    normalized = selected_profile
                elif text is None:
                    normalized = copy.deepcopy(profile)
                else:
                    normalized = self._profile_from_advanced_text(profile, text)
                normalized = self._apply_tuning_to_profile(
                    normalized,
                    self._profile_tuning_values.get(profile_id),
                )
                normalized_profiles.append(normalized)
        except ValueError as error:
            QMessageBox.critical(self, tr('配置错误'), str(error))
            return
        config = copy.deepcopy(self._config)
        config["activeModelProfileId"] = self._current_profile_id
        config["modelProfiles"] = normalized_profiles
        config["language"] = normalize_language(self.language_combo.currentData())

        try:
            save_config(config)
        except Exception as e:
            QMessageBox.critical(
                self, tr('保存失败'),
                tr('无法写入配置文件:\n\n{v0}', v0=str(e))
            )
            return

        try:
            for profile_id in self._credential_dirty:
                profile = self._profile_drafts.get(profile_id) or {}
                target = str(
                    profile.get("credentialTarget")
                    or credential_target_for_profile(profile_id)
                )
                key = self._profile_keys.get(profile_id, "").strip()
                if profile_id in self._credential_delete or not key:
                    delete_api_key(target)
                else:
                    write_api_key(key, target)
        except Exception as e:
            QMessageBox.critical(
                self,
                tr('密钥保存失败'),
                tr('无法写入 Windows 凭据管理器：\n\n') + str(e),
            )
            return

        self._config = config
        self._saved_template_text = self.template_edit.toPlainText()
        self.api_key_configured = bool(api_key)
        set_language(config["language"])
        self.accept()
