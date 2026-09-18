from shared.i18n import get_language, language_context, runtime_text, tr
from ui.desktop.qt import QGridLayout, QSizePolicy
import sys
import os
import json
import copy
import hashlib
import re
import shutil
import uuid
from pathlib import Path
from ui.desktop.qt import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QTextEdit, QPushButton, QLabel,
                             QMessageBox, QScrollArea, QFrame, QComboBox,
                             QStackedWidget, QFileDialog, QMenu, QDialog,
                             QButtonGroup, QRadioButton, QGroupBox, QSplitter,
                             QListWidget, QListWidgetItem, QTabWidget, QLineEdit,
                             QInputDialog, QPlainTextEdit, QAbstractItemView,
                             QProgressBar)
from ui.desktop.qt import QSvgWidget
from ui.desktop.qt import QEvent, QThread, pyqtSignal, Qt, QTimer
from ui.desktop.qt import (
    QAction,
    QColor,
    QFont,
    QPainter,
    QPalette,
    QPixmap,
    QTextCursor,
)
from application.model_api import (
    generate_model_json,
    analyze_requirement,
    _detect_plc_model,
    _build_model_context,
)
from model_runtime.provider import (
    ImageAttachment,
    reload_model_provider,
    reset_model_provider,
    sdk_runtime_self_test,
)
from rendering.ladder_svg import AdvancedSVGLadder, normalize_svg_for_preview
from rendering.numbering import (
    build_rung_display_map,
    display_number_for_anchor,
    rung_index_from_path,
)
from plc.ladder_repair import (
    merge_duplicate_coils,
    normalize_app_instr_out_outputs,
    normalize_legacy_counter_outputs,
    normalize_m8029_parallel_branches,
)
from storage.config import (
    get_active_model_name,
    get_api_key,
    get_model_profile,
    load_full_config,
)
from shared.paths import resource_path
from ui.desktop.sfc.editor import SFCEditorWidget, sfc_to_text, show_sfc_message
from storage.session import (
    MAX_IMAGE_ATTACHMENT_COUNT,
    MAX_IMAGE_ATTACHMENT_BYTES,
    MAX_IMAGE_ATTACHMENTS_TOTAL_BYTES,
    SessionStore,
    detect_image_media_type,
)
from ui.desktop.workbench import (
    DebugContextWidget,
    DebugReportCard,
    InspectionReportCard,
    MessageBubble,
    RequirementReviewCard,
)
from ui.desktop.controls import BorderedComboBox
from ui.desktop.theme import ThemeMode, get_theme_manager, normalize_theme, theme_tokens
from ui.desktop.chrome import (
    DialogTitleBar,
    WINDOW_CHROME_QSS,
    prepare_frameless_dialog,
    window_chrome_qss,
)
from ui.desktop.icons import (
    codicon,
    codicon_font,
    codicon_icon,
    load_codicon_font,
    set_codicon,
)
from plc.specification.confirmed import canonicalize_confirmed_spec
from plc.specification.repair import (
    build_contract_repair_plan,
    format_contract_repair_plan,
    patch_device_addresses,
)
from shared.display_names import (
    naturalize_display_text,
    naturalize_identifier,
    preferred_display_name,
    version_display_name,
)
from plc.validation import (
    ApproachContractValidationError,
    PLCJsonValidationError,
    validate_ladder_full,
    validate_ladder_partial,
    validate_st_json,
)
from plc.ir import (
    IR_SCHEMA_VERSION,
    apply_ladder_partial_to_ir,
    build_plc_ir,
    canonical_sha256,
    ir_to_ladder,
    is_plc_ir,
    validate_plc_ir,
)
from ui.desktop.styles import QSS_TEMPLATE, WORKBENCH_DARK_QSS, WORKBENCH_GEOMETRY_QSS, WORKBENCH_LIGHT_QSS
from application.request_intent import _REGENERATE_LOCKED_SPEC_RE, _is_regenerate_locked_spec_request
from ui.desktop.widgets.activity import ThinkingPanel
from ui.desktop.workers import AnalysisThread, CompilerThread, DebugThread, EvidenceDebugExecuteThread, EvidenceDebugPlanThread, GXWorks2ImportThread, GXWorks2PullThread, GXWorks2SyncInspectThread, InspectionThread, LanguageScopedThread, SimulatorTestExecuteThread, SimulatorTestPlanThread, ToolAgentThread, merge_partial_update
from ui.desktop.dialogs.workflow import ArrowCombo, GXWorks2SyncErrorDialog, RequirementConfirmDialog, SimpleRequirementConfirmDialog, WorkbenchConfirmDialog
from ui.desktop.sfc.dialog import SFCWorkspaceDialog
from ui.desktop.window_frame import WindowResizeHandle, WorkbenchTitleBar
from ui.desktop.classic_window import PLCSystemUI











# ============================
# 阶段1：需求分析线程
# ============================















# ============================
# 阶段2：需求确认对话框
# ============================





































class _IndustrialWorkbenchUI(QMainWindow):
    def __init__(self):
        super().__init__()
        from shared.i18n import set_language, on_language_changed
        try:
            set_language(load_full_config().get("language", "zh-CN"))
        except (OSError, ValueError):
            pass
        on_language_changed(self._language_changed)
        load_codicon_font()
        self.setWindowTitle(tr('PLC AI 编程工作台'))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setMinimumSize(1100, 700)
        self.resize(1360, 860)
        self.theme_manager = get_theme_manager()
        self.theme_manager.apply_application_palette()
        self.theme_manager.theme_changed.connect(self.apply_theme)
        self.setStyleSheet(
            WORKBENCH_DARK_QSS
            if self.theme_manager.is_dark
            else WORKBENCH_LIGHT_QSS
        )

        self.store = SessionStore(legacy_dir=Path(__file__).resolve().parents[2])
        imported = self.store.import_legacy_once()
        self.current_project_id = None
        self.current_version_id = None
        self.active_task = None
        self._composer_image_paths = []
        self._ladder_natural_size = None
        self._analysis_thread = None
        self._compiler_thread = None
        self._tool_agent_thread = None
        self._inspection_thread = None
        self._debug_thread = None
        self._gxworks2_import_thread = None
        self._gxworks2_sync_thread = None
        self._gxworks2_pull_thread = None
        self._pending_gx_sync_result = None
        self._gx_sync_request = None
        self._gx_sync_retry_pending = False
        self._gx_sync_intent = "idle"
        self._pending_gx_pull = None
        self._simulator_test_plan_thread = None
        self._simulator_test_execute_thread = None
        self._active_simulator_test_task_id = None
        self._evidence_debug_plan_thread = None
        self._evidence_debug_execute_thread = None
        # A result signal is emitted from inside QThread.run(), a few
        # instructions before QThread.finished.  Keep every worker alive until
        # the latter signal arrives; clearing the only Python reference in a
        # result slot can otherwise destroy a still-running QThread and abort
        # the whole Qt process (0xc0000409 on Windows).
        self._active_worker_threads = set()
        self._repair_wait_seconds = 0
        self._repair_status_timer = QTimer(self)
        self._repair_status_timer.setInterval(1000)
        self._repair_status_timer.timeout.connect(
            self._update_repair_wait_status
        )

        self._init_ui()
        self._init_resize_handles()
        self.apply_theme(self.theme_manager.current_theme, reload_preview=False)
        projects = self.store.list_projects()
        preferred_id = (
            imported["id"]
            if imported
            else (projects[0]["id"] if projects else None)
        )
        self._refresh_projects(preferred_id, projects=projects)

    def _retain_worker_thread(self, attribute, thread, *, on_finished=None):
        """Own *thread* until Qt proves that ``run()`` has returned."""

        setattr(self, attribute, thread)
        self._active_worker_threads.add(thread)

        def release():
            self._active_worker_threads.discard(thread)
            if getattr(self, attribute, None) is thread:
                setattr(self, attribute, None)
            if on_finished is not None:
                on_finished()

        thread.finished.connect(release)
        thread.finished.connect(thread.deleteLater)
        return thread

    def _init_ui(self):
        root = QWidget()
        root.setObjectName("WorkbenchRoot")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.title_bar = WorkbenchTitleBar(self)
        self.title_bar.setObjectName("TopBar")
        self.title_bar.setFixedHeight(36)
        top_layout = QHBoxLayout(self.title_bar)
        top_layout.setContentsMargins(8, 0, 0, 0)
        top_layout.setSpacing(5)
        self.project_title = QLabel(tr('PLC AI 编程工作台'))
        self.project_title.setObjectName("ProjectTitle")
        brand_icon = QLabel(codicon("circuit-board"))
        brand_icon.setObjectName("AppIcon")
        brand_icon.setFont(codicon_font(15))
        brand_icon.setFixedWidth(24)
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_icon.setToolTip(tr('PLC AI 编程工作台'))
        self.model_combo = BorderedComboBox()
        self.model_combo.addItems(self._load_plc_models())
        self.target_combo = BorderedComboBox()
        self.target_combo.addItem(tr('梯形图 / GX Works2'), "ladder")
        self.target_combo.addItem(tr('ST 结构化文本'), "st")
        self.workflow_combo = BorderedComboBox()
        self.workflow_combo.addItem(tr('生成'), "generate")
        self.workflow_combo.addItem(tr('版本评审'), "review")
        self.workflow_combo.addItem(tr('故障调试'), "debug")
        for combo in (
            self.model_combo,
            self.target_combo,
            self.workflow_combo,
        ):
            self._configure_combo_popup(combo)
        self.sfc_button = QPushButton(tr('流程图输入'))
        self.theme_button = QPushButton()
        self.settings_button = QPushButton(tr('设置'))
        self.sfc_button.setObjectName("ToolbarButton")
        self.theme_button.setObjectName("ThemeButton")
        self.settings_button.setObjectName("ToolbarButton")
        set_codicon(self.sfc_button, "circuit-board", tr('流程图'), 10)
        set_codicon(self.settings_button, "settings-gear", tr('设置'), 10)
        self.theme_button.setFixedSize(32, 28)
        theme_icon_font = QFont("Segoe UI Symbol", 14)
        self.theme_button.setFont(theme_icon_font)
        self.sfc_button.clicked.connect(self._open_sfc_workspace)
        self.theme_button.clicked.connect(self.theme_manager.toggle_theme)
        self.settings_button.clicked.connect(self._open_api_settings)
        self.model_combo.currentIndexChanged.connect(self._settings_changed)
        self.target_combo.currentIndexChanged.connect(self._settings_changed)
        self.workflow_combo.currentIndexChanged.connect(self._workflow_changed)
        top_layout.addWidget(brand_icon)
        top_layout.addWidget(self.project_title)
        top_layout.addStretch()
        self.plc_label = QLabel("PLC")
        self.plc_label.setObjectName("TopBarLabel")
        self.output_label = QLabel(tr('输出'))
        self.output_label.setObjectName("TopBarLabel")
        self.workflow_label = QLabel(tr('工作流'))
        self.workflow_label.setObjectName("TopBarLabel")
        top_layout.addWidget(self.plc_label)
        top_layout.addWidget(self.model_combo)
        top_layout.addWidget(self.output_label)
        top_layout.addWidget(self.target_combo)
        top_layout.addWidget(self.workflow_label)
        top_layout.addWidget(self.workflow_combo)
        top_layout.addWidget(self.sfc_button)
        top_layout.addWidget(self.theme_button)
        top_layout.addWidget(self.settings_button)
        self.minimize_button = self._window_button(
            "chrome-minimize", "WindowMinButton", tr('最小化')
        )
        self.maximize_button = self._window_button(
            "chrome-maximize", "WindowMaxButton", tr('最大化')
        )
        self.close_button = self._window_button(
            "chrome-close", "WindowCloseButton", tr('关闭')
        )
        self.minimize_button.clicked.connect(self.showMinimized)
        self.maximize_button.clicked.connect(self._toggle_window_maximized)
        self.close_button.clicked.connect(self.close)
        top_layout.addWidget(self.minimize_button)
        top_layout.addWidget(self.maximize_button)
        top_layout.addWidget(self.close_button)
        layout.addWidget(self.title_bar)
        self._update_titlebar_density()

        workspace = QFrame()
        workspace.setObjectName("Workspace")
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.sidebar = self._build_sidebar()
        self.workspace_splitter.addWidget(self.sidebar)
        self.workspace_splitter.addWidget(self._build_conversation_pane())
        self.workspace_splitter.addWidget(self._build_artifact_pane())
        self.workspace_splitter.setStretchFactor(0, 0)
        self.workspace_splitter.setStretchFactor(1, 4)
        self.workspace_splitter.setStretchFactor(2, 5)
        self.workspace_splitter.setSizes([220, 500, 620])
        workspace_layout.addWidget(self.workspace_splitter, 1)
        layout.addWidget(workspace, 1)

        self.activity_panel = ThinkingPanel()
        self.activity_panel._collapse()
        layout.addWidget(self.activity_panel)
        self._init_status_bar()

    @staticmethod
    def _window_button(icon_name, object_name, tooltip):
        button = QPushButton()
        button.setObjectName(object_name)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        set_codicon(button, icon_name, point_size=10)
        return button

    def _toggle_window_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._update_maximize_button()

    def _update_maximize_button(self):
        if not hasattr(self, "maximize_button"):
            return
        if self.isMaximized():
            set_codicon(
                self.maximize_button,
                "chrome-restore",
                point_size=10,
            )
            self.maximize_button.setToolTip(tr('还原'))
            self.maximize_button.setAccessibleName(tr('还原'))
        else:
            set_codicon(
                self.maximize_button,
                "chrome-maximize",
                point_size=10,
            )
            self.maximize_button.setToolTip(tr('最大化'))
            self.maximize_button.setAccessibleName(tr('最大化'))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_maximize_button()
            QTimer.singleShot(0, self._position_resize_handles)

    def _init_resize_handles(self):
        left = Qt.Edge.LeftEdge
        right = Qt.Edge.RightEdge
        top = Qt.Edge.TopEdge
        bottom = Qt.Edge.BottomEdge
        self._resize_handles = {
            "left": WindowResizeHandle(
                left, Qt.CursorShape.SizeHorCursor, self
            ),
            "right": WindowResizeHandle(
                right, Qt.CursorShape.SizeHorCursor, self
            ),
            "top": WindowResizeHandle(
                top, Qt.CursorShape.SizeVerCursor, self
            ),
            "bottom": WindowResizeHandle(
                bottom, Qt.CursorShape.SizeVerCursor, self
            ),
            "top_left": WindowResizeHandle(
                top | left, Qt.CursorShape.SizeFDiagCursor, self
            ),
            "top_right": WindowResizeHandle(
                top | right, Qt.CursorShape.SizeBDiagCursor, self
            ),
            "bottom_left": WindowResizeHandle(
                bottom | left, Qt.CursorShape.SizeBDiagCursor, self
            ),
            "bottom_right": WindowResizeHandle(
                bottom | right, Qt.CursorShape.SizeFDiagCursor, self
            ),
        }
        self._position_resize_handles()

    def _position_resize_handles(self):
        if not hasattr(self, "_resize_handles"):
            return
        border = 5
        corner = 10
        width = self.width()
        height = self.height()
        geometries = {
            "left": (0, corner, border, max(0, height - corner * 2)),
            "right": (
                max(0, width - border),
                corner,
                border,
                max(0, height - corner * 2),
            ),
            "top": (corner, 0, max(0, width - corner * 2), border),
            "bottom": (
                corner,
                max(0, height - border),
                max(0, width - corner * 2),
                border,
            ),
            "top_left": (0, 0, corner, corner),
            "top_right": (max(0, width - corner), 0, corner, corner),
            "bottom_left": (0, max(0, height - corner), corner, corner),
            "bottom_right": (
                max(0, width - corner),
                max(0, height - corner),
                corner,
                corner,
            ),
        }
        visible = not self.isMaximized()
        for name, handle in self._resize_handles.items():
            handle.setGeometry(*geometries[name])
            handle.setVisible(visible)
            if visible:
                handle.raise_()

    def _update_titlebar_density(self):
        if not hasattr(self, "project_title"):
            return
        compact = self.width() < 1240
        self.project_title.setVisible(not compact)
        for label_name in (
            "plc_label",
            "output_label",
            "workflow_label",
        ):
            label = getattr(self, label_name, None)
            if label is not None:
                label.setVisible(not compact)

    def _init_status_bar(self):
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.status_project = QLabel()
        self.status_runtime = QLabel()
        self.status_mode = QLabel()
        self.status_project.setText(tr('项目: 未选择'))
        self.status_runtime.setText(tr('状态: 就绪'))
        self.status_mode.setText("PLC: FX3U")
        bar.addWidget(self.status_project)
        bar.addWidget(self.status_runtime)
        bar.addPermanentWidget(self.status_mode)

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(190)
        sidebar.setMaximumWidth(290)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel(tr('项目会话'))
        title.setObjectName("PaneTitle")
        new_button = QPushButton(tr('新建'))
        new_button.setObjectName("PrimaryButton")
        new_button.setToolTip(tr('新建项目'))
        set_codicon(new_button, "new-file", tr('新建'), 9)
        new_button.clicked.connect(self._new_project)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(new_button)
        layout.addLayout(header)

        self.project_list = QListWidget()
        self._configure_light_list(self.project_list)
        self.project_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.project_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.project_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.project_list.customContextMenuRequested.connect(
            self._show_project_context_menu
        )
        self.project_list.currentItemChanged.connect(self._project_selected)
        self.project_list.itemDoubleClicked.connect(self._rename_project)
        layout.addWidget(self.project_list, 3)

        versions_title = QLabel(tr('生成版本'))
        versions_title.setObjectName("PaneTitle")
        versions_hint = QLabel(tr('双击重命名 · 右键管理项目'))
        versions_hint.setObjectName("SectionCaption")
        versions_hint.setWordWrap(True)
        layout.addWidget(versions_title)
        layout.addWidget(versions_hint)
        self.version_list = QListWidget()
        self._configure_light_list(self.version_list)
        self.version_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.version_list.currentItemChanged.connect(self._version_selected)
        layout.addWidget(self.version_list, 2)
        return sidebar

    def _build_conversation_pane(self):
        pane = QFrame()
        pane.setObjectName("ConversationPane")
        pane.setMinimumWidth(430)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.conversation_title = QLabel(tr('需求与修改'))
        self.conversation_title.setObjectName("PaneTitle")
        self.conversation_status = QLabel(tr('等待输入'))
        self.conversation_status.setObjectName("SectionCaption")
        header.addWidget(self.conversation_title)
        header.addStretch()
        header.addWidget(self.conversation_status)
        layout.addLayout(header)

        self.conversation_scroll = QScrollArea()
        self.conversation_scroll.setWidgetResizable(True)
        self.conversation_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.conversation_content = QWidget()
        self.conversation_content.setObjectName("ConversationContent")
        self.conversation_layout = QVBoxLayout(self.conversation_content)
        self.conversation_layout.setContentsMargins(3, 3, 3, 3)
        self.conversation_layout.setSpacing(8)
        self.conversation_layout.addStretch()
        self.conversation_scroll.setWidget(self.conversation_content)
        layout.addWidget(self.conversation_scroll, 1)

        composer = QFrame()
        composer.setObjectName("Composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setSpacing(6)
        self.task_target_badge = QLabel(tr('任务目标：尚未选择版本'))
        self.task_target_badge.setObjectName("SectionCaption")
        self.task_target_badge.setWordWrap(True)
        composer_layout.addWidget(self.task_target_badge)
        self.composer_edit = QTextEdit()
        self.composer_edit.setMinimumHeight(82)
        self.composer_edit.setMaximumHeight(150)
        self.composer_edit.setPlaceholderText(
            tr('描述控制需求，或输入对当前版本的修改要求。Ctrl+Enter 发送。')
        )
        self.image_attachment_scroll = QScrollArea()
        self.image_attachment_scroll.setObjectName("ImageAttachmentStrip")
        self.image_attachment_scroll.setWidgetResizable(False)
        self.image_attachment_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.image_attachment_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.image_attachment_scroll.setFixedHeight(72)
        self.image_attachment_content = QWidget()
        self.image_attachment_content.setObjectName("ImageAttachmentContent")
        self.image_attachment_layout = QHBoxLayout(self.image_attachment_content)
        self.image_attachment_layout.setContentsMargins(4, 4, 4, 4)
        self.image_attachment_layout.setSpacing(6)
        self.image_attachment_scroll.setWidget(self.image_attachment_content)
        self.image_attachment_scroll.setVisible(False)
        self.debug_context_widget = DebugContextWidget()
        self.debug_context_widget.setVisible(False)
        action_row = QHBoxLayout()
        self.image_attachment_button = QPushButton(tr('添加图片'))
        self.image_attachment_button.setObjectName("ToolbarButton")
        self.image_attachment_button.setToolTip(
            tr('添加控制图、接线图、HMI 截图或需求截图')
        )
        set_codicon(
            self.image_attachment_button,
            "file-media",
            tr('添加图片'),
            10,
        )
        self.image_attachment_button.clicked.connect(self._choose_images)
        self.composer_hint = QLabel(tr('修改将基于最新成功版本'))
        self.composer_hint.setObjectName("SectionCaption")
        self.send_button = QPushButton(tr('分析需求'))
        self.send_button.setObjectName("PrimaryButton")
        set_codicon(self.send_button, "sparkle", tr('分析需求'), 10)
        self.send_button.clicked.connect(self._send_requirement)
        action_row.addWidget(self.image_attachment_button)
        action_row.addWidget(self.composer_hint)
        action_row.addStretch()
        action_row.addWidget(self.send_button)
        composer_layout.addWidget(self.composer_edit)
        composer_layout.addWidget(self.image_attachment_scroll)
        composer_layout.addWidget(self.debug_context_widget)
        composer_layout.addLayout(action_row)
        layout.addWidget(composer)

        send_shortcut = QAction(self)
        send_shortcut.setShortcut("Ctrl+Return")
        send_shortcut.triggered.connect(self._send_requirement)
        self.addAction(send_shortcut)
        return pane

    def _active_profile(self):
        try:
            return get_model_profile(load_full_config())
        except Exception:
            return {}

    def _model_supports_images(self):
        return bool((self._active_profile().get("capabilities") or {}).get("multimodal"))

    def _ensure_image_capable_model(self):
        profile = self._active_profile()
        if (profile.get("capabilities") or {}).get("multimodal"):
            return True
        model = str(profile.get("model") or tr('当前模型'))
        QMessageBox.warning(
            self,
            tr('当前模型不支持图片'),
            tr('{v0} 不能接收图片。\n\n请在“API 设置”中切换到 deepseek-v4-flash-vision-exp 或 glm-5.3-flash 后再发送。', v0=model),
        )
        return False

    def _choose_images(self):
        if not self.current_project_id:
            self.statusBar().showMessage(tr('请先选择项目。'), 3000)
            return
        if not self._model_supports_images():
            self._ensure_image_capable_model()
            return
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            tr('添加图片'),
            "",
            tr('图片文件 (*.jpg *.jpeg *.png *.gif *.webp)'),
        )
        if paths:
            self._add_composer_image_paths(paths)

    def _add_composer_image_paths(self, paths):
        existing = []
        markers = set()
        for value in [*self._composer_image_paths, *(paths or [])]:
            try:
                path = Path(value).resolve(strict=True)
            except (OSError, RuntimeError):
                QMessageBox.warning(self, tr('图片不可用'), tr('找不到图片：{v0}', v0=value))
                return False
            marker = str(path).casefold()
            if marker in markers:
                continue
            markers.add(marker)
            existing.append(path)
        if len(existing) > MAX_IMAGE_ATTACHMENT_COUNT:
            QMessageBox.warning(
                self,
                tr('图片过多'),
                tr('一次最多添加 {v0} 张图片。', v0=MAX_IMAGE_ATTACHMENT_COUNT),
            )
            return False

        total_bytes = 0
        for path in existing:
            try:
                size = path.stat().st_size
                if size <= 0:
                    raise ValueError(tr('图片内容为空'))
                if size > MAX_IMAGE_ATTACHMENT_BYTES:
                    raise ValueError(tr('单张图片不能超过 32 MiB'))
                total_bytes += size
                if total_bytes > MAX_IMAGE_ATTACHMENTS_TOTAL_BYTES:
                    raise ValueError(tr('本次图片总大小不能超过 30 MiB'))
                if not detect_image_media_type(path.read_bytes()):
                    raise ValueError(tr('仅支持 JPEG、PNG、GIF、WebP'))
            except (OSError, ValueError) as error:
                QMessageBox.warning(
                    self,
                    tr('图片不可用'),
                    f"{path.name}：{error}",
                )
                return False
        self._composer_image_paths = [str(path) for path in existing]
        self._refresh_image_attachment_strip()
        return True

    def _refresh_image_attachment_strip(self):
        while self.image_attachment_layout.count():
            item = self.image_attachment_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for stored_path in self._composer_image_paths:
            path = Path(stored_path)
            card = QFrame()
            card.setObjectName("ImageAttachmentCard")
            card.setFixedSize(132, 54)
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(4, 4, 3, 4)
            card_layout.setSpacing(5)

            preview = QLabel()
            preview.setObjectName("ImageAttachmentPreview")
            preview.setFixedSize(44, 44)
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                preview.setText(codicon("file-media"))
                preview.setFont(codicon_font(18))
            else:
                preview.setPixmap(
                    pixmap.scaled(
                        44,
                        44,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

            details = QVBoxLayout()
            details.setContentsMargins(0, 0, 0, 0)
            details.setSpacing(1)
            name = QLabel(path.name)
            name.setObjectName("ImageAttachmentName")
            name.setToolTip(path.name)
            name.setMaximumWidth(66)
            remove = QPushButton("×")
            remove.setObjectName("ImageAttachmentRemove")
            remove.setFixedSize(20, 20)
            remove.setToolTip(tr('移除 {v0}', v0=path.name))
            remove.clicked.connect(
                lambda _checked=False, value=stored_path: self._remove_composer_image(value)
            )
            details.addWidget(name)
            details.addWidget(remove, 0, Qt.AlignmentFlag.AlignRight)
            card_layout.addWidget(preview)
            card_layout.addLayout(details)
            self.image_attachment_layout.addWidget(card)
        width = max(1, len(self._composer_image_paths) * 138 + 8)
        self.image_attachment_content.setFixedSize(width, 62)
        self.image_attachment_scroll.setVisible(bool(self._composer_image_paths))

    def _remove_composer_image(self, stored_path):
        marker = str(stored_path).casefold()
        self._composer_image_paths = [
            value
            for value in self._composer_image_paths
            if str(value).casefold() != marker
        ]
        self._refresh_image_attachment_strip()

    def _clear_composer_images(self):
        self._composer_image_paths = []
        if hasattr(self, "image_attachment_layout"):
            self._refresh_image_attachment_strip()

    def _model_images_from_records(self, project_id, records):
        images = []
        for record in records or []:
            images.append(
                ImageAttachment(
                    str(record.get("filename") or tr('图片')),
                    str(record.get("media_type") or ""),
                    self.store.load_image_attachment(project_id, record),
                )
            )
        return tuple(images)

    def _persist_composer_images(self, project_id):
        records = self.store.import_image_attachments(
            project_id,
            self._composer_image_paths,
        )
        return records, self._model_images_from_records(project_id, records)

    def _restore_composer_images(self, project_id, records):
        restored = []
        for record in records or []:
            stored_name = str(record.get("stored_name") or "")
            path = self.store.attachments_dir(project_id) / stored_name
            if path.is_file():
                restored.append(str(path))
        self._composer_image_paths = restored
        self._refresh_image_attachment_strip()

    def _build_artifact_pane(self):
        pane = QFrame()
        pane.setObjectName("ArtifactPane")
        pane.setMinimumWidth(410)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel(tr('生成产物'))
        title.setObjectName("PaneTitle")
        self.artifact_caption = QLabel(tr('尚未生成'))
        self.artifact_caption.setObjectName("SectionCaption")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.artifact_caption)
        layout.addLayout(header)

        self.artifact_tabs = QTabWidget()
        self.preview_stack = QStackedWidget()
        self.empty_preview = QLabel(tr('完成一次生成后，这里会显示梯形图或 ST 程序。'))
        self.empty_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_preview.setWordWrap(True)
        self.ladder_scroll = QScrollArea()
        self.ladder_scroll.setObjectName("LadderPreview")
        self.ladder_scroll.setWidgetResizable(False)
        self.svg_viewer = QSvgWidget()
        self.svg_viewer.setObjectName("LadderCanvas")
        drawing_palette = self.ladder_scroll.palette()
        drawing_palette.setColor(QPalette.ColorRole.Window, QColor("#181818"))
        drawing_palette.setColor(QPalette.ColorRole.Base, QColor("#181818"))
        self.ladder_scroll.setPalette(drawing_palette)
        self.ladder_scroll.setAutoFillBackground(True)
        self.ladder_scroll.viewport().setPalette(drawing_palette)
        self.ladder_scroll.viewport().setAutoFillBackground(True)
        self.svg_viewer.setPalette(drawing_palette)
        self.svg_viewer.setAutoFillBackground(True)
        self.ladder_scroll.setWidget(self.svg_viewer)
        self.st_preview = QPlainTextEdit()
        self.st_preview.setReadOnly(True)
        self.preview_stack.addWidget(self.empty_preview)
        self.preview_stack.addWidget(self.ladder_scroll)
        self.preview_stack.addWidget(self.st_preview)

        self.validation_view = QPlainTextEdit()
        self.validation_view.setReadOnly(True)
        self.io_view = QPlainTextEdit()
        self.io_view.setReadOnly(True)
        self.source_view = QPlainTextEdit()
        self.source_view.setReadOnly(True)
        self.source_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.artifact_tabs.addTab(
            self.preview_stack, codicon_icon("preview"), tr('预览')
        )
        self.artifact_tabs.addTab(
            self.validation_view, codicon_icon("checklist"), tr('校验')
        )
        self.artifact_tabs.addTab(
            self.io_view, codicon_icon("symbol-field"), "I/O"
        )
        self.artifact_tabs.addTab(
            self.source_view, codicon_icon("json"), "JSON"
        )
        layout.addWidget(self.artifact_tabs, 1)

        self.simulation_progress_panel = QFrame()
        self.simulation_progress_panel.setObjectName("SimulationProgressPanel")
        simulation_progress_layout = QVBoxLayout(self.simulation_progress_panel)
        simulation_progress_layout.setContentsMargins(8, 7, 8, 7)
        simulation_progress_layout.setSpacing(5)
        simulation_progress_header = QHBoxLayout()
        simulation_progress_header.setContentsMargins(0, 0, 0, 0)
        self.simulation_progress_title = QLabel(tr('仿真进度'))
        self.simulation_progress_title.setObjectName("SimulationProgressTitle")
        self.simulation_progress_percent = QLabel("0%")
        self.simulation_progress_percent.setObjectName("SectionCaption")
        simulation_progress_header.addWidget(self.simulation_progress_title)
        simulation_progress_header.addStretch()
        simulation_progress_header.addWidget(self.simulation_progress_percent)
        simulation_progress_layout.addLayout(simulation_progress_header)
        self.simulation_progress_bar = QProgressBar()
        self.simulation_progress_bar.setRange(0, 100)
        self.simulation_progress_bar.setValue(0)
        self.simulation_progress_bar.setTextVisible(False)
        simulation_progress_layout.addWidget(self.simulation_progress_bar)
        self.simulation_progress_current = QLabel(tr('等待开始'))
        self.simulation_progress_current.setObjectName("SimulationProgressCurrent")
        self.simulation_progress_current.setWordWrap(True)
        simulation_progress_layout.addWidget(self.simulation_progress_current)
        self.simulation_progress_log = QPlainTextEdit()
        self.simulation_progress_log.setObjectName("SimulationProgressLog")
        self.simulation_progress_log.setReadOnly(True)
        self.simulation_progress_log.setMaximumHeight(92)
        self.simulation_progress_log.document().setMaximumBlockCount(160)
        simulation_progress_layout.addWidget(self.simulation_progress_log)
        self.simulation_progress_panel.setVisible(False)
        layout.addWidget(self.simulation_progress_panel)

        self.export_button = QPushButton(tr('导出当前版本'))
        self.export_button.setObjectName("PrimaryButton")
        set_codicon(self.export_button, "export", tr('导出当前版本'), 10)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export_current_version)
        self.contract_repair_button = QPushButton(tr('修复方案约束'))
        self.contract_repair_button.setObjectName("PrimaryButton")
        self.contract_repair_button.setEnabled(False)
        self.contract_repair_button.setVisible(False)
        self.contract_repair_button.clicked.connect(
            self._repair_current_contract_mismatch
        )
        self.gxworks2_sync_status = QLabel(tr('GX：未检查'))
        self.gxworks2_sync_status.setObjectName("SectionCaption")
        self.gxworks2_sync_status.setToolTip(
            tr('显示当前项目版本与GX Works2中MAIN程序、软元件注释的同步状态')
        )
        self.gxworks2_import_button = QPushButton(tr('写入 GX Works2'))
        self.gxworks2_import_button.setObjectName("PrimaryButton")
        set_codicon(
            self.gxworks2_import_button,
            "export",
            tr('写入 GX Works2'),
            10,
        )
        self.gxworks2_import_button.setEnabled(False)
        self.gxworks2_import_button.setToolTip(
            tr('将当前已验证版本写入GX Works2；写入前仍会自动备份并检查外部修改')
        )
        self.gxworks2_import_button.clicked.connect(
            self._publish_current_version_to_gxworks2
        )
        self.gxworks2_pull_button = QPushButton(tr('读取 GX Works2'))
        set_codicon(
            self.gxworks2_pull_button,
            "sync",
            tr('读取 GX Works2'),
            10,
        )
        self.gxworks2_pull_button.setEnabled(False)
        self.gxworks2_pull_button.setToolTip(
            tr('读取GX Works2当前MAIN和注释；有差异时创建新的本地版本，不覆盖现有版本')
        )
        self.gxworks2_pull_button.clicked.connect(
            self._pull_current_version_from_gxworks2
        )
        self.gxworks2_advanced_button = QPushButton(tr('高级同步'))
        self.gxworks2_advanced_button.setEnabled(False)
        self.gxworks2_advanced_button.setToolTip(
            tr('比较双方与同步基线，仅在首次绑定、冲突或需要决定保留哪一方时使用')
        )
        self.gxworks2_advanced_button.clicked.connect(
            self._sync_current_version_with_gxworks2
        )
        self.gxw_reader_button = QPushButton(tr('解析 GXW'))
        self.gxw_reader_button.setToolTip(
            tr('实验性只读解析GX Works2 Structured Ladder/FBD工程；不会修改原GXW文件')
        )
        self.gxw_reader_button.clicked.connect(self._open_gxw_structured_reader)
        self.simulator_test_button = QPushButton(tr('仿真测试'))
        self.simulator_test_button.setObjectName("PrimaryButton")
        set_codicon(
            self.simulator_test_button,
            "run-all",
            tr('仿真测试'),
            10,
        )
        self.simulator_test_button.setEnabled(False)
        self.simulator_test_button.clicked.connect(
            self._generate_simulator_test_plan
        )
        self.simulator_test_button.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.simulator_test_button.customContextMenuRequested.connect(
            self._show_simulator_test_menu
        )
        self.simulator_test_button.setToolTip(
            tr('复用当前版本已保存的测试方案；右键可强制重新生成')
        )
        actions = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self.gxworks2_sync_status, 0, 0, 1, 2)
        actions.addWidget(self.contract_repair_button, 0, 2)
        for index, button in enumerate((
            self.export_button, self.gxw_reader_button, self.simulator_test_button,
            self.gxworks2_import_button, self.gxworks2_pull_button, self.gxworks2_advanced_button,
        )):
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            actions.addWidget(button, 1 + index // 3, index % 3)
        layout.addLayout(actions)
        return pane

    @staticmethod
    def _load_plc_models():
        try:
            path = resource_path("plc_models.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            models = [key for key, value in data.items() if isinstance(value, dict)]
            return models or ["FX3U"]
        except Exception:
            return ["FX3U"]

    def _configure_light_list(self, widget, mode=None):
        colors = theme_tokens(mode or self.theme_manager.current_theme)
        palette = widget.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["shell"]))
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["shell"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["selection"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["text_strong"]))
        widget.setPalette(palette)

    def _configure_combo_popup(self, combo, mode=None):
        selected = normalize_theme(mode or self.theme_manager.current_theme)
        colors = theme_tokens(selected)
        combo.setProperty("darkTheme", selected == ThemeMode.DARK)
        view = combo.view()
        palette = view.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface_alt"]))
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["surface_alt"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["selection"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["text_strong"]))
        view.setPalette(palette)
        view.setStyleSheet("""
            QAbstractItemView {
                color: %(text)s;
                background-color: %(surface_alt)s;
                border: 1px solid %(border)s;
                outline: none;
                selection-color: %(text_strong)s;
                selection-background-color: %(selection)s;
            }
            QAbstractItemView::item {
                min-height: 30px;
                padding: 3px 9px;
                color: %(text)s;
                background-color: %(surface_alt)s;
            }
            QAbstractItemView::item:selected {
                color: %(text_strong)s;
                background-color: %(selection)s;
            }
        """ % colors)

    def apply_theme(self, mode, reload_preview=True):
        selected = normalize_theme(mode)
        self.setStyleSheet(
            WORKBENCH_DARK_QSS
            if selected == ThemeMode.DARK
            else WORKBENCH_LIGHT_QSS
        )
        self.theme_manager.apply_application_palette()
        if hasattr(self, "theme_button"):
            target = tr('浅色') if selected == ThemeMode.DARK else tr('深色')
            tooltip = tr('切换到{v0}主题', v0=target)
            self.theme_button.setText("☀" if selected == ThemeMode.DARK else "☾")
            self.theme_button.setToolTip(tooltip)
            self.theme_button.setAccessibleName(tooltip)
        for name in ("project_list", "version_list"):
            widget = getattr(self, name, None)
            if widget is not None:
                self._configure_light_list(widget, selected)
        for name in ("model_combo", "target_combo", "workflow_combo"):
            combo = getattr(self, name, None)
            if combo is not None:
                self._configure_combo_popup(combo, selected)
        self._apply_ladder_canvas_theme(selected)
        for widget in self.findChildren(QWidget):
            apply = getattr(widget, "apply_theme", None)
            if callable(apply) and widget is not self:
                apply(selected)
        if reload_preview and self.current_version_id:
            self._load_version(self.current_version_id)

    def _apply_ladder_canvas_theme(self, mode=None):
        if not hasattr(self, "ladder_scroll"):
            return
        colors = theme_tokens(mode or self.theme_manager.current_theme)
        palette = self.ladder_scroll.palette()
        canvas = QColor(colors["canvas"])
        palette.setColor(QPalette.ColorRole.Window, canvas)
        palette.setColor(QPalette.ColorRole.Base, canvas)
        for widget in (
            self.ladder_scroll,
            self.ladder_scroll.viewport(),
            self.svg_viewer,
        ):
            widget.setPalette(palette)
            widget.setAutoFillBackground(True)

    def _refresh_projects(self, selected_id=None, projects=None):
        self.project_list.blockSignals(True)
        self.project_list.clear()
        selected_item = None
        if projects is None:
            projects = self.store.list_projects()
        for project in projects:
            timestamp = project.get("updated_at", "").replace("T", " ")[:16]
            display_project_name = naturalize_display_text(project["name"])
            item = QListWidgetItem(
                codicon_icon("project"),
                f"{display_project_name}\n{timestamp}",
            )
            item.setData(Qt.ItemDataRole.UserRole, project["id"])
            item.setToolTip(display_project_name)
            self.project_list.addItem(item)
            if project["id"] == selected_id:
                selected_item = item
        self.project_list.blockSignals(False)
        if selected_item is None and self.project_list.count():
            selected_item = self.project_list.item(0)
        if selected_item:
            self.project_list.blockSignals(True)
            try:
                self.project_list.setCurrentItem(selected_item)
            finally:
                self.project_list.blockSignals(False)
            self._load_project(selected_item.data(Qt.ItemDataRole.UserRole))
        else:
            self._clear_project_state()

    def _new_project(self):
        name, ok = self._project_name_input(tr('新建项目'), tr('新项目'))
        if not ok:
            return
        project = self.store.create_project(name=name.strip() or tr('新项目'))
        self._refresh_projects(project["id"])

    def _project_name_input(self, title, value):
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(tr('项目名称：'))
        dialog.setTextValue(value)
        dialog.setTextEchoMode(QLineEdit.EchoMode.Normal)
        colors = theme_tokens(self.theme_manager.current_theme)
        dialog.setStyleSheet(f"""
            QInputDialog {{ background: {colors['surface']}; color: {colors['text']}; }}
            QInputDialog QLabel {{ color: {colors['text']}; background: transparent; }}
            QInputDialog QLineEdit {{
                min-height: 30px;
                padding: 0 8px;
                color: {colors['text']};
                background: {colors['input']};
                border: 1px solid {colors['border']};
                border-radius: 2px;
                selection-color: #ffffff;
                selection-background-color: {colors['accent']};
            }}
            QInputDialog QLineEdit:focus {{ border-color: {colors['accent']}; }}
            QInputDialog QPushButton {{
                min-width: 76px;
                min-height: 30px;
                color: {colors['text']};
                background: {colors['surface_alt']};
                border: 1px solid {colors['border']};
                border-radius: 2px;
            }}
            QInputDialog QPushButton:hover {{ color: {colors['text_strong']}; background: {colors['hover']}; }}
        """)
        ok = dialog.exec() == QDialog.DialogCode.Accepted
        return dialog.textValue(), ok

    def _rename_project(self, item):
        project_id = item.data(Qt.ItemDataRole.UserRole)
        project = self.store.get_project(project_id)
        if not project:
            return
        name, ok = self._project_name_input(tr('重命名项目'), project["name"])
        if ok and name.strip():
            self.store.update_project_settings(project_id, name=name.strip())
            self._refresh_projects(project_id)

    def _show_project_context_menu(self, position):
        item = self.project_list.itemAt(position)
        if item is None:
            return
        self.project_list.setCurrentItem(item)

        menu = QMenu(self.project_list)
        menu.setObjectName("ProjectContextMenu")
        rename_action = menu.addAction(
            codicon_icon("edit"), tr('重命名项目')
        )
        menu.addSeparator()
        delete_action = menu.addAction(
            codicon_icon("trash"), tr('删除项目')
        )
        selected = menu.exec(
            self.project_list.viewport().mapToGlobal(position)
        )
        if selected == rename_action:
            self._rename_project(item)
        elif selected == delete_action:
            self._delete_project(item.data(Qt.ItemDataRole.UserRole))

    def _delete_project(self, project_id):
        project = self.store.get_project(project_id)
        if not project:
            return False
        if (
            self.active_task
            and self.active_task.get("project_id") == project_id
        ):
            QMessageBox.warning(
                self,
                tr('无法删除项目'),
                tr('该项目正在分析或生成程序，请等待任务结束后再删除。'),
            )
            return False

        dialog = WorkbenchConfirmDialog(
            tr('删除项目'),
            tr('确定删除项目“{v0}”吗？\n\n该项目的对话、确认规格和全部生成版本都会被永久删除。', v0=naturalize_display_text(project['name'])),
            confirm_text=tr('删除'),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        was_current = self.current_project_id == project_id
        try:
            self.store.delete_project(project_id)
        except Exception as error:
            QMessageBox.critical(
                self,
                tr('删除失败'),
                tr('无法删除项目：\n{v0}', v0=naturalize_display_text(error)),
            )
            return False

        projects = self.store.list_projects()
        if was_current:
            self.current_project_id = None
            self.current_version_id = None
            selected_id = projects[0]["id"] if projects else None
        else:
            selected_id = self.current_project_id
            if (
                selected_id
                and not any(
                    item["id"] == selected_id for item in projects
                )
            ):
                selected_id = None
            if selected_id is None and projects:
                selected_id = projects[0]["id"]
        self._refresh_projects(selected_id)
        self.statusBar().showMessage(
            tr('项目“{v0}”已删除', v0=naturalize_display_text(project['name'])), 4000
        )
        return True

    def _project_selected(self, current, previous):
        if current:
            self._load_project(current.data(Qt.ItemDataRole.UserRole))

    def _load_project(self, project_id):
        project = self.store.get_project(project_id)
        if not project:
            return
        if self.current_project_id != project_id:
            self._clear_composer_images()
        self.current_project_id = project_id
        controls_enabled = self.active_task is None
        self.composer_edit.setEnabled(controls_enabled)
        self.send_button.setEnabled(controls_enabled)
        self.model_combo.setEnabled(controls_enabled)
        self.target_combo.setEnabled(controls_enabled)
        self.workflow_combo.setEnabled(controls_enabled)
        self.sfc_button.setEnabled(controls_enabled)
        self.empty_preview.setText(
            tr('完成一次生成后，这里会显示梯形图或 ST 程序。')
        )
        display_project_name = naturalize_display_text(project["name"])
        self.project_title.setText(f"PLC AI  /  {display_project_name}")
        self.status_project.setText(tr('项目: {v0}', v0=display_project_name))
        self.status_mode.setText(
            f"PLC: {project.get('plc_model', 'FX3U')}"
        )
        self._set_combo_data(self.model_combo, project.get("plc_model", "FX3U"))
        self._set_combo_data(
            self.target_combo, project.get("target_mode", "ladder")
        )
        self._set_combo_data(
            self.workflow_combo, project.get("workflow_mode", "generate")
        )
        self._render_conversation(project)
        self._refresh_versions(project)
        active_version = project.get("active_version_id")
        if active_version:
            self._select_version(active_version)
        else:
            self._clear_artifacts()
        self._update_workflow_ui()

    def _clear_project_state(self):
        self.current_project_id = None
        self.current_version_id = None
        self.project_title.setText(tr('PLC AI 编程工作台'))
        self.status_project.setText(tr('项目: 未选择'))
        self.status_mode.setText("PLC: --")
        self.conversation_status.setText(tr('请新建项目'))
        self.composer_edit.clear()
        self._clear_composer_images()
        self.composer_edit.setEnabled(False)
        self.send_button.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.target_combo.setEnabled(False)
        self.workflow_combo.setEnabled(False)
        self.sfc_button.setEnabled(False)

        while self.conversation_layout.count():
            item = self.conversation_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        empty = QLabel(tr('当前没有项目\n点击左侧“新建”开始'))
        empty.setObjectName("SectionCaption")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        empty.setMinimumHeight(180)
        self.conversation_layout.addWidget(empty)
        self.conversation_layout.addStretch()

        self.version_list.blockSignals(True)
        self.version_list.clear()
        self.version_list.blockSignals(False)
        self.empty_preview.setText(tr('新建项目并完成生成后，此处显示程序产物。'))
        self._clear_artifacts()

    @staticmethod
    def _set_combo_data(combo, value):
        combo.blockSignals(True)
        index = combo.findData(value)
        if index < 0:
            index = combo.findText(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _settings_changed(self, *args):
        if not self.current_project_id:
            return
        self.store.update_project_settings(
            self.current_project_id,
            plc_model=self.model_combo.currentText(),
            target_mode=self.target_combo.currentData(),
            workflow_mode=self.workflow_combo.currentData(),
        )
        self._refresh_projects(self.current_project_id)
        self._update_workflow_ui()

    def _workflow_changed(self, *args):
        self._settings_changed(*args)
        self._update_workflow_ui()

    def _update_workflow_ui(self):
        if not hasattr(self, "send_button"):
            return
        project = (
            self.store.get_project(self.current_project_id)
            if self.current_project_id
            else None
        )
        workflow = (
            self.workflow_combo.currentData()
            if hasattr(self, "workflow_combo")
            else "generate"
        )
        busy = self.active_task is not None
        selected = (
            project and self._version_with_json(project, self.current_version_id)
        )
        if hasattr(self, "debug_context_widget"):
            self.debug_context_widget.setVisible(workflow == "debug")
        if hasattr(self, "image_attachment_button"):
            image_workflow = workflow == "generate"
            self.image_attachment_button.setVisible(image_workflow)
            self.image_attachment_scroll.setVisible(
                image_workflow and bool(self._composer_image_paths)
            )
            if self._model_supports_images():
                self.image_attachment_button.setToolTip(
                    tr('添加控制图、接线图、HMI 截图或需求截图')
                )
            else:
                self.image_attachment_button.setToolTip(
                    tr('当前模型不支持图片；请先在 API 设置中选择视觉模型')
                )

        if workflow == "generate":
            self.conversation_title.setText(tr('需求与修改'))
            self.composer_edit.setPlaceholderText(
                tr('描述控制需求，或输入对最新成功版本的修改要求。Ctrl+Enter 发送。')
            )
            self.composer_hint.setText(tr('修改将基于最新成功版本'))
            self.send_button.setText(tr('分析并确认'))
            set_codicon(self.send_button, "sparkle", tr('分析并确认'), 10)
            self.task_target_badge.setText(tr('生成前规格确认 · 完成后创建新版本'))
            enabled = bool(project) and not busy
            self.model_combo.setEnabled(enabled)
            self.target_combo.setEnabled(enabled)
            self.sfc_button.setEnabled(enabled)
            self.send_button.setEnabled(enabled)
            return

        is_review = workflow == "review"
        self.conversation_title.setText(tr('版本评审') if is_review else tr('故障调试'))
        self.composer_edit.setPlaceholderText(
            tr('可填写重点检查项；留空则执行完整版本评审。Ctrl+Enter 发送。')
            if is_review
            else tr('描述故障现象（必填），并可补充下方现场观测。Ctrl+Enter 发送。')
        )
        self.send_button.setText(
            tr('评审 {v0}', v0=version_display_name(self.current_version_id))
            if is_review
            else tr('分析故障')
        )
        set_codicon(
            self.send_button,
            "checklist" if is_review else "tools",
            self.send_button.text(),
            10,
        )
        self.model_combo.setEnabled(False)
        self.target_combo.setEnabled(False)
        self.sfc_button.setEnabled(False)
        if not selected:
            self.task_target_badge.setText(tr('任务目标：请先选择一个已生成版本'))
            self.composer_hint.setText(tr('评审与调试严格绑定正在查看的版本'))
            self.send_button.setEnabled(False)
            return
        version, _data = selected
        target_mode = version.get("target_mode", "")
        plc_model = version.get("plc_model") or project.get("plc_model", "FX3U")
        self.task_target_badge.setText(
            tr('任务目标：{v0} · {v1} · {v2}', v0=version_display_name(version['id']), v1=plc_model, v2=tr('梯形图') if target_mode == 'ladder' else 'ST')
        )
        if target_mode != "ladder":
            self.composer_hint.setText(tr('首期仅支持梯形图版本评审与故障调试'))
            self.send_button.setEnabled(False)
            return
        self.composer_hint.setText(
            tr('先执行本地规则，再进行 AI 深查；无 API 时仍保留本地报告')
        )
        self.send_button.setEnabled(not busy)

    def _render_conversation(self, project):
        while self.conversation_layout.count():
            item = self.conversation_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        messages = project.get("messages", [])
        if not messages and not project.get("pending_review"):
            empty = QLabel(
                tr('从一条控制需求开始。\n首次生成会确认方案与 I/O，后续修改只确认差异。')
            )
            empty.setObjectName("SectionCaption")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            empty.setMinimumHeight(160)
            self.conversation_layout.addWidget(empty)
        latest_ladder = self._latest_ladder_version(project)
        latest_ladder_id = latest_ladder[0].get("id") if latest_ladder else None
        for message in messages:
            metadata = message.get("metadata", {})
            if message.get("kind") == "inspection_report":
                report_id = metadata.get("report_id")
                report = (
                    self.store.get_report(project["id"], report_id)
                    if report_id
                    else metadata.get("report", {})
                )
                if report:
                    report_base = self._version_with_json(
                        project, report.get("base_version_id")
                    ) if isinstance(report, dict) and report.get("base_version_id") else None
                    card = InspectionReportCard(
                        report,
                        latest_version_id=latest_ladder_id,
                        current_version_id=self.current_version_id,
                        base_ladder=(
                            report_base[1]
                            if report_base and isinstance(report_base[1], dict)
                            else None
                        ),
                    )
                    card.locate_requested.connect(
                        self._locate_inspection_evidence
                    )
                    card.repair_requested.connect(
                        self._start_inspection_repair
                    )
                    card.retry_ai_requested.connect(
                        self._retry_inspection_ai
                    )
                    self.conversation_layout.addWidget(card)
                    continue
            if message.get("kind") == "debug_report":
                report = metadata.get("report", {})
                report_base = self._version_with_json(
                    project, report.get("base_version_id")
                ) if isinstance(report, dict) and report.get("base_version_id") else None
                card = DebugReportCard(
                    report,
                    latest_version_id=latest_ladder_id,
                    current_version_id=self.current_version_id,
                    base_ladder=(
                        report_base[1]
                        if report_base and isinstance(report_base[1], dict)
                        else None
                    ),
                )
                card.locate_requested.connect(
                    self._locate_inspection_evidence
                )
                card.fix_requested.connect(
                    lambda rep, pid=project["id"]: self._start_debug_fix(pid, rep)
                )
                card.copy_fix_requested.connect(self._copy_debug_fix_to_input)
                self.conversation_layout.addWidget(card)
                continue
            bubble = MessageBubble(
                message.get("role", "assistant"),
                message.get("content", ""),
                message.get("kind", "message"),
                metadata,
            )
            self.conversation_layout.addWidget(bubble)
        active_version_id = project.get("active_version_id")
        active_version = self.store.get_version(
            project["id"], active_version_id
        ) if active_version_id else None
        legacy_findings = (
            (active_version or {}).get("validation", {}).get("findings", [])
        )
        if (
            active_version
            and active_version.get("target_mode") == "ladder"
            and not active_version.get("review_report_id")
            and legacy_findings
        ):
            selected_legacy = self._version_with_json(
                project, active_version_id
            )
            legacy_report = {
                "report_type": "program_review",
                "trigger": "legacy",
                "depth": "basic",
                "status": "complete",
                "base_version_id": active_version_id,
                "plc_model": active_version.get("plc_model")
                or project.get("plc_model", "FX3U"),
                "summary": tr('旧版本 validation.findings 已按统一报告格式展示。'),
                "findings": legacy_findings,
            }
            if selected_legacy and isinstance(selected_legacy[1], dict):
                legacy_report["base_json_hash"] = self._json_sha256(
                    selected_legacy[1]
                )
            legacy_card = InspectionReportCard(
                legacy_report,
                latest_version_id=latest_ladder_id,
                current_version_id=self.current_version_id,
                base_ladder=(
                    selected_legacy[1]
                    if selected_legacy and isinstance(selected_legacy[1], dict)
                    else None
                ),
            )
            legacy_card.locate_requested.connect(
                self._locate_inspection_evidence
            )
            self.conversation_layout.addWidget(legacy_card)
        pending = project.get("pending_review")
        review_card = None
        if pending:
            review_card = RequirementReviewCard(
                pending.get("analysis", {}),
                pending.get("request", ""),
                pending.get("draft") or project.get("confirmed_spec"),
                plc_model=project.get("plc_model", "FX3U"),
            )
            review_card.confirmed.connect(
                lambda spec, pid=project["id"]: self._confirm_review(pid, spec)
            )
            review_card.draft_revise_requested.connect(
                lambda text, draft, pid=project["id"]: self._revise_review_with_draft(
                    pid, text, draft
                )
            )
            self.conversation_layout.addWidget(review_card)
        self.conversation_layout.addStretch()
        if review_card is not None:
            QTimer.singleShot(
                0,
                lambda card=review_card: self.conversation_scroll.verticalScrollBar().setValue(
                    max(0, card.y() - 4)
                ),
            )
        else:
            QTimer.singleShot(
                0,
                lambda: self.conversation_scroll.verticalScrollBar().setValue(
                    self.conversation_scroll.verticalScrollBar().maximum()
                ),
            )

    def _refresh_versions(self, project):
        self.version_list.blockSignals(True)
        self.version_list.clear()
        for version in reversed(project.get("versions", [])):
            mode = tr('梯形图') if version.get("target_mode") == "ladder" else "ST"
            parent = version.get("parent_version_id")
            lineage = f" ← {version_display_name(parent)}" if parent else ""
            item = QListWidgetItem(
                codicon_icon("versions"),
                f"{version_display_name(version['id'])}{lineage}  {mode}",
            )
            item.setData(Qt.ItemDataRole.UserRole, version["id"])
            self.version_list.addItem(item)
        self.version_list.blockSignals(False)

    def _select_version(self, version_id):
        for index in range(self.version_list.count()):
            item = self.version_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == version_id:
                self.version_list.blockSignals(True)
                try:
                    self.version_list.setCurrentItem(item)
                finally:
                    self.version_list.blockSignals(False)
                self._load_version(version_id)
                return

    def _version_selected(self, current, previous):
        if current:
            self._load_version(current.data(Qt.ItemDataRole.UserRole))

    def _load_version(self, version_id):
        if not self.current_project_id:
            return
        version = self.store.get_version(self.current_project_id, version_id)
        if not version:
            return
        self.current_version_id = version_id
        version_dir = self.store.version_dir(self.current_project_id, version_id)
        artifacts = version.get("artifacts", {})
        mode = version.get("target_mode")
        self.artifact_caption.setText(
            tr('{v0} · {v1} · 只读', v0=version_display_name(version_id), v1=tr('梯形图') if mode == 'ladder' else 'ST')
        )
        self.validation_view.setPlainText(
            self._format_validation_text(version.get("validation", {}))
        )
        if mode == "ladder":
            svg_path = version_dir / artifacts.get("svg", "")
            json_path = version_dir / artifacts.get("json", "")
            ir_path = version_dir / artifacts.get("ir", "")
            source_text = ""
            ir_source_text = ""
            if ir_path.exists():
                try:
                    ir_source_text = ir_path.read_text(encoding="utf-8")
                    source_text = json.dumps(
                        ir_to_ladder(json.loads(ir_source_text)),
                        ensure_ascii=False,
                        indent=2,
                    )
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    source_text = ""
                    ir_source_text = ""
            if not source_text and json_path.exists():
                source_text = json_path.read_text(encoding="utf-8")
            preview_svg = ""
            preview_width = 0
            preview_height = 0
            if source_text:
                try:
                    # Re-render from the immutable JSON in memory so legacy
                    # SVG files immediately gain continuous display numbers.
                    # Neither ladder.json nor its stored SVG is rewritten.
                    drawer = AdvancedSVGLadder()
                    preview_svg = drawer.generate_ladder(source_text)
                    preview_width = int(drawer.width)
                    preview_height = int(drawer.height)
                except (
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                    KeyError,
                    IndexError,
                    AttributeError,
                ):
                    preview_svg = ""
            if not preview_svg and svg_path.exists():
                preview_svg = svg_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            if preview_svg:
                preview_svg = normalize_svg_for_preview(
                    preview_svg, self.theme_manager.current_theme
                )
                self.svg_viewer.load(bytearray(preview_svg.encode("utf-8")))
                renderer_size = self.svg_viewer.renderer().defaultSize()
                if renderer_size.isValid() and renderer_size.width() > 0:
                    self._ladder_natural_size = renderer_size
                else:
                    width = max(
                        1, preview_width or int(version.get("width", 640))
                    )
                    height = max(
                        1, preview_height or int(version.get("height", 420))
                    )
                    self._ladder_natural_size = renderer_size.__class__(
                        width, height
                    )
                self._fit_ladder_to_viewport()
                QTimer.singleShot(0, self._fit_ladder_to_viewport)
                self.preview_stack.setCurrentIndex(1)
            # Preserve the existing JSON tab for users while the authoritative
            # program.ir.json remains an internal project artifact.
            self.source_view.setPlainText(source_text)
            self.artifact_tabs.setTabIcon(3, codicon_icon("json"))
            self.artifact_tabs.setTabText(3, "JSON")
            try:
                comments = json.loads(source_text).get("device_comments", {})
                self.io_view.setPlainText(
                    "\n".join(f"{key}: {value}" for key, value in comments.items())
                )
            except (json.JSONDecodeError, AttributeError):
                self.io_view.clear()
        else:
            st_path = version_dir / artifacts.get("st", "")
            source_text = (
                st_path.read_text(encoding="utf-8") if st_path.exists() else ""
            )
            self.st_preview.setPlainText(source_text)
            self.preview_stack.setCurrentIndex(2)
            self.source_view.setPlainText(source_text)
            self.io_view.setPlainText(tr('ST 版本未生成独立 I/O 注释表。'))
            self.artifact_tabs.setTabIcon(3, codicon_icon("code"))
            self.artifact_tabs.setTabText(3, "ST")
        self.export_button.setEnabled(True)
        has_contract_mismatch = bool(version.get("contract_mismatch"))
        self.contract_repair_button.setVisible(
            mode == "ladder" and has_contract_mismatch
        )
        self.contract_repair_button.setEnabled(
            mode == "ladder"
            and has_contract_mismatch
            and self.active_task is None
        )
        self._update_gx_sync_button_enabled()
        self._set_gx_sync_status(
            "unknown" if mode == "ladder" else "unknown",
            tr('可直接写入或读取GX Works2；需要比较双方改动时使用“高级同步”') if mode == "ladder" else tr('ST版本不使用GX Works2梯形图同步'),
        )
        self.simulator_test_button.setEnabled(mode == "ladder")
        self._update_workflow_ui()

    def _clear_artifacts(self):
        self.current_version_id = None
        self._ladder_natural_size = None
        self.preview_stack.setCurrentIndex(0)
        self.validation_view.clear()
        self.io_view.clear()
        self.source_view.clear()
        self.artifact_caption.setText(tr('尚未生成'))
        self.export_button.setEnabled(False)
        self.contract_repair_button.setEnabled(False)
        self.contract_repair_button.setVisible(False)
        self._update_gx_sync_button_enabled()
        self._set_gx_sync_status("unknown")
        self.simulator_test_button.setEnabled(False)
        self.simulation_progress_panel.setVisible(False)
        self._update_workflow_ui()

    @staticmethod
    def _format_validation_text(validation):
        validation = validation or {}
        hard_messages = validation.get("messages", []) or []
        review_messages = validation.get("review_messages", []) or []
        lines = [tr('硬校验')]
        lines.extend(hard_messages or [tr('已通过')])
        lines.append("")
        lines.append(tr('评审建议'))
        lines.extend(review_messages or [tr('无')])
        return naturalize_display_text("\n".join(str(item) for item in lines))

    def _fit_ladder_to_viewport(self):
        natural = self._ladder_natural_size
        if not natural or not natural.isValid() or natural.width() <= 0:
            return
        viewport_width = max(1, self.ladder_scroll.viewport().width() - 4)
        viewport_height = max(1, self.ladder_scroll.viewport().height() - 4)
        width_scale = viewport_width / natural.width()
        short_diagram_scale = min(
            1.0, (viewport_height * 0.78) / natural.height()
        )
        scale = max(width_scale, short_diagram_scale)
        fitted_width = max(1, round(natural.width() * scale))
        fitted_height = max(1, round(natural.height() * scale))
        self.svg_viewer.setFixedSize(
            fitted_width,
            min(30000, fitted_height),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_resize_handles()
        self._update_titlebar_density()
        if hasattr(self, "ladder_scroll"):
            QTimer.singleShot(0, self._fit_ladder_to_viewport)

    def _api_history(self, project):
        history = []
        for message in project.get("messages", []):
            if message.get("role") not in {"user", "assistant"}:
                continue
            if message.get("kind", "message") != "message":
                continue
            history.append(
                {
                    "role": message["role"],
                    "content": message.get("content", ""),
                }
            )
        return history

    def _tool_agent_context(self, project):
        """Snapshot the selected project/version before the worker starts."""
        from agent_runtime.plc_tools import build_tool_context

        version = None
        ladder = None
        program_ir = None
        if self.current_version_id:
            version = self.store.get_version(
                project["id"], self.current_version_id
            )
            if version and version.get("target_mode") == "ladder":
                ladder = self.store.load_ladder(
                    project["id"], self.current_version_id
                )
                program_ir = self.store.load_program_ir(
                    project["id"], self.current_version_id
                )
        return build_tool_context(
            project,
            version=version,
            ladder=ladder,
            program_ir=program_ir,
        )

    def _start_tool_agent_task(self, project, text):
        """Start a safe tool turn without disturbing the generation workflow."""
        if not self._ensure_api_configured():
            self.statusBar().showMessage(tr('需要先完成 API 配置。'), 4000)
            return False
        history_before = self._api_history(project)
        try:
            context = self._tool_agent_context(project)
        except Exception as error:
            self.statusBar().showMessage(
                tr('读取当前程序失败：{v0}', v0=naturalize_display_text(error)),
                5000,
            )
            return False

        self.store.add_message(project["id"], "user", text)
        self.composer_edit.clear()
        project = self.store.get_project(project["id"])
        self._render_conversation(project)
        self._refresh_projects(project["id"])

        task_id = f"agent-{uuid.uuid4().hex[:10]}"
        self.active_task = {
            "id": task_id,
            "project_id": project["id"],
            "phase": "tool_agent",
            "request": text,
        }
        self._set_busy(True, tr('正在执行工程工具'))
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('正在准备工程工具'))
        thread = ToolAgentThread(
            task_id,
            text,
            context,
            conversation_history=history_before,
        )
        self._retain_worker_thread("_tool_agent_thread", thread)
        thread.agent_done.connect(self._tool_agent_done)
        thread.agent_failed.connect(self._tool_agent_failed)
        thread.progress_updated.connect(self._tool_agent_progress)
        thread.thinking_updated.connect(self._append_reasoning)
        thread.content_updated.connect(self._append_content)
        thread.start()
        return True

    def _tool_agent_progress(self, task_id, message):
        if not self.active_task or self.active_task.get("id") != task_id:
            return
        self._flush_activity_display_streams()
        display_message = naturalize_display_text(message)
        self.activity_panel.set_status(display_message)
        self.activity_panel.append_content(f"\n[{display_message}]\n")
        self.conversation_status.setText(display_message)

    def _tool_agent_done(self, task_id, payload):
        task = self.active_task
        if not task or task.get("id") != task_id:
            return
        project_id = task["project_id"]
        payload = dict(payload or {})
        content = str(
            payload.get("content") or tr('工具任务已完成。')
        )
        self.store.add_message(
            project_id,
            "assistant",
            content,
            kind="agent",
            metadata={
                "tool_audit": list(payload.get("audit") or []),
                "rounds": payload.get("rounds"),
            },
        )
        pending_actions = [
            dict(action)
            for action in (payload.get("pending_actions") or [])
            if isinstance(action, dict)
        ]
        self.active_task = None
        self._set_busy(False, tr('工具任务完成'))
        self.activity_panel.set_status(tr('工具任务完成'))
        self._refresh_projects(self.current_project_id)
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))
        for action in pending_actions:
            action_type = action.get("type")
            if action_type == "accept_candidate_patch":
                self._confirm_agent_candidate_patch(action)
                break
            if action_type == "import_current_program_to_gxworks2":
                self._confirm_agent_gxworks2_import(action)
                break

    def _tool_agent_failed(self, task_id, error):
        task = self.active_task
        if not task or task.get("id") != task_id:
            return
        project_id = task["project_id"]
        self.store.add_message(
            project_id,
            "assistant",
            naturalize_display_text(error),
            kind="system",
            metadata={"source": "tool_agent"},
        )
        self.active_task = None
        self._set_busy(False, tr('工具任务失败'))
        self.activity_panel.show_error(naturalize_display_text(error))
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _confirm_agent_candidate_patch(self, action):
        """Show the deterministic diff before accepting a local candidate."""

        if not isinstance(action, dict):
            return
        requested_project = str(action.get("project_id") or "")
        base_version_id = str(action.get("base_version_id") or "")
        if (
            not requested_project
            or not base_version_id
            or requested_project != self.current_project_id
            or base_version_id != self.current_version_id
        ):
            QMessageBox.warning(
                self,
                tr('候选补丁已过期'),
                tr('候选补丁绑定的项目或基础版本已经变化，请重新提出修改要求。'),
            )
            return

        diff = action.get("diff") or {}
        changes = diff.get("changes") or []
        lines = []
        for change in changes[:30]:
            if not isinstance(change, dict):
                continue
            marker = str(change.get("marker") or "~")
            network = str(change.get("network") or tr('未知网络'))
            comment = naturalize_display_text(change.get("comment") or tr('未命名网络'))
            instruction_count = int(change.get("instruction_count") or 0)
            lines.append(
                tr('{v0} {v1}  {v2}（{v3} 条指令）', v0=marker, v1=network, v2=comment, v3=instruction_count)
            )
        if len(changes) > len(lines):
            lines.append(tr('…另有 {v0} 项变更', v0=len(changes) - len(lines)))
        if diff.get("device_comments_changed"):
            lines.append(tr('~ 软元件注释'))
        if not lines:
            lines.append(tr('未检测到 Network 或注释差异'))

        counts = (action.get("diagnostics") or {}).get("counts") or {}
        answer = QMessageBox.question(
            self,
            tr('查看并接受候选补丁'),
            (
                tr('项目：{v0}\n基础版本：{v1}\n目标修订：{v2}\n\n差异：\n', v0=naturalize_display_text(action.get('project_name') or requested_project), v1=version_display_name(base_version_id), v2=action.get('target_revision'))
                + "\n".join(lines)
                + tr('\n\n确定性校验：')
                + tr('错误 {v0}，', v0=int(counts.get('error', 0) or 0))
                + tr('警告 {v0}，', v0=int(counts.get('warning', 0) or 0))
                + tr('提示 {v0}。\n\n', v0=int(counts.get('info', 0) or 0))
                + tr('接受后只创建本地新版本，不会自动同步 GX Works2。是否接受？')
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(tr('已丢弃候选补丁，当前版本未改变。'), 5000)
            return

        try:
            from plc.core import accept_candidate_patch

            version = accept_candidate_patch(self.store, action)
        except Exception as error:
            QMessageBox.critical(
                self,
                tr('候选补丁未接受'),
                naturalize_display_text(error),
            )
            return

        version_id = str(version.get("id") or "")
        self.store.add_message(
            requested_project,
            "assistant",
            tr('已接受候选补丁并创建本地{v0}；尚未同步 GX Works2。', v0=version_display_name(version_id)),
            kind="system",
            metadata={
                "source": "candidate_patch_confirmation",
                "candidate_id": action.get("candidate_id"),
                "base_version_id": base_version_id,
                "version_id": version_id,
                "diff": copy.deepcopy(diff),
            },
        )
        project = self.store.get_project(requested_project)
        self._refresh_projects(requested_project)
        if project and requested_project == self.current_project_id:
            self._refresh_versions(project)
            self._select_version(version_id)
            self._render_conversation(self.store.get_project(requested_project))
        self.statusBar().showMessage(
            tr('已创建本地{v0}，未同步 GX Works2。', v0=version_display_name(version_id)),
            6000,
        )

    def _confirm_agent_gxworks2_import(self, action):
        """Bind a model-requested action to the still-selected immutable version."""
        if not isinstance(action, dict):
            return
        requested_project = str(action.get("project_id") or "")
        requested_version = str(action.get("version_id") or "")
        if (
            not requested_project
            or not requested_version
            or requested_project != self.current_project_id
            or requested_version != self.current_version_id
        ):
            QMessageBox.warning(
                self,
                tr('无法执行导入'),
                tr('AI 请求所绑定的项目或版本已经发生变化，请重新发出导入请求。'),
            )
            return
        answer = QMessageBox.question(
            self,
            tr('确认同步 GX Works2'),
            (
                tr('项目：{v0}\n版本：{v1}\n程序：{v2}\n\n将先读取并比较GX Works2当前程序与注释：只有项目侧变化时才写入，GX侧变化会回读为新版本，双方变化时会要求你选择。是否继续？', v0=naturalize_display_text(action.get('project_name') or requested_project), v1=version_display_name(requested_version), v2=action.get('program_name') or 'MAIN')
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(tr('已取消 AI 请求的 GX Works2 导入。'), 4000)
            return
        self._sync_current_version_with_gxworks2()

    def _inspection_history(self, project, base_version_id):
        history = self._api_history(project)[-12:]
        try:
            reports = self.store.list_reports(
                project["id"], base_version_id=base_version_id
            )
        except Exception:
            reports = []
        for item in reports[:3]:
            report_id = item.get("report_id") or item.get("id")
            report = self.store.get_report(project["id"], report_id)
            if not report:
                continue
            compact = {
                "report_type": report.get("report_type"),
                "summary": report.get("summary"),
                "findings": [
                    {
                        "title": finding.get("title"),
                        "message": finding.get("message"),
                        "suggestion": (
                            finding.get("suggestion")
                            or finding.get("recommendation")
                        ),
                        "resolution_status": finding.get("resolution_status"),
                    }
                    for finding in (report.get("findings") or [])[:8]
                    if isinstance(finding, dict)
                ],
            }
            history.append(
                {
                    "role": "assistant",
                    "content": tr('同一版本的既往诊断：')
                    + json.dumps(compact, ensure_ascii=False),
                }
            )
        return history

    @staticmethod
    def _version_confirmed_spec(version):
        snapshot = version.get("confirmed_spec_snapshot")
        return canonicalize_confirmed_spec(snapshot) if snapshot else None

    def _resolve_version_plc_model(self, project, version, purpose):
        """Return the immutable version model, explicitly confirming old data."""
        stored_model = str(
            version.get("plc_model") or project.get("plc_model") or "FX3U"
        ).upper()
        if version.get("plc_model") and version.get("confirmed_spec_snapshot"):
            return stored_model
        models = ["FX3U", "FX5U"]
        default_index = models.index(stored_model) if stored_model in models else 0
        selected, accepted = QInputDialog.getItem(
            self,
            tr('确认旧版本 PLC 型号'),
            (
                tr('{v0} 缺少完整规格快照。\n请确认本次{v1}使用的 PLC 型号；只会读取该版本 JSON，不会套用项目最新规格。', v0=version.get('id', tr('旧版本')), v1=purpose)
            ),
            models,
            default_index,
            False,
        )
        return str(selected).upper() if accepted else None

    @staticmethod
    def _json_sha256(payload):
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _start_inspection_task(self, project, workflow_mode, text):
        selected = self._version_with_json(project, self.current_version_id)
        if not selected:
            self.statusBar().showMessage(tr('请先选择一个可读取的生成版本。'), 4000)
            return False
        version, current_json = selected
        if version.get("target_mode") != "ladder" or not isinstance(
            current_json, dict
        ):
            self.statusBar().showMessage(
                tr('首期仅支持梯形图版本评审与故障调试。'), 5000
            )
            return False
        if workflow_mode == "debug":
            failed_runs = [
                item
                for item in self.store.list_simulator_runs(
                    project["id"], version["id"]
                )
                if isinstance(item, dict) and item.get("status") == "failed"
            ]
            if failed_runs:
                return self._start_evidence_debug_plan(
                    project, version, failed_runs[-1], text
                )
            if not text:
                self.statusBar().showMessage(
                    tr('当前版本没有失败仿真记录，请先运行测试或描述故障现象。'),
                    5000,
                )
                return False

        report_type = "fault_debug" if workflow_mode == "debug" else "program_review"
        if report_type == "fault_debug":
            debug_context = (
                self.debug_context_widget.to_dict()
                if hasattr(self, "debug_context_widget")
                else {}
            )
            request = {"symptom": text, **debug_context}
            display_text = text
        else:
            request = {"review_focus": text}
            display_text = text or tr('执行完整版本评审')

        confirmed_spec = self._version_confirmed_spec(version)
        plc_model = self._resolve_version_plc_model(
            project,
            version,
            tr('评审') if report_type == "program_review" else tr('调试'),
        )
        if not plc_model:
            self.statusBar().showMessage(tr('已取消：未确认旧版本 PLC 型号。'), 4000)
            return False
        self.store.add_message(project["id"], "user", display_text)
        self.composer_edit.clear()
        if hasattr(self, "debug_context_widget"):
            self.debug_context_widget.clear()
        task_id = f"inspection-{uuid.uuid4().hex[:10]}"
        self.active_task = {
            "id": task_id,
            "project_id": project["id"],
            "phase": "inspection",
            "report_type": report_type,
            "request": request,
            "base_version_id": version["id"],
            "base_json": current_json,
            "plc_model": plc_model,
            "report_id": None,
        }
        self._set_busy(
            True,
            tr('正在版本评审') if report_type == "program_review" else tr('正在分析故障'),
        )
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('正在执行本地规则'))
        thread = InspectionThread(
            task_id,
            report_type,
            request,
            current_json,
            version["id"],
            plc_model,
            project_id=project["id"],
            program_ir=self.store.load_program_ir(project["id"], version["id"]),
            confirmed_spec=confirmed_spec,
            conversation_history=self._inspection_history(
                project, version["id"]
            ),
            effort=None,
            deep=True,
        )
        self._retain_worker_thread("_inspection_thread", thread)
        thread.local_ready.connect(self._inspection_local_ready)
        thread.inspection_done.connect(self._inspection_done)
        thread.inspection_failed.connect(
            self._inspection_failed
        )
        thread.progress_updated.connect(
            self._inspection_progress
        )
        thread.start()
        self._render_conversation(self.store.get_project(project["id"]))
        self._refresh_projects(project["id"])
        return True

    def _send_requirement(self):
        if self.active_task:
            self.statusBar().showMessage(tr('当前已有任务运行，请等待完成。'), 4000)
            return
        text = self.composer_edit.toPlainText().strip()
        has_images = bool(self._composer_image_paths)
        if not self.current_project_id:
            self.statusBar().showMessage(tr('请先选择项目。'), 3000)
            return
        project = self.store.get_project(self.current_project_id)
        workflow_mode = project.get("workflow_mode", "generate")
        if workflow_mode in {"review", "debug"}:
            self._start_inspection_task(project, workflow_mode, text)
            return
        if not text and not has_images:
            self.statusBar().showMessage(tr('请先输入控制需求或添加图片。'), 3000)
            return
        if not text:
            text = tr('请结合所附图片分析并生成控制方案。')
        if has_images and not self._ensure_image_capable_model():
            return
        from agent_runtime.agent import should_route_to_tool_agent

        selected_version = (
            self.store.get_version(project["id"], self.current_version_id)
            if self.current_version_id
            else None
        )
        if not has_images and should_route_to_tool_agent(
            text,
            has_current_program=bool(
                selected_version
                and selected_version.get("target_mode") == "ladder"
            ),
        ):
            self._start_tool_agent_task(project, text)
            return
        if not self._ensure_api_configured():
            self.statusBar().showMessage(tr('需要先完成 API 配置。'), 4000)
            return
        try:
            image_records, model_images = self._persist_composer_images(
                project["id"]
            )
        except Exception as error:
            QMessageBox.warning(
                self,
                tr('图片未添加'),
                naturalize_display_text(error),
            )
            return
        confirmed_spec = canonicalize_confirmed_spec(
            project.get("confirmed_spec")
        ) if project.get("confirmed_spec") else None
        preserved_spec_draft = copy.deepcopy(
            project.get("preserved_spec_draft")
        )
        if confirmed_spec != project.get("confirmed_spec"):
            self.store.set_confirmed_spec(project["id"], confirmed_spec)
            project = self.store.get_project(project["id"])
        if confirmed_spec and _is_regenerate_locked_spec_request(text):
            # "重新生成" is an execution command for the locked specification,
            # not a new control requirement.  Sending it through requirement
            # analysis can create a second control_method question and silently
            # replace the user's already confirmed implementation.
            self.store.add_message(
                project["id"],
                "user",
                text,
                metadata={"image_attachments": image_records},
            )
            self.composer_edit.clear()
            self._clear_composer_images()
            self.store.set_pending_review(
                project["id"],
                {
                    "request": (
                        tr('请严格按当前已确认规格重新生成完整程序；不重新分析需求，不改变已选方案、参数、I/O 或硬件接口。')
                    ),
                    "analysis": {},
                    "draft": copy.deepcopy(confirmed_spec),
                    "image_attachments": image_records,
                },
            )
            self._confirm_review(project["id"], confirmed_spec)
            return
        history_before = self._api_history(project)
        self.store.add_message(
            project["id"],
            "user",
            text,
            metadata={"image_attachments": image_records},
        )
        if project["name"] == tr('新项目'):
            name = text.replace("\n", " ")[:18]
            self.store.update_project_settings(project["id"], name=name)
        self.composer_edit.clear()
        self._clear_composer_images()
        project = self.store.get_project(project["id"])
        self._render_conversation(project)
        self._refresh_projects(project["id"])

        task_id = f"analysis-{uuid.uuid4().hex[:10]}"
        self.active_task = {
            "id": task_id,
            "project_id": project["id"],
            "phase": "analysis",
            "request": text,
            "history_before": history_before,
            "task_type": workflow_mode,
            "preserved_spec_draft": preserved_spec_draft,
            "image_attachments": image_records,
            "model_images": model_images,
        }
        self._set_busy(True, tr('正在分析需求'))
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('分析需求'))
        thread = AnalysisThread(
            task_id,
            tr('目标 PLC 型号：{v0}\n{v1}', v0=project.get('plc_model', 'FX3U'), v1=text),
            conversation_history=history_before,
            confirmed_context=(
                {"_context_phase": "analysis_baseline", **confirmed_spec}
                if confirmed_spec
                else None
            ),
            task_type=workflow_mode,
            image_attachments=model_images,
        )
        self._retain_worker_thread("_analysis_thread", thread)
        thread.analysis_done.connect(self._analysis_done)
        thread.analysis_failed.connect(self._analysis_failed)
        thread.thinking_updated.connect(self._append_reasoning)
        thread.content_updated.connect(self._append_content)
        thread.start()

    def _start_evidence_debug_plan(self, project, version, run_record, text=""):
        if not self._ensure_api_configured():
            self.statusBar().showMessage(tr('证据化调试需要先完成 API 配置。'), 5000)
            return False
        run_id = str(run_record.get("run_id") or "")
        display_text = text or tr('分析最近一次失败的仿真测试')
        self.store.add_message(project["id"], "user", display_text)
        self.composer_edit.clear()
        task_id = f"debug-plan-{uuid.uuid4().hex[:10]}"
        self.active_task = {
            "id": task_id,
            "project_id": project["id"],
            "phase": "debug_plan",
            "base_version_id": version["id"],
            "run_id": run_id,
        }
        self._set_busy(True, tr('正在分析仿真失败证据'))
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('正在整理失败轨迹和反向依赖'))
        thread = EvidenceDebugPlanThread(
            task_id,
            self.store,
            project["id"],
            version["id"],
            run_id,
            effort=None,
        )
        self._retain_worker_thread("_evidence_debug_plan_thread", thread)
        thread.plan_ready.connect(self._evidence_debug_plan_ready)
        thread.plan_failed.connect(self._evidence_debug_plan_failed)
        thread.progress_updated.connect(self._inspection_progress)
        thread.start()
        self._render_conversation(self.store.get_project(project["id"]))
        return True

    def _evidence_debug_plan_ready(self, task_id, plan):
        task = self.active_task
        if not task or task.get("id") != task_id:
            return
        diagnosis = plan.get("diagnosis") or {}
        affected_networks = diagnosis.get("affected_networks") or []
        affected = "、".join(
            naturalize_identifier(item, kind=tr('程序段'), index=index)
            for index, item in enumerate(affected_networks, start=1)
        ) or tr('暂未定位到具体程序段')
        operations = plan.get("patch", {}).get("operations") or []
        evidence = plan.get("evidence") or {}
        answer = QMessageBox.question(
            self,
            tr('确认执行仿真修复闭环'),
            (
                tr('基础版本：{v0}\n失败来源：最近一次仿真测试\n根因判断：{v1}\n置信度：{v2:.0%}\n修改网络：{v3}\n局部操作数：{v4}\n证据条目：{v5} 个失败，{v6} 条轨迹\n\n确认后将创建候选版本、导入 GX Works2 并运行完整回归。只有全部通过才会激活；失败将自动恢复原版本。', v0=version_display_name(plan.get('base_version_id')), v1=naturalize_display_text(diagnosis.get('root_cause', '')), v2=float(diagnosis.get('confidence') or 0), v3=affected, v4=len(operations), v5=len(evidence.get('failures') or []), v6=len(evidence.get('device_trace') or []))
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            project_id = task["project_id"]
            self.active_task = None
            self._set_busy(False, tr('已取消执行，调试方案已保留'))
            self.activity_panel.set_status(tr('调试方案已保留，未修改程序'))
            self.store.add_message(
                project_id,
                "assistant",
                tr('已生成证据化调试方案，但未获确认执行；程序与 GX Works2 均未修改。'),
                kind="system",
                metadata={"debug_plan_id": plan.get("plan_id")},
            )
            self._render_conversation(self.store.get_project(project_id))
            return
        self.active_task["phase"] = "debug_execute"
        self.active_task["plan_id"] = plan.get("plan_id")
        self._set_busy(True, tr('正在执行调试闭环'))
        self.activity_panel.set_status(tr('正在校验候选补丁'))
        thread = EvidenceDebugExecuteThread(task_id, self.store, plan)
        self._retain_worker_thread("_evidence_debug_execute_thread", thread)
        thread.completed.connect(self._evidence_debug_execute_done)
        thread.failed.connect(self._evidence_debug_execute_failed)
        thread.progress_updated.connect(self._inspection_progress)
        thread.start()

    def _evidence_debug_plan_failed(self, task_id, error):
        task = self.active_task
        if not task or task.get("id") != task_id:
            return
        project_id = task["project_id"]
        self.active_task = None
        self._set_busy(False, tr('证据化调试方案生成失败'))
        display_error = naturalize_display_text(error)
        self.activity_panel.show_error(display_error)
        self.store.add_message(
            project_id,
            "assistant",
            tr('失败仿真未生成可执行补丁：{v0}', v0=display_error),
            kind="system",
            metadata={"workflow_mode": "debug"},
        )
        self._render_conversation(self.store.get_project(project_id))

    def _evidence_debug_execute_done(self, task_id, attempt):
        task = self.active_task
        if not task or task.get("id") != task_id:
            return
        project_id = task["project_id"]
        self.active_task = None
        status = str(attempt.get("status") or "error")
        passed = status == "passed"
        self._set_busy(False, tr('调试回归通过') if passed else tr('调试回归未通过'))
        if passed:
            self.activity_panel.set_status(tr('补丁已通过完整回归'))
            QMessageBox.information(
                self,
                tr('调试闭环完成'),
                tr('{v0}\n新版本：{v1}', v0=naturalize_display_text(attempt.get('message')), v1=version_display_name(attempt.get('candidate_version_id'))),
            )
        else:
            self.activity_panel.show_error(
                naturalize_display_text(attempt.get("message") or status)
            )
            QMessageBox.warning(
                self,
                tr('调试闭环未通过'),
                (
                    tr('{v0}\n原版本：{v1}\n回滚：{v2}', v0=naturalize_display_text(attempt.get('message')), v1=version_display_name(attempt.get('base_version_id') or task.get('base_version_id')), v2=tr('已恢复') if (attempt.get('rollback') or {}).get('restored') else tr('无需恢复或恢复失败'))
                ),
            )
        self.store.add_message(
            project_id,
            "assistant",
            naturalize_display_text(attempt.get("message") or tr('调试闭环已结束')),
            kind="system",
            metadata={
                "workflow_mode": "debug_loop",
                "debug_attempt_id": attempt.get("attempt_id"),
                "version_id": attempt.get("candidate_version_id") if passed else task.get("base_version_id"),
            },
        )
        self._refresh_projects(project_id)
        project = self.store.get_project(project_id)
        if project and project.get("active_version_id"):
            self._select_version(project["active_version_id"])
        self._render_conversation(project)

    def _evidence_debug_execute_failed(self, task_id, error):
        task = self.active_task
        if not task or task.get("id") != task_id:
            return
        project_id = task["project_id"]
        self.active_task = None
        self._set_busy(False, tr('调试闭环执行失败'))
        display_error = naturalize_display_text(error)
        self.activity_panel.show_error(display_error)
        QMessageBox.warning(self, tr('调试闭环执行失败'), display_error)
        self._refresh_projects(project_id)
        self._render_conversation(self.store.get_project(project_id))

    def _analysis_done(self, task_id, analysis):
        task = self.active_task
        if not task or task["id"] != task_id:
            return
        project_id = task["project_id"]
        self.store.set_pending_review(
            project_id,
            {
                "request": task["request"],
                "analysis": analysis,
                "history_before": task["history_before"],
                "created_at": task_id,
                "draft": task.get("preserved_spec_draft"),
                "image_attachments": copy.deepcopy(
                    task.get("image_attachments") or []
                ),
            },
        )
        project = self.store.get_project(project_id)
        if project and "preserved_spec_draft" in project:
            project.pop("preserved_spec_draft", None)
            self.store.save_project(project)
        self.store.add_message(
            project_id,
            "assistant",
            tr('需求分析完成，请检查下方确认卡后再生成。'),
            kind="system",
        )
        self.active_task = None
        self._set_busy(False, tr('等待确认'))
        self.activity_panel.set_status(tr('等待确认'))
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))
        self._refresh_projects(self.current_project_id)

    def _analysis_failed(self, task_id, error):
        task = self.active_task
        if not task or task["id"] != task_id:
            return
        project_id = task["project_id"]
        self._restore_composer_images(
            project_id,
            task.get("image_attachments") or [],
        )
        self.store.add_message(
            project_id,
            "assistant",
            tr('需求分析失败：{v0}\n请检查 API 配置后重试，或修改需求描述。', v0=naturalize_display_text(error)),
            kind="system",
        )
        self.active_task = None
        self._set_busy(False, tr('分析失败'))
        self.activity_panel.show_error(naturalize_display_text(error))
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _retry_inspection_ai(self, report_id):
        if self.active_task or not self.current_project_id:
            self.statusBar().showMessage(tr('当前已有任务运行。'), 3000)
            return
        project = self.store.get_project(self.current_project_id)
        report = self.store.get_report(self.current_project_id, report_id)
        if not project or not report:
            self.statusBar().showMessage(tr('报告不存在或已被移除。'), 4000)
            return
        selected = self._version_with_json(
            project, report.get("base_version_id")
        )
        if not selected or not isinstance(selected[1], dict):
            self.statusBar().showMessage(tr('报告绑定版本无法读取。'), 4000)
            return
        version, base_json = selected
        from inspection.engine import hash_ladder_json

        if report.get("base_json_hash") and (
            hash_ladder_json(base_json) != report.get("base_json_hash")
        ):
            self.statusBar().showMessage(
                tr('报告绑定的 JSON 哈希不再匹配，不能重试 AI。'), 5000
            )
            return
        if not self._ensure_api_configured():
            self.statusBar().showMessage(
                tr('未配置 API；本地报告保持不变。'), 4000
            )
            return
        task_id = f"inspection-retry-{uuid.uuid4().hex[:10]}"
        report_type = report.get("report_type", "program_review")
        plc_model = str(
            report.get("plc_model")
            or version.get("plc_model")
            or project.get("plc_model")
            or "FX3U"
        ).upper()
        self.active_task = {
            "id": task_id,
            "project_id": project["id"],
            "phase": "inspection",
            "report_type": report_type,
            "request": copy.deepcopy(report.get("request") or {}),
            "base_version_id": version["id"],
            "base_json": base_json,
            "plc_model": plc_model,
            "report_id": report_id,
            "reuse_report": True,
        }
        self._set_busy(True, tr('正在重试 AI 深查'))
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('正在重新执行本地规则并重试 AI'))
        thread = InspectionThread(
            task_id,
            report_type,
            report.get("request") or {},
            base_json,
            version["id"],
            plc_model,
            project_id=project["id"],
            program_ir=self.store.load_program_ir(project["id"], version["id"]),
            confirmed_spec=self._version_confirmed_spec(version),
            conversation_history=self._inspection_history(
                project, version["id"]
            ),
            effort=None,
            deep=True,
        )
        self._retain_worker_thread("_inspection_thread", thread)
        thread.local_ready.connect(
            self._inspection_local_ready
        )
        thread.inspection_done.connect(
            self._inspection_done
        )
        thread.inspection_failed.connect(
            self._inspection_failed
        )
        thread.progress_updated.connect(
            self._inspection_progress
        )
        thread.start()

    def _inspection_local_ready(self, task_id, report):
        task = self.active_task
        if not task or task["id"] != task_id:
            return
        report = copy.deepcopy(report or {})
        report_id = (
            task.get("report_id")
            or report.get("report_id")
            or f"rpt_{uuid.uuid4().hex[:16]}"
        )
        report["report_id"] = report_id
        report["base_version_id"] = task["base_version_id"]
        if task.get("reuse_report"):
            report["trigger"] = "ai_retry"
            try:
                self.store.update_report(
                    task["project_id"], report_id, report
                )
            except Exception as error:
                self._inspection_failed(
                    task_id, tr('更新本地报告失败：{v0}', v0=error)
                )
                return
            if self.current_project_id == task["project_id"]:
                self._render_conversation(
                    self.store.get_project(task["project_id"])
                )
            self.activity_panel.set_status(tr('本地结果已刷新，正在重试 AI'))
            return
        try:
            created = self.store.create_report(task["project_id"], report)
            if isinstance(created, dict):
                report_id = created.get("report_id", report_id)
        except Exception as error:
            self._inspection_failed(task_id, tr('保存本地报告失败：{v0}', v0=error))
            return
        task["report_id"] = report_id
        self.store.add_message(
            task["project_id"],
            "assistant",
            report.get("summary") or tr('本地检查完成，正在进行 AI 深查。'),
            kind="inspection_report",
            metadata={
                "report_id": report_id,
                "base_version_id": task["base_version_id"],
                "report_type": task["report_type"],
            },
        )
        if self.current_project_id == task["project_id"]:
            self._render_conversation(
                self.store.get_project(task["project_id"])
            )
        self.activity_panel.set_status(tr('本地结果已生成，正在等待 AI'))

    def _inspection_done(self, task_id, report):
        task = self.active_task
        if not task or task["id"] != task_id:
            return
        report = copy.deepcopy(report or {})
        report_id = task.get("report_id") or report.get("report_id")
        if not report_id:
            self._inspection_local_ready(task_id, report)
            report_id = task.get("report_id")
        report["report_id"] = report_id
        report["base_version_id"] = task["base_version_id"]
        if task.get("reuse_report"):
            report["trigger"] = "ai_retry"
        try:
            multi_agent = report.get("multi_agent")
            if isinstance(multi_agent, dict):
                saved_run = self.store.save_multi_agent_run(
                    task["project_id"], task["base_version_id"], multi_agent
                )
                report["multi_agent"]["run_id"] = saved_run["run_id"]
            self.store.update_report(task["project_id"], report_id, report)
        except Exception as error:
            self._inspection_failed(task_id, tr('更新报告失败：{v0}', v0=error))
            return
        execution_status = report.get("status", "complete")
        if execution_status == "complete":
            status = tr('版本评审完成') if task["report_type"] == "program_review" else tr('故障分析完成')
        elif execution_status == "local_only":
            status = (
                tr('仅本地版本评审完成')
                if task["report_type"] == "program_review"
                else tr('仅本地故障初筛完成')
            )
        elif execution_status == "partial":
            status = tr('本地检查完成，AI 深查未完成')
        elif execution_status == "needs_input":
            status = tr('需要补充现场信息')
        elif execution_status == "unsupported":
            status = tr('当前版本暂不支持')
        else:
            status = tr('检查失败')
        project_id = task["project_id"]
        self.active_task = None
        self._set_busy(False, status)
        self.activity_panel.set_status(status)
        self._refresh_projects(self.current_project_id)
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _inspection_failed(self, task_id, error):
        task = self.active_task
        if not task or task["id"] != task_id:
            return
        project_id = task["project_id"]
        if task.get("report_id"):
            report = self.store.get_report(project_id, task["report_id"]) or {}
            report["status"] = (
                "partial" if task.get("reuse_report") else "failed"
            )
            report["ai_error" if task.get("reuse_report") else "local_error"] = str(error)
            self.store.update_report(
                project_id, task["report_id"], report
            )
        else:
            self.store.add_message(
                project_id,
                "assistant",
                tr('评审/调试失败：{v0}', v0=naturalize_display_text(error)),
                kind="system",
            )
        self.active_task = None
        self._set_busy(False, tr('检查失败'))
        self.activity_panel.show_error(naturalize_display_text(error))
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _inspection_progress(self, task_id, message):
        if not self.active_task or self.active_task["id"] != task_id:
            return
        display_message = naturalize_display_text(message)
        self.activity_panel.set_status(display_message)
        self.conversation_status.setText(display_message)

    def _debug_done(self, task_id, report):
        task = self.active_task
        if not task or task["id"] != task_id:
            return
        project_id = task["project_id"]
        report = dict(report or {})
        report.setdefault("base_version_id", task.get("base_version_id"))
        self.store.add_message(
            project_id,
            "assistant",
            report.get("summary", tr('调试报告已生成')),
            kind="debug_report",
            metadata={
                "workflow_mode": "debug",
                "report": report,
                "base_version_id": report.get("base_version_id"),
            },
        )
        self.active_task = None
        self._debug_thread = None
        self._set_busy(False, tr('调试完成'))
        self.activity_panel.set_status(tr('调试完成'))
        self._refresh_projects(self.current_project_id)
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _debug_failed(self, task_id, error):
        task = self.active_task
        if not task or task["id"] != task_id:
            return
        project_id = task["project_id"]
        self.store.add_message(
            project_id,
            "assistant",
            tr('调试失败：{v0}', v0=naturalize_display_text(error)),
            kind="system",
            metadata={"workflow_mode": "debug"},
        )
        self.active_task = None
        self._debug_thread = None
        self._set_busy(False, tr('调试失败'))
        self.activity_panel.show_error(naturalize_display_text(error))
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _copy_debug_fix_to_input(self, text):
        if not text:
            self.statusBar().showMessage(tr('该调试报告没有生成修复要求。'), 3000)
            return
        self.composer_edit.setPlainText(str(text).strip())
        self.composer_edit.setFocus()
        self.statusBar().showMessage(tr('修复要求已放入输入框。'), 3000)

    def _start_debug_fix(self, project_id, report):
        if self.active_task:
            self.statusBar().showMessage(tr('当前已有任务运行。'), 3000)
            return
        if not self._ensure_api_configured():
            self.statusBar().showMessage(tr('需要先完成 API 配置。'), 4000)
            return
        project = self.store.get_project(project_id)
        if not project:
            return
        latest_ladder = self._latest_ladder_version(project)
        if not latest_ladder:
            self.statusBar().showMessage(tr('当前没有可修复的梯形图版本。'), 4000)
            return
        version, previous_json = latest_ladder
        if report.get("base_version_id") != version.get("id"):
            self.statusBar().showMessage(
                tr('该调试报告基于旧版本，请重新调试当前版本。'), 5000
            )
            return
        fix_instruction = str(report.get("fix_instruction", "")).strip()
        if not fix_instruction:
            self.statusBar().showMessage(tr('该调试报告没有修复要求。'), 3000)
            return
        confirmed_spec = canonicalize_confirmed_spec(
            project.get("confirmed_spec")
        ) if project.get("confirmed_spec") else None
        if confirmed_spec != project.get("confirmed_spec"):
            self.store.set_confirmed_spec(project_id, confirmed_spec)
            project = self.store.get_project(project_id)
        plc_model = (
            version.get("plc_model")
            or project.get("plc_model")
            or "FX3U"
        )
        version_id, output_dir = self.store.prepare_version(project_id)
        task_id = f"{project_id}:{version_id}"
        self.active_task = {
            "id": task_id,
            "project_id": project_id,
            "version_id": version_id,
            "phase": "compile",
            "summary": tr('调试修复：{v0}', v0=report.get('summary', '')),
        }
        request = (
            tr('请基于当前版本 JSON 生成调试修复版本。只修改调试报告指出的问题，不要重写无关逻辑。\n\n调试摘要：{v0}\n修复要求：{v1}', v0=report.get('summary', ''), v1=fix_instruction)
        )
        self.store.add_message(
            project_id,
            "assistant",
            tr('已根据调试报告开始生成{v0}。', v0=version_display_name(version_id)),
            kind="system",
            metadata={"workflow_mode": "debug_fix", "version_id": version_id},
        )
        self._set_busy(True, tr('正在生成调试修复版本'))
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('准备生成调试修复版本'))
        thread = CompilerThread(
            task_id,
            request,
            None,
            "ladder",
            output_dir,
            previous_json=previous_json,
            previous_ir=self.store.load_program_ir(project_id, version["id"]),
            conversation_history=[],
            confirmed_context=confirmed_spec,
            task_type="debug_fix",
            current_version_json=previous_json,
            plc_model=plc_model,
            program_name="MAIN",
            revision=int(str(version_id).lstrip("vV") or "1"),
            requirement_text=fix_instruction,
        )
        self._retain_worker_thread("_compiler_thread", thread)
        thread.thinking_updated.connect(self._append_reasoning)
        thread.content_updated.connect(self._append_content)
        thread.progress_updated.connect(self._progress_updated)
        thread.success.connect(self._compile_success)
        thread.failure.connect(self._compile_failure)
        thread.start()
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _locate_inspection_evidence(
        self, version_id, rung_id=None, json_path=""
    ):
        if not self.current_project_id:
            return
        project = self.store.get_project(self.current_project_id)
        selected = self._version_with_json(project, version_id)
        if not selected or not isinstance(selected[1], dict):
            self.statusBar().showMessage(tr('报告绑定版本无法读取。'), 4000)
            return
        self._select_version(version_id)
        version, ladder = selected
        if rung_id is None:
            self.artifact_tabs.setCurrentIndex(3)
            return
        try:
            rung_id = int(rung_id)
            display_map = build_rung_display_map(ladder)
            path_index = rung_index_from_path(json_path)
            if path_index is not None:
                path_location = display_map.get("by_index", {}).get(path_index)
                if (
                    isinstance(path_location, dict)
                    and path_location.get("raw_rung_id") not in (None, "")
                ):
                    # A report path is bound to the selected version and is
                    # more precise than an AI-provided/display-only number.
                    rung_id = int(path_location["raw_rung_id"])
            display_number = display_number_for_anchor(
                display_map,
                raw_rung_id=rung_id,
                json_path=json_path,
            )
            drawer = AdvancedSVGLadder()
            highlighted = drawer.generate_ladder(
                json.dumps(ladder, ensure_ascii=False),
                highlight_rung_ids=[rung_id],
            )
            highlighted = normalize_svg_for_preview(
                highlighted, self.theme_manager.current_theme
            )
            self.svg_viewer.load(bytearray(highlighted.encode("utf-8")))
            self._ladder_natural_size = self.svg_viewer.renderer().defaultSize()
            self._fit_ladder_to_viewport()
            bounds = drawer.rung_bounds.get(rung_id, {})

            def scroll_to_rung():
                natural_height = max(1, drawer.height)
                rendered_height = max(1, self.svg_viewer.height())
                target = int(
                    float(bounds.get("top", 0))
                    * rendered_height
                    / natural_height
                )
                self.ladder_scroll.verticalScrollBar().setValue(
                    max(0, target - 28)
                )

            self.artifact_tabs.setCurrentIndex(0)
            QTimer.singleShot(0, scroll_to_rung)
            self.source_view.moveCursor(
                QTextCursor.MoveOperation.Start
            )
            self.source_view.find(f'"rung_id": {rung_id}')
            self.statusBar().showMessage(
                tr('已定位{v0}的梯级 {v1}', v0=version_display_name(version_id), v1=display_number if display_number is not None else rung_id),
                5000,
            )
        except Exception as error:
            self.statusBar().showMessage(
                tr('定位失败：{v0}', v0=naturalize_display_text(error)), 4000
            )

    def _start_inspection_repair(self, report_id, selected_finding_ids):
        if self.active_task or not self.current_project_id:
            self.statusBar().showMessage(tr('当前已有任务运行。'), 3000)
            return
        project_id = self.current_project_id
        project = self.store.get_project(project_id)
        report = self.store.get_report(project_id, report_id)
        if not report:
            self.statusBar().showMessage(tr('诊断报告不存在。'), 4000)
            return
        selected_ids = list(dict.fromkeys(selected_finding_ids or []))
        findings = [
            item
            for item in report.get("findings", [])
            if isinstance(item, dict)
            and (item.get("finding_id") or item.get("id")) in selected_ids
            and item.get("fixable")
            and str(item.get("fix_instruction", "")).strip()
        ]
        if not findings:
            self.statusBar().showMessage(tr('请至少勾选一个可修复问题。'), 4000)
            return
        base_version_id = report.get("base_version_id")
        selected = self._version_with_json(project, base_version_id)
        if not selected or not isinstance(selected[1], dict):
            self.statusBar().showMessage(tr('报告绑定版本无法读取。'), 4000)
            return
        version, previous_json = selected
        from inspection.engine import hash_ladder_json

        if report.get("base_json_hash") and (
            hash_ladder_json(previous_json) != report.get("base_json_hash")
        ):
            self.statusBar().showMessage(
                tr('版本内容已变化，请重新评审或调试后再修复。'), 5000
            )
            return
        allowed_rungs = set()
        allowed_addresses = set()
        allowed_paths = set()
        for finding in findings:
            for rung in finding.get("rung_ids", []) or []:
                try:
                    allowed_rungs.add(int(rung))
                except (TypeError, ValueError):
                    pass
            address = str(finding.get("address", "")).strip().upper()
            if address:
                allowed_addresses.add(address)
            for address in finding.get("addresses", []) or []:
                address = str(address).strip().upper()
                if address:
                    allowed_addresses.add(address)
            for path in finding.get("json_paths", []) or []:
                path = str(path).strip()
                if path:
                    allowed_paths.add(path)
            for evidence in finding.get("evidence", []) or []:
                if not isinstance(evidence, dict):
                    continue
                try:
                    if evidence.get("rung_id") is not None:
                        allowed_rungs.add(int(evidence["rung_id"]))
                except (TypeError, ValueError):
                    pass
                address = str(evidence.get("address", "")).strip().upper()
                if address:
                    allowed_addresses.add(address)
                path = str(evidence.get("json_path", "")).strip()
                if path:
                    allowed_paths.add(path)
        if not allowed_rungs:
            self.statusBar().showMessage(
                tr('所选问题缺少可验证的梯级证据，不能自动修复。'), 5000
            )
            return
        summary_lines = [
            "- "
            + naturalize_display_text(
                item.get("title")
                or item.get("message")
                or naturalize_identifier(
                    item.get("finding_id"),
                    kind=tr('问题'),
                    index=index,
                )
            )
            for index, item in enumerate(findings, start=1)
        ]
        boundary = (
            tr('\n允许影响的梯级：')
            + ", ".join(map(str, sorted(allowed_rungs)))
            + tr('\n允许影响的地址：')
            + (", ".join(sorted(allowed_addresses)) or tr('无附加地址'))
            + tr('\n精确定位：')
            + (
                tr('已绑定 {v0} 处程序位置', v0=len(allowed_paths))
                if allowed_paths
                else tr('仅限上述梯级')
            )
            + tr('\n边界：必须返回增量 JSON，不得完整重写，也不得修改未勾选问题。')
        )
        answer = QMessageBox.question(
            self,
            tr('确认生成修复版本'),
            (
                tr('基础版本：{v0}\n将修复 {v1} 项并创建新版本，不覆盖原版本：\n', v0=version_display_name(base_version_id), v1=len(findings))
                + "\n".join(summary_lines)
                + boundary
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self._ensure_api_configured():
            self.statusBar().showMessage(
                tr('生成修复版本需要先配置 API；本地报告仍可查看。'), 5000
            )
            return

        version_id, output_dir = self.store.prepare_version(project_id)
        attempt_id = f"attempt_{uuid.uuid4().hex[:12]}"
        attempts = report.setdefault("fix_history", [])
        attempts.append(
            {
                "attempt_id": attempt_id,
                "selected_finding_ids": selected_ids,
                "status": "running",
                "output_version_id": version_id,
            }
        )
        self.store.update_report(project_id, report_id, report)
        confirmed_spec = self._version_confirmed_spec(version)
        plc_model = (
            version.get("plc_model")
            or report.get("plc_model")
            or project.get("plc_model", "FX3U")
        )
        repair_payload = {
            "base_version_id": base_version_id,
            "selected_findings": findings,
            "allowed_rung_ids": sorted(allowed_rungs),
            "allowed_addresses": sorted(allowed_addresses),
        }
        request = (
            tr('目标 PLC 型号：{v0}\n请根据以下已确认问题生成严格增量修复。只返回 mode=partial 的梯形图 JSON；不得改动未列入 allowed_rung_ids 的梯级，也不得顺带修复未勾选的问题。\n', v0=plc_model)
            + json.dumps(repair_payload, ensure_ascii=False, indent=2)
        )
        task_id = f"{project_id}:{version_id}"
        self.active_task = {
            "id": task_id,
            "project_id": project_id,
            "version_id": version_id,
            "phase": "compile",
            "summary": tr('诊断修复：{v0}', v0=report.get('summary', '')),
            "parent_version_id": base_version_id,
            "source_report_id": report_id,
            "selected_finding_ids": selected_ids,
            "repair_attempt_id": attempt_id,
            "confirmed_spec_snapshot": confirmed_spec,
            "plc_model": plc_model,
        }
        self.store.add_message(
            project_id,
            "assistant",
            tr('已确认 {v0} 项问题，正在基于 {v1} 生成新的修复版本（{v2}）。', v0=len(findings), v1=version_display_name(base_version_id), v2=version_display_name(version_id)),
            kind="system",
            metadata={
                "workflow_mode": "inspection_repair",
                "version_id": version_id,
                "report_id": report_id,
            },
        )
        self._set_busy(True, tr('正在生成诊断修复版本'))
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('正在生成严格增量修复'))
        thread = CompilerThread(
            task_id,
            request,
            None,
            "ladder",
            output_dir,
            previous_json=previous_json,
            previous_ir=self.store.load_program_ir(project_id, base_version_id),
            conversation_history=[],
            confirmed_context=confirmed_spec,
            task_type="repair",
            current_version_json=previous_json,
            plc_model=plc_model,
            repair_mode=True,
            allowed_rung_ids=allowed_rungs,
            allowed_addresses=allowed_addresses,
            program_name="MAIN",
            revision=int(str(version_id).lstrip("vV") or "1"),
            requirement_text=request,
        )
        self._retain_worker_thread("_compiler_thread", thread)
        thread.thinking_updated.connect(self._append_reasoning)
        thread.content_updated.connect(self._append_content)
        thread.progress_updated.connect(self._progress_updated)
        thread.success.connect(self._compile_success)
        thread.failure.connect(self._compile_failure)
        thread.start()
        self._render_conversation(self.store.get_project(project_id))

    def _confirm_review(self, project_id, spec):
        if self.active_task:
            self.statusBar().showMessage(tr('当前已有任务运行。'), 3000)
            return
        if not self._ensure_api_configured():
            self.statusBar().showMessage(tr('需要先完成 API 配置。'), 4000)
            return
        project = self.store.get_project(project_id)
        pending = project.get("pending_review")
        if not pending:
            return
        image_records = pending.get("image_attachments") or []
        if image_records and not self._model_supports_images():
            profile = self._active_profile()
            QMessageBox.warning(
                self,
                tr('当前模型不支持图片'),
                tr('{v0} 不能继续处理本次图片需求。\n\n请切换回 deepseek-v4-flash-vision-exp 或 glm-5.3-flash。', v0=profile.get('model') or tr('当前模型')),
            )
            return
        try:
            model_images = self._model_images_from_records(
                project_id,
                image_records,
            )
        except Exception as error:
            QMessageBox.warning(
                self,
                tr('图片附件不可用'),
                naturalize_display_text(error),
            )
            return
        spec = canonicalize_confirmed_spec(spec)
        self.store.set_confirmed_spec(project_id, spec)
        self.store.set_pending_review(project_id, None)
        self.store.add_message(
            project_id,
            "assistant",
            tr('确认规格已锁定，开始生成并执行硬校验。'),
            kind="system",
        )
        project = self.store.get_project(project_id)
        target_mode = project.get("target_mode", "ladder")
        effort = None
        workflow_mode = project.get("workflow_mode", "generate")
        base_version = (
            self._latest_ladder_version(project)
            if target_mode == "ladder"
            else None
        )
        previous_json = base_version[1] if base_version else None
        parent_version_id = (
            base_version[0]["id"]
            if base_version
            else project.get("active_version_id")
        )
        plc_model = project.get("plc_model", "FX3U")
        version_id, output_dir = self.store.prepare_version(project_id)
        task_id = f"{project_id}:{version_id}"
        self.active_task = {
            "id": task_id,
            "project_id": project_id,
            "version_id": version_id,
            "phase": "compile",
            "summary": spec.get("summary", pending["request"][:80]),
            "parent_version_id": parent_version_id,
            "confirmed_spec_snapshot": spec,
            "confirmed_spec_hash": self._json_sha256(spec),
            "plc_model": plc_model,
            "image_attachments": copy.deepcopy(image_records),
        }
        self._set_busy(True, tr('正在生成程序'))
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('准备生成'))
        thread = CompilerThread(
            task_id,
            tr('目标 PLC 型号：{v0}\n{v1}', v0=plc_model, v1=pending['request']),
            effort,
            target_mode,
            output_dir,
            previous_json=previous_json,
            previous_ir=(
                self.store.load_program_ir(project_id, base_version[0]["id"])
                if base_version
                else None
            ),
            conversation_history=[],
            confirmed_context=spec,
            task_type=workflow_mode,
            current_version_json=previous_json,
            plc_model=plc_model,
            program_name="MAIN",
            revision=int(str(version_id).lstrip("vV") or "1"),
            requirement_text=pending["request"],
            image_attachments=model_images,
        )
        self._retain_worker_thread("_compiler_thread", thread)
        thread.thinking_updated.connect(self._append_reasoning)
        thread.content_updated.connect(self._append_content)
        thread.progress_updated.connect(self._progress_updated)
        thread.success.connect(self._compile_success)
        thread.failure.connect(self._compile_failure)
        thread.start()
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _revise_review(self, project_id, text):
        project = self.store.get_project(project_id) or {}
        records = (project.get("pending_review") or {}).get(
            "image_attachments"
        ) or []
        self.store.set_pending_review(project_id, None)
        if self.current_project_id == project_id:
            self._restore_composer_images(project_id, records)
            self.composer_edit.setPlainText(text)
            self.composer_edit.setFocus()
            self._render_conversation(self.store.get_project(project_id))

    def _revise_review_with_draft(self, project_id, text, draft):
        """Return to editing without losing the user's v3 specification draft."""
        project = self.store.get_project(project_id)
        if not project:
            return
        records = (project.get("pending_review") or {}).get(
            "image_attachments"
        ) or []
        project["pending_review"] = None
        project["preserved_spec_draft"] = copy.deepcopy(draft or {})
        self.store.save_project(project)
        if self.current_project_id == project_id:
            self._restore_composer_images(project_id, records)
            self.composer_edit.setPlainText(text)
            self.composer_edit.setFocus()
            self._render_conversation(self.store.get_project(project_id))

    def _latest_ladder_json(self, project):
        latest = self._latest_ladder_version(project)
        return latest[1] if latest else None

    def _version_with_json(self, project, version_id):
        if not project or not version_id:
            return None
        version = self.store.get_version(project["id"], version_id)
        if not version:
            return None
        if version.get("target_mode") != "ladder":
            return version, None
        ladder = self.store.load_ladder(project["id"], version["id"])
        return (version, ladder) if ladder is not None else None

    def _latest_ladder_version(self, project):
        for version in reversed(project.get("versions", [])):
            if version.get("target_mode") != "ladder":
                continue
            ladder = self.store.load_ladder(project["id"], version["id"])
            if ladder is not None:
                return version, ladder
        return None

    @staticmethod
    def _build_confirmed_context(spec, project):
        parts = [tr('目标 PLC: {v0}', v0=project.get('plc_model', 'FX3U'))]
        if spec.get("summary"):
            parts.append(tr('确认后的需求摘要: {v0}', v0=spec['summary']))
        approach = spec.get("selected_approach") or {}
        if approach:
            parts.append(
                tr('方案: {v0}——{v1}', v0=approach.get('name', ''), v1=approach.get('description', ''))
            )
            if approach.get("generation_guide"):
                parts.append(tr('方案生成要点: {v0}', v0=approach['generation_guide']))
        for parameter in spec.get("parameters", []) or []:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("name", "")).strip()
            value = str(parameter.get("value", "")).strip()
            if name and value:
                parts.append(f"{name}: {value}")
        for question, answer in spec.get("missing_answers", {}).items():
            parts.append(f"{question}: {answer}")
        if spec.get("user_notes"):
            parts.append(tr('用户补充: {v0}', v0=spec['user_notes']))
        if spec.get("io_allocation_raw"):
            parts.append(
                '【软元件分配——整个程序必须一致使用】\n'
                + spec["io_allocation_raw"]
            )
        return "\n".join(parts)

    def _compile_success(self, task_id, result):
        task = self.active_task
        if not task or task["id"] != task_id:
            return
        result = dict(result)
        contract_mismatch = result.get("contract_mismatch")
        basic_report = result.pop("inspection_report", None)
        metadata = {
            **result,
            "summary": task.get("summary", ""),
            "plc_model": task.get("plc_model"),
            "confirmed_spec_snapshot": task.get("confirmed_spec_snapshot"),
            "confirmed_spec_hash": task.get("confirmed_spec_hash")
            or (
                self._json_sha256(task["confirmed_spec_snapshot"])
                if task.get("confirmed_spec_snapshot")
                else None
            ),
            "parent_version_id": task.get("parent_version_id"),
            "source_report_id": task.get("source_report_id"),
            "selected_finding_ids": task.get("selected_finding_ids", []),
            "contract_repair_plan": task.get("contract_repair_plan"),
        }
        if isinstance(basic_report, dict):
            basic_report = copy.deepcopy(basic_report)
            basic_report["base_version_id"] = task["version_id"]
            basic_report["plc_model"] = task.get("plc_model") or basic_report.get(
                "plc_model", "FX3U"
            )
            basic_report.setdefault("base", {})["version_id"] = task["version_id"]
            basic_report["base"]["plc_model"] = task.get("plc_model")
            created = self.store.create_report(task["project_id"], basic_report)
            metadata["review_report_id"] = created.get("report_id")
        self.store.complete_version(
            task["project_id"], task["version_id"], metadata
        )
        self.store.add_message(
            task["project_id"],
            "assistant",
            (
                tr('程序和 CSV 已生成。版本：{v0}。方案约束尚未满足；可以先导出或写入 GX Works2 检查，再点击“修复方案约束”决定是否修复。', v0=task['version_id'])
                if contract_mismatch
                else tr('程序已生成并通过校验。版本：{v0}', v0=task['version_id'])
            ),
            kind="generation",
            metadata={"version_id": task["version_id"]},
        )
        if metadata.get("review_report_id"):
            self.store.add_message(
                task["project_id"],
                "assistant",
                tr('自动基础评审已完成；建议项不会阻止版本保存。'),
                kind="inspection_report",
                metadata={
                    "report_id": metadata["review_report_id"],
                    "base_version_id": task["version_id"],
                    "report_type": "program_review",
                },
            )
        if task.get("source_report_id"):
            report = self.store.get_report(
                task["project_id"], task["source_report_id"]
            ) or {}
            for attempt in report.get("fix_history", []) or []:
                if attempt.get("attempt_id") == task.get("repair_attempt_id"):
                    attempt["status"] = "succeeded"
                    attempt["output_version_id"] = task["version_id"]
            selected_ids = set(task.get("selected_finding_ids", []))
            new_ids = {
                item.get("finding_id") or item.get("id")
                for item in (basic_report or {}).get("findings", [])
                if isinstance(item, dict)
            }
            for finding in report.get("findings", []) or []:
                finding_id = finding.get("finding_id") or finding.get("id")
                if finding_id not in selected_ids:
                    continue
                origins = set(
                    finding.get("origins")
                    or [finding.get("source", "")]
                )
                if finding_id in new_ids:
                    finding["resolution_status"] = "still_present"
                elif "local" in origins:
                    finding["resolution_status"] = "resolved"
                else:
                    finding["resolution_status"] = "needs_review"
            self.store.update_report(
                task["project_id"], task["source_report_id"], report
            )
        project_id = task["project_id"]
        version_id = task["version_id"]
        self._stop_repair_status_timer()
        self.active_task = None
        completion_status = (
            tr('CSV 已生成 · 方案约束待处理')
            if contract_mismatch
            else tr('生成完成')
        )
        self._set_busy(False, completion_status)
        self.activity_panel.set_status(completion_status)
        self.statusBar().showMessage(
            (
                tr('{v0}原始 CSV 已保存；可先导入 GX Works2，再决定是否修复', v0=version_display_name(version_id))
                if contract_mismatch
                else tr('{v0}已生成并保存', v0=version_display_name(version_id))
            ),
            7000 if contract_mismatch else 5000,
        )
        self._refresh_projects(self.current_project_id)
        if self.current_project_id == project_id:
            self._load_project(project_id)
            self._select_version(version_id)

    def _compile_failure(self, task_id, error):
        task = self.active_task
        if not task or task["id"] != task_id:
            return
        self.store.discard_version(task["project_id"], task["version_id"])
        if task.get("source_report_id"):
            report = self.store.get_report(
                task["project_id"], task["source_report_id"]
            ) or {}
            for attempt in report.get("fix_history", []) or []:
                if attempt.get("attempt_id") == task.get("repair_attempt_id"):
                    attempt["status"] = "failed"
                    attempt["error"] = str(error)
            self.store.update_report(
                task["project_id"], task["source_report_id"], report
            )
        self.store.add_message(
            task["project_id"],
            "assistant",
            tr('生成失败：{v0}\n可修改需求后重新发送。', v0=naturalize_display_text(error)),
            kind="system",
        )
        project_id = task["project_id"]
        if task.get("image_attachments"):
            self._restore_composer_images(
                project_id,
                task.get("image_attachments") or [],
            )
        self._stop_repair_status_timer()
        self.active_task = None
        self._set_busy(False, tr('生成失败'))
        self.activity_panel.show_error(naturalize_display_text(error))
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _append_reasoning(self, task_id, token):
        if self.active_task and self.active_task["id"] == task_id:
            rendered = self._activity_stream_chunk(task_id, "reasoning", token)
            if rendered:
                self.activity_panel.append_reasoning(rendered)

    def _append_content(self, task_id, token):
        if self.active_task and self.active_task["id"] == task_id:
            rendered = self._activity_stream_chunk(task_id, "content", token)
            if rendered:
                self.activity_panel.append_content(rendered)

    def _activity_stream_chunk(self, task_id, channel, token):
        # Acceptance owns model language. Rendering must not change JSON keys,
        # PLC identifiers or quoted evidence, nor re-read a newer UI language.
        return str(token or "")

    def _flush_activity_display_streams(self):
        if hasattr(self, "activity_panel"):
            self.activity_panel.flush_display()

    def _progress_updated(self, task_id, payload):
        if not self.active_task or self.active_task["id"] != task_id:
            return
        self._flush_activity_display_streams()
        stage = payload.get("stage", "")
        if stage == "repairing_remote":
            if not self._repair_status_timer.isActive():
                self._repair_wait_seconds = 0
                self._repair_status_timer.start()
        elif stage == "repaired_local":
            self._stop_repair_status_timer()
        message = naturalize_display_text(payload.get("message", ""))
        self.activity_panel.set_status(message)
        if message:
            self.activity_panel.append_content(f"\n[{message}]\n")
        self.conversation_status.setText(message)

    def _update_repair_wait_status(self):
        if not self.active_task or self.active_task.get("phase") != "compile":
            self._stop_repair_status_timer()
            return
        self._repair_wait_seconds += 1
        message = (
            tr('AI 自动修复中 {v0} 秒 / 最长 120 秒', v0=self._repair_wait_seconds)
        )
        self.activity_panel.set_status(message)
        self.conversation_status.setText(message)
        self.status_runtime.setText(tr('状态: {v0}', v0=message))

    def _stop_repair_status_timer(self):
        self._repair_status_timer.stop()
        self._repair_wait_seconds = 0

    def _set_busy(self, busy, status):
        status = naturalize_display_text(status)
        if not busy:
            self._flush_activity_display_streams()
        project_enabled = self.current_project_id is not None
        self.send_button.setEnabled(not busy and project_enabled)
        self.model_combo.setEnabled(not busy and project_enabled)
        self.target_combo.setEnabled(not busy and project_enabled)
        self.workflow_combo.setEnabled(not busy and project_enabled)
        self.sfc_button.setEnabled(not busy and project_enabled)
        self.image_attachment_button.setEnabled(not busy and project_enabled)
        self.settings_button.setEnabled(not busy)
        self.conversation_status.setText(status)
        set_codicon(
            self.send_button,
            "sync" if busy else "sparkle",
            tr('任务运行中') if busy else tr('分析需求'),
            10,
        )
        self.status_runtime.setText(tr('状态: {v0}', v0=status))
        self._update_workflow_ui()

    def _open_sfc_workspace(self):
        dialog = SFCWorkspaceDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            current = self.composer_edit.toPlainText().strip()
            text = dialog.generated_text
            self.composer_edit.setPlainText(
                f"{current}\n\n{text}".strip() if current else text
            )
            self.composer_edit.setFocus()

    def _open_api_settings(self):
        if self.active_task or self._active_worker_threads:
            self.statusBar().showMessage(tr('请等待当前任务结束后再修改设置。'), 4000)
            return
        self._show_api_settings(initial_setup=False)

    def _language_changed(self, _language):
        if not hasattr(self, "activity_panel"):
            return
        self._activity_display_streams = {}
        self.activity_panel.content_edit.clear()
        self.activity_panel._language_guards = {}
        self.activity_panel.set_status(tr('等待中'))
        self._update_workflow_ui()
        self._update_titlebar_density()

    @staticmethod
    def _api_key_available():
        try:
            config = load_full_config()
            return bool(get_api_key(config))
        except Exception:
            return False

    def _ensure_api_configured(self, initial_setup=False):
        if self._api_key_available():
            return True
        return self._show_api_settings(
            initial_setup=initial_setup,
            require_key=True,
        )

    def _show_api_settings(self, initial_setup=False, require_key=False):
        from ui.desktop.dialogs.config import RequestTemplateConfigDialog

        dialog = RequestTemplateConfigDialog(
            self,
            initial_setup=initial_setup,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if not dialog.api_key_configured:
                reset_model_provider()
                self._update_workflow_ui()
                self.statusBar().showMessage(tr('设置已更新；尚未配置 API Key。'), 4000)
                return not require_key
            try:
                reload_model_provider()
                self._update_workflow_ui()
                self.statusBar().showMessage(tr('设置已更新'), 4000)
                return True
            except Exception as error:
                QMessageBox.critical(
                    self,
                    tr('API 配置错误'),
                    tr('重新加载失败：\n{v0}', v0=naturalize_display_text(error)),
                )
                return False
        return False

    def _repair_current_contract_mismatch(self):
        if self.active_task:
            self.statusBar().showMessage(tr('当前已有任务运行。'), 3000)
            return
        if not self.current_project_id or not self.current_version_id:
            return

        project_id = self.current_project_id
        version_id = self.current_version_id
        project = self.store.get_project(project_id)
        version = self.store.get_version(project_id, version_id)
        if not project or not version:
            return
        mismatch = version.get("contract_mismatch") or {}
        if not mismatch:
            self.statusBar().showMessage(tr('当前版本没有待修复的方案约束。'), 4000)
            return

        selected = self._version_with_json(project, version_id)
        if not selected or not isinstance(selected[1], dict):
            self.statusBar().showMessage(tr('当前版本没有可修复的梯形图 JSON。'), 4000)
            return
        _version, previous_json = selected
        confirmed_spec = self._version_confirmed_spec(version)
        if not confirmed_spec:
            QMessageBox.warning(
                self,
                tr('无法修复'),
                tr('当前版本缺少已确认规格快照，不能自动修改实现方案。'),
            )
            return

        plc_model = (
            version.get("plc_model")
            or project.get("plc_model")
            or "FX3U"
        )
        try:
            plan = build_contract_repair_plan(
                previous_json,
                confirmed_spec,
                plc_model=plc_model,
                mismatch=mismatch,
            )
        except Exception as error:
            QMessageBox.warning(
                self,
                tr('无法建立修复计划'),
                naturalize_display_text(error),
            )
            return

        if plan.get("repairability") == "not_needed":
            QMessageBox.information(
                self,
                tr('无需修复'),
                tr('重新检查后当前程序已经满足 generation_contract。'),
            )
            return

        if plan.get("repairability") != "scoped_patch":
            details = "\n".join(
                f"• {item.get('kind')}: {item.get('value')}"
                for item in (plan.get("violations") or [])
            )
            QMessageBox.warning(
                self,
                tr('无法安全自动修复'),
                (
                    tr('{v0}\n\n{v1}\n\n系统不会再让 AI 猜测 MOV/SET/RST 等指令应该放在哪里。\n请在已确认方案中补充目标软元件、状态寄存器或具体实现语义后再生成。', v0=plan.get('reason', tr('当前约束缺少可定位的语义上下文')), v1=details)
                ),
            )
            return

        csv_name = (version.get("artifacts") or {}).get(
            "program_csv", "program.csv"
        )
        answer = QMessageBox.question(
            self,
            tr('确认受限方案约束修复'),
            (
                tr('基础版本：{v0}\n原始 CSV 已保留：{v1}\n\n这次不会重新生成整份程序，只允许修改计划中的既有梯级，并禁止引入计划外软元件。\n\n{v2}\n\n确认后创建一个新的修复版本；原始 CSV 不会覆盖。', v0=version_display_name(version_id), v1=csv_name, v2=format_contract_repair_plan(plan))
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self._ensure_api_configured():
            self.statusBar().showMessage(tr('修复需要先完成 API 配置。'), 4000)
            return

        new_version_id, output_dir = self.store.prepare_version(project_id)
        audit_plan = {
            key: copy.deepcopy(plan.get(key))
            for key in (
                "plan_id",
                "repairability",
                "approach_name",
                "violations",
                "allowed_rung_ids",
                "allowed_addresses",
                "scope_reasons",
                "fallback_scope",
            )
        }
        task_id = f"{project_id}:{new_version_id}"
        self.active_task = {
            "id": task_id,
            "project_id": project_id,
            "version_id": new_version_id,
            "phase": "compile",
            "summary": tr('方案约束受限修复：{v0}', v0=plan.get('plan_id')),
            "parent_version_id": version_id,
            "confirmed_spec_snapshot": copy.deepcopy(confirmed_spec),
            "confirmed_spec_hash": self._json_sha256(confirmed_spec),
            "plc_model": plc_model,
            "contract_repair_plan": audit_plan,
        }
        self.store.add_message(
            project_id,
            "assistant",
            (
                tr('已确认基于{v0}执行受限方案约束修复；允许修改梯级 {v1}。原始版本和 CSV 保持不变。', v0=version_display_name(version_id), v1=', '.join(map(str, plan['allowed_rung_ids'])))
            ),
            kind="system",
        )
        self._set_busy(True, tr('正在执行受限方案约束修复'))
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('AI 正在生成受限 partial patch'))

        thread = CompilerThread(
            task_id,
            plan["prompt"],
            "high",
            "ladder",
            output_dir,
            previous_json=previous_json,
            previous_ir=self.store.load_program_ir(project_id, version_id),
            conversation_history=[],
            confirmed_context=confirmed_spec,
            task_type="contract_repair",
            current_version_json=previous_json,
            plc_model=plc_model,
            program_name=version.get("program_name") or "MAIN",
            revision=int(str(new_version_id).lstrip("vV") or "1"),
            requirement_text=str(confirmed_spec.get("summary") or ""),
            repair_mode=True,
            allowed_rung_ids=plan["allowed_rung_ids"],
            allowed_addresses=plan["allowed_addresses"],
        )
        self._retain_worker_thread("_compiler_thread", thread)
        thread.thinking_updated.connect(self._append_reasoning)
        thread.content_updated.connect(self._append_content)
        thread.progress_updated.connect(self._progress_updated)
        thread.success.connect(self._compile_success)
        thread.failure.connect(self._compile_failure)
        thread.start()
        if self.current_project_id == project_id:
            self._render_conversation(self.store.get_project(project_id))

    def _export_current_version(self):
        if not self.current_project_id or not self.current_version_id:
            return
        version = self.store.get_version(
            self.current_project_id, self.current_version_id
        )
        version_dir = self.store.version_dir(
            self.current_project_id, self.current_version_id
        )
        artifacts = version.get("artifacts", {})
        if version.get("target_mode") == "ladder":
            source = version_dir / artifacts.get("program_csv", "")
            if not source.exists():
                QMessageBox.critical(self, tr('导出失败'), tr('当前版本缺少程序 CSV。'))
                return
            destination, _ = QFileDialog.getSaveFileName(
                self,
                tr('导出 GX Works2 程序'),
                f"{self.current_version_id}_program.csv",
                "CSV Files (*.csv)",
                options=QFileDialog.Option.DontUseNativeDialog,
            )
            if not destination:
                return
            shutil.copy2(source, destination)
            comment_source = version_dir / artifacts.get("comment_csv", "")
            if comment_source.exists():
                target = Path(destination)
                shutil.copy2(
                    comment_source,
                    target.with_name(tr('{v0}_注释{v1}', v0=target.stem, v1=target.suffix)),
                )
        else:
            source = version_dir / artifacts.get("st", "")
            if not source.exists():
                QMessageBox.critical(self, tr('导出失败'), tr('当前版本缺少 ST 文件。'))
                return
            destination, _ = QFileDialog.getSaveFileName(
                self,
                tr('导出 ST 程序'),
                f"{version_display_name(self.current_version_id).replace(' ', '')}.st",
                "ST Files (*.st);;Text Files (*.txt)",
                options=QFileDialog.Option.DontUseNativeDialog,
            )
            if not destination:
                return
            shutil.copy2(source, destination)
        self.statusBar().showMessage(tr('已导出到 {v0}', v0=destination), 6000)

    def _open_gxw_structured_reader(self):
        source, _ = QFileDialog.getOpenFileName(
            self,
            tr('选择 GX Works2 Structured Ladder/FBD 工程'),
            "",
            "GX Works2 Project (*.gxw);;All Files (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not source:
            return

        try:
            from gxw.decoder import describe_program
            from gxw.models import GXWFormatError
            from gxw.project_resolver import GXWProjectResolver
            from gxw.structured_pou import parse_structured_pou

            resolver = GXWProjectResolver.from_file(source)
            candidates = resolver.program_pou_names()
            if not candidates:
                raise GXWFormatError(tr('工程中没有找到 *.Program.pou。'))

            selected = candidates[0]
            if len(candidates) > 1:
                selected, accepted = QInputDialog.getItem(
                    self,
                    tr('选择程序体'),
                    tr('检测到多个 Program.pou，请选择要解析的程序体：'),
                    candidates,
                    0,
                    False,
                )
                if not accepted or not selected:
                    return

            program = parse_structured_pou(
                resolver.read_logical_file(selected),
                logical_name=selected,
                source_path=Path(source),
            )
            report_lines = describe_program(program)
        except Exception as exc:
            QMessageBox.critical(
                self,
                tr('GXW 解析失败'),
                tr('当前入口只针对已验证的 Structured Ladder/FBD GXW 结构。\n\n')
                + naturalize_display_text(str(exc)),
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(tr('GXW 结构化梯形图解析（只读）'))
        dialog.resize(900, 640)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        title = QLabel(
            tr('{v0}  ·  {v1}  ·  {v2} 节点  ·  {v3} 连线', v0=Path(source).name, v1=selected, v2=len(program.nodes), v3=len(program.wires))
        )
        title.setObjectName("CanvasTitle")
        layout.addWidget(title)

        note = QLabel(
            tr('实验性只读解析：不会修改GXW文件。未知结构会保留或报错，不会猜测。')
        )
        note.setWordWrap(True)
        note.setObjectName("HelperText")
        layout.addWidget(note)

        output = QPlainTextEdit(dialog)
        output.setReadOnly(True)
        output.setPlainText("\n".join(report_lines))
        layout.addWidget(output, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close_button = QPushButton(tr('关闭'))
        close_button.clicked.connect(dialog.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        dialog.exec()
        self.statusBar().showMessage(
            tr('已只读解析 {v0}：{v1} 个节点，{v2} 条连线', v0=Path(source).name, v1=len(program.nodes), v2=len(program.wires)),
            6000,
        )

    def _set_gx_sync_status(self, status, detail=""):
        labels = {
            "unknown": tr('GX：未检查'),
            "checking": tr('GX：检查中'),
            "synced": tr('GX：已同步'),
            "project_changed": tr('GX：项目有修改'),
            "gx_changed": tr('GX：GX有修改'),
            "conflict": tr('GX：双方冲突'),
            "unsaved": tr('GX：工程未保存'),
            "pulling": tr('GX：正在回读'),
            "pushing": tr('GX：正在写入'),
            "error": tr('GX：同步异常'),
        }
        if hasattr(self, "gxworks2_sync_status"):
            self.gxworks2_sync_status.setText(labels.get(status, labels["unknown"]))
            self.gxworks2_sync_status.setToolTip(
                naturalize_display_text(detail)
                if detail
                else tr('当前项目版本与GX Works2的同步状态')
            )

    def _gx_sync_busy(self):
        return any(
            getattr(self, name, None) is not None
            for name in (
                "_gxworks2_sync_thread",
                "_gxworks2_pull_thread",
                "_gxworks2_import_thread",
            )
        )

    def _gx_action_buttons(self):
        return tuple(
            button
            for button in (
                getattr(self, "gxworks2_import_button", None),
                getattr(self, "gxworks2_pull_button", None),
                getattr(self, "gxworks2_advanced_button", None),
            )
            if button is not None
        )

    def _set_gx_action_buttons_enabled(self, enabled):
        for button in self._gx_action_buttons():
            button.setEnabled(bool(enabled))

    def _reset_gx_action_buttons(self):
        if hasattr(self, "gxworks2_import_button"):
            set_codicon(
                self.gxworks2_import_button,
                "export",
                tr('写入 GX Works2'),
                10,
            )
        if hasattr(self, "gxworks2_pull_button"):
            set_codicon(
                self.gxworks2_pull_button,
                "sync",
                tr('读取 GX Works2'),
                10,
            )
        if hasattr(self, "gxworks2_advanced_button"):
            self.gxworks2_advanced_button.setText(tr('高级同步'))

    def _update_gx_sync_button_enabled(self):
        if not hasattr(self, "gxworks2_import_button"):
            return
        project_ready = bool(self.current_project_id)
        version = (
            self.store.get_version(self.current_project_id, self.current_version_id)
            if self.current_project_id and self.current_version_id
            else None
        )
        ladder_ready = bool(version and version.get("target_mode") == "ladder")
        available = not self._gx_sync_busy()
        self.gxworks2_import_button.setEnabled(ladder_ready and available)
        self.gxworks2_pull_button.setEnabled(project_ready and available)
        self.gxworks2_advanced_button.setEnabled(ladder_ready and available)

    def _gx_sync_request_for_version(self, project_id=None, version_id=None):
        project_id = project_id or self.current_project_id
        version_id = version_id or self.current_version_id
        if not project_id or not version_id:
            raise ValueError(tr('请先选择一个已生成的梯形图版本。'))
        version = self.store.get_version(project_id, version_id)
        if not version or version.get("target_mode") != "ladder":
            raise ValueError(tr('只有梯形图版本可以与GX Works2同步。'))
        version_dir = self.store.version_dir(project_id, version_id)
        artifacts = version.get("artifacts", {}) or {}
        program_path = version_dir / str(artifacts.get("program_csv") or "")
        comment_path = version_dir / str(artifacts.get("comment_csv") or "")
        if not program_path.is_file():
            raise ValueError(tr('当前版本缺少程序CSV。'))
        if not comment_path.is_file():
            raise ValueError(tr('当前版本缺少软元件注释CSV。'))
        context = {
            "project_id": project_id,
            "version_id": version_id,
            "revision": version.get("revision"),
            "program_name": version.get("program_name") or "MAIN",
            "ir_schema_version": version.get("ir_schema_version"),
            "ir_sha256": version.get("ir_sha256"),
            "ladder_sha256": version.get("ladder_sha256"),
        }
        return {
            "project_id": project_id,
            "version_id": version_id,
            "version": version,
            "program_path": program_path,
            "comment_path": comment_path,
            "context": context,
        }

    def _gx_pull_request(self):
        project_id = self.current_project_id
        if not project_id:
            raise ValueError(tr('请先选择一个项目。'))
        project = self.store.get_project(project_id)
        if not project:
            raise ValueError(tr('当前项目不存在。'))
        version = (
            self.store.get_version(project_id, self.current_version_id)
            if self.current_version_id
            else None
        )
        if version and version.get("target_mode") == "ladder":
            request = self._gx_sync_request_for_version(
                project_id=project_id,
                version_id=self.current_version_id,
            )
            request["bootstrap"] = False
            return request
        return {
            "project_id": project_id,
            "version_id": None,
            "version": None,
            "program_path": None,
            "comment_path": None,
            "context": {
                "project_id": project_id,
                "version_id": None,
                "revision": None,
                "program_name": "MAIN",
                "ir_schema_version": None,
                "ir_sha256": None,
                "ladder_sha256": None,
            },
            "bootstrap": True,
        }

    def _publish_current_version_to_gxworks2(self):
        if self._gx_sync_busy():
            self.statusBar().showMessage(tr('GX Works2操作正在运行。'), 3000)
            return
        try:
            request = self._gx_sync_request_for_version()
        except Exception as error:
            QMessageBox.warning(self, tr('无法写入'), naturalize_display_text(error))
            return
        self._gx_sync_intent = "publish"
        self._import_current_version_to_gxworks2(
            project_id=request["project_id"],
            version_id=request["version_id"],
        )

    def _pull_current_version_from_gxworks2(self):
        self._start_gxworks2_inspection("pull")

    def _sync_current_version_with_gxworks2(self):
        self._start_gxworks2_inspection("reconcile")

    def _start_gxworks2_inspection(self, intent):
        if self._gx_sync_busy():
            self.statusBar().showMessage(tr('GX Works2操作正在运行。'), 3000)
            return
        try:
            request = (
                self._gx_pull_request()
                if intent == "pull"
                else self._gx_sync_request_for_version()
            )
        except Exception as error:
            title = tr('无法读取') if intent == "pull" else tr('无法高级同步')
            QMessageBox.warning(self, title, naturalize_display_text(error))
            return
        self._gx_sync_intent = str(intent or "reconcile")
        self._pending_gx_sync_result = None
        self._gx_sync_request = request
        detail = (
            tr('正在读取GX Works2当前MAIN和软元件注释')
            if self._gx_sync_intent == "pull"
            else tr('正在比较项目与GX Works2')
        )
        self._set_gx_sync_status("checking", detail)
        self._set_gx_action_buttons_enabled(False)
        active_button = (
            self.gxworks2_pull_button
            if self._gx_sync_intent == "pull"
            else self.gxworks2_advanced_button
        )
        active_button.setText(tr('正在读取…') if self._gx_sync_intent == "pull" else tr('正在检查…'))
        self.statusBar().showMessage(tr('正在读取GX Works2当前MAIN和软元件注释…'))
        thread = GXWorks2SyncInspectThread(
            request.get("program_path"),
            request.get("comment_path"),
            import_context=request["context"],
            snapshot_only=bool(request.get("bootstrap")),
        )
        thread.progress_changed.connect(self._gxworks2_sync_progress)
        thread.completed.connect(self._gxworks2_sync_inspected)
        self._retain_worker_thread(
            "_gxworks2_sync_thread",
            thread,
            on_finished=self._gxworks2_sync_thread_finished,
        )
        thread.start()

    def _gxworks2_sync_progress(self, stage, message):
        labels = {
            "validate": tr('正在校验…'),
            "validate_local": tr('正在校验…'),
            "check_gxworks2": tr('检查GX进程…'),
            "check_project": tr('检查GX工程…'),
            "check_program": tr('检查MAIN…'),
            "inspect_project": tr('读取GX状态…'),
            "activate_main": tr('激活MAIN…'),
            "activate_comments": tr('打开注释…'),
            "open_export_menu": tr('打开导出命令…'),
            "wait_program_file_dialog": tr('等待程序窗口…'),
            "wait_comment_file_dialog": tr('等待注释窗口…'),
            "submit_program_export_path": tr('提交程序路径…'),
            "submit_comment_export_path": tr('提交注释路径…'),
            "wait_program_export_file": tr('等待程序CSV…'),
            "wait_comment_export_file": tr('等待注释CSV…'),
            "export_program": tr('读取MAIN…'),
            "validate_program_csv": tr('校验MAIN…'),
            "export_comments": tr('读取注释…'),
            "validate_comment_csv": tr('校验注释…'),
            "write_manifest": tr('保存校验信息…'),
            "retry_export": tr('正在安全重试…'),
            "compare": tr('比较版本…'),
        }
        intent = getattr(self, "_gx_sync_intent", "reconcile")
        button = (
            getattr(self, "gxworks2_pull_button", None)
            if intent == "pull"
            else getattr(self, "gxworks2_advanced_button", None)
        )
        if button is not None:
            button.setText(labels.get(stage, tr('处理中…')))
        self.statusBar().showMessage(naturalize_display_text(message))

    @staticmethod
    def _gx_conflict_text(result):
        difference = (result.details or {}).get("diff", {}) or {}
        changes = difference.get("changes", []) or []
        lines = [
            naturalize_display_text(result.message),
            "",
            tr('项目指令：{v0} 条', v0=difference.get('project_instruction_count', 0)),
            tr('GX Works2指令：{v0} 条', v0=difference.get('gxworks2_instruction_count', 0)),
            tr('发现差异：{v0} 处', v0=difference.get('changed_instruction_count', 0)),
        ]
        if changes:
            lines.extend(["", tr('前几处差异：')])
            for item in changes[:6]:
                project = item.get("project") or [tr('无'), ""]
                gx = item.get("gxworks2") or [tr('无'), ""]
                lines.append(
                    tr('第{v0}条：项目 {v1} {v2}；GX {v3} {v4}', v0=item.get('index'), v1=project[0], v2=project[1], v3=gx[0], v4=gx[1])
                )
        lines.extend(
            [
                "",
                tr('“使用项目版本”会先备份GX，再覆盖当前MAIN和注释。'),
                tr('“从GX创建新版本”不会删除当前项目版本。'),
            ]
        )
        return "\n".join(lines)

    def _resolve_gxworks2_conflict(self, result, request):
        self._set_gx_sync_status("conflict", result.message)
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(
            tr('首次同步需要选择') if result.status.value == "unbound" else tr('GX Works2同步冲突')
        )
        dialog.setText(self._gx_conflict_text(result))
        use_project = dialog.addButton(
            tr('使用项目版本'),
            QMessageBox.ButtonRole.AcceptRole,
        )
        use_gx = dialog.addButton(
            tr('从GX创建新版本'),
            QMessageBox.ButtonRole.ActionRole,
        )
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        selected = dialog.clickedButton()
        if selected is use_project:
            hashes = (result.details or {}).get("hashes", {}) or {}
            self._import_current_version_to_gxworks2(
                project_id=request["project_id"],
                version_id=request["version_id"],
                expected_current_program_sha256=hashes.get(
                    "gx_program_semantic_sha256"
                ),
                expected_current_comment_sha256=hashes.get(
                    "gx_comment_semantic_sha256"
                ),
            )
        elif selected is use_gx:
            self._start_gxworks2_pull(result, request)

    def _gxworks2_sync_inspected(self, result):
        request = getattr(self, "_gx_sync_request", None)
        if not request:
            return
        self._pending_gx_sync_result = result
        if not result.success:
            self._set_gx_sync_status("error", result.message)
            self.showNormal()
            self.raise_()
            self.activateWindow()
            dialog = GXWorks2SyncErrorDialog(result, self)
            dialog.exec()
            if dialog.retry_requested:
                self._gx_sync_retry_pending = True
                self._set_gx_action_buttons_enabled(False)
                active_button = (
                    self.gxworks2_pull_button
                    if getattr(self, "_gx_sync_intent", "reconcile") == "pull"
                    else self.gxworks2_advanced_button
                )
                active_button.setText(tr('准备重试…'))
                if self._gxworks2_sync_thread is None:
                    QTimer.singleShot(0, self._run_pending_gx_sync_retry)
            return
        status = result.status.value
        intent = getattr(self, "_gx_sync_intent", "reconcile")
        if intent == "pull" and request.get("bootstrap"):
            self._start_gxworks2_pull(result, request)
            return
        if intent == "pull":
            if status == "synced":
                gx_save = (result.details or {}).get("gx_save", {}) or {}
                self._set_gx_sync_status(
                    "unsaved" if gx_save and not gx_save.get("success") else "synced",
                    gx_save.get("message") or tr('GX Works2内容与当前版本一致'),
                )
                self.activity_panel.set_status(tr('GX Works2内容与当前版本一致，无需创建新版本'))
                self.statusBar().showMessage(tr('GX Works2内容与当前版本一致，无需回读。'), 6000)
            else:
                self._start_gxworks2_pull(result, request)
            return
        if status == "synced":
            gx_save = (result.details or {}).get("gx_save", {}) or {}
            self._set_gx_sync_status(
                "unsaved" if gx_save and not gx_save.get("success") else "synced",
                gx_save.get("message") or result.message,
            )
            self.activity_panel.set_status(
                tr('内容一致，GX工程尚未保存')
                if gx_save and not gx_save.get("success")
                else tr('项目与GX Works2已同步')
            )
            self.statusBar().showMessage(
                naturalize_display_text(gx_save.get("message") or result.message),
                6000,
            )
        elif status == "needs_push":
            self._set_gx_sync_status("project_changed", result.message)
            hashes = (result.details or {}).get("hashes", {}) or {}
            self._import_current_version_to_gxworks2(
                project_id=request["project_id"],
                version_id=request["version_id"],
                expected_current_program_sha256=hashes.get(
                    "gx_program_semantic_sha256"
                ),
                expected_current_comment_sha256=hashes.get(
                    "gx_comment_semantic_sha256"
                ),
            )
        elif status == "needs_pull":
            self._set_gx_sync_status("gx_changed", result.message)
            self._start_gxworks2_pull(result, request)
        else:
            self._resolve_gxworks2_conflict(result, request)

    def _gxworks2_sync_thread_finished(self):
        if self._gx_sync_retry_pending:
            self._set_gx_action_buttons_enabled(False)
            active_button = (
                self.gxworks2_pull_button
                if getattr(self, "_gx_sync_intent", "reconcile") == "pull"
                else self.gxworks2_advanced_button
            )
            active_button.setText(tr('准备重试…'))
            QTimer.singleShot(0, self._run_pending_gx_sync_retry)
            return
        self._reset_gx_action_buttons()
        self._update_gx_sync_button_enabled()

    def _run_pending_gx_sync_retry(self):
        if not self._gx_sync_retry_pending or self._gx_sync_busy():
            return
        self._gx_sync_retry_pending = False
        if getattr(self, "_gx_sync_intent", "reconcile") == "pull":
            self._pull_current_version_from_gxworks2()
        else:
            self._sync_current_version_with_gxworks2()

    def _start_gxworks2_pull(self, result, request):
        if self._gxworks2_pull_thread is not None:
            return
        project = self.store.get_project(request["project_id"])
        if not project:
            return
        source_version = request.get("version") or {}
        result_details = dict(getattr(result, "details", {}) or {})
        try:
            version_id, output_dir = self.store.prepare_version(request["project_id"])
        except Exception as error:
            QMessageBox.warning(self, tr('无法创建同步版本'), naturalize_display_text(error))
            return
        self._pending_gx_pull = {
            "result": result,
            "request": request,
            "version_id": version_id,
            "output_dir": output_dir,
        }
        self._set_gx_sync_status("pulling", tr('正在把GX Works2人工修改保存为新版本'))
        self._set_gx_action_buttons_enabled(False)
        self.gxworks2_pull_button.setText(tr('正在读取…'))
        self.statusBar().showMessage(tr('正在解析GX Works2程序并创建新的项目版本…'))
        thread = GXWorks2PullThread(
            result.exported_program_path,
            result.exported_comment_path,
            output_dir,
            plc_model=(
                source_version.get("plc_model")
                or project.get("plc_model")
                or "FX3U"
            ),
            program_name=source_version.get("program_name") or result_details.get("program_name") or "MAIN",
            revision=int(str(version_id).lstrip("vV") or "1"),
        )
        thread.completed.connect(self._gxworks2_pull_completed)
        thread.failed.connect(self._gxworks2_pull_failed)
        self._retain_worker_thread(
            "_gxworks2_pull_thread",
            thread,
            on_finished=self._gxworks2_pull_thread_finished,
        )
        thread.start()

    def _gxworks2_pull_completed(self, metadata):
        pending = self._pending_gx_pull
        if not pending:
            return
        project_id = pending["request"]["project_id"]
        version_id = pending["version_id"]
        metadata = dict(metadata or {})
        bootstrap = bool(pending["request"].get("bootstrap"))
        metadata.update(
            {
                "summary": (
                    tr('从GX Works2导入的初始程序')
                    if bootstrap
                    else tr('从GX Works2同步的人工修改')
                ),
                "parent_version_id": (
                    None if bootstrap else pending["request"]["version_id"]
                ),
                "confirmed_spec_snapshot": None,
                "confirmed_spec_hash": None,
                "import_origin": (
                    "gxworks2_bootstrap" if bootstrap else "gxworks2_pull"
                ),
            }
        )
        try:
            version = self.store.complete_version(project_id, version_id, metadata)
        except Exception as error:
            self._gxworks2_pull_failed(str(error))
            return
        version_dir = self.store.version_dir(project_id, version_id)
        artifacts = version.get("artifacts", {}) or {}
        context = {
            "project_id": project_id,
            "version_id": version_id,
            "revision": version.get("revision"),
            "program_name": version.get("program_name") or "MAIN",
            "ir_schema_version": version.get("ir_schema_version"),
            "ir_sha256": version.get("ir_sha256"),
            "ladder_sha256": version.get("ladder_sha256"),
        }
        baseline_error = ""
        try:
            from gxworks2 import record_sync_snapshot

            record_sync_snapshot(
                pending["result"].details.get("project_identity", {}),
                app_program_path=version_dir / artifacts["program_csv"],
                app_comment_path=version_dir / artifacts["comment_csv"],
                gx_program_path=pending["result"].exported_program_path,
                gx_comment_path=pending["result"].exported_comment_path,
                import_context=context,
            )
        except Exception as error:
            baseline_error = str(error)
        self.store.add_message(
            project_id,
            "assistant",
            (
                tr('已从GX Works2导入初始程序并创建{v0}。', v0=version_display_name(version_id))
                if bootstrap
                else tr('已从GX Works2回读人工修改并创建{v0}。', v0=version_display_name(version_id))
            ),
            kind="system",
            metadata={
                "workflow_mode": "gxworks2_sync",
                "version_id": version_id,
                "parent_version_id": (
                    None if bootstrap else pending["request"]["version_id"]
                ),
                "baseline_error": baseline_error,
            },
        )
        gx_save = (pending["result"].details or {}).get("gx_save", {}) or {}
        save_required = bool(gx_save and not gx_save.get("success"))
        self._set_gx_sync_status(
            "error" if baseline_error else "unsaved" if save_required else "synced",
            (
                tr('新版本已创建，但同步基线保存失败：') + baseline_error
                if baseline_error
                else gx_save.get("message")
                if save_required
                else tr('GX Works2人工修改已保存为新的项目版本')
            ),
        )
        self.activity_panel.set_status(tr('已从GX Works2创建新版本'))
        self.statusBar().showMessage(
            (
                tr('已创建{v0}，但同步基线保存失败', v0=version_display_name(version_id))
                if baseline_error
                else tr('已创建{v0}；{v1}', v0=version_display_name(version_id), v1=gx_save.get('message'))
                if save_required
                else tr('已创建{v0}，项目与GX Works2已同步', v0=version_display_name(version_id))
            ),
            7000,
        )
        if baseline_error:
            QMessageBox.warning(
                self,
                tr('新版本已创建（同步状态未保存）'),
                tr('GX Works2内容已回读为新版本，但无法保存下次比较所需的同步状态：\n')
                + naturalize_display_text(baseline_error),
            )
        self._refresh_projects(self.current_project_id)
        if self.current_project_id == project_id:
            self._load_project(project_id)
            self._select_version(version_id)
            self._set_gx_sync_status(
                "error" if baseline_error else "unsaved" if save_required else "synced"
            )

    def _gxworks2_pull_failed(self, error, *, discard=True):
        pending = self._pending_gx_pull
        if pending and discard:
            try:
                self.store.discard_version(
                    pending["request"]["project_id"], pending["version_id"]
                )
            except Exception:
                pass
        self._set_gx_sync_status("error", str(error))
        self.showNormal()
        self.raise_()
        self.activateWindow()
        QMessageBox.warning(
            self,
            tr('无法从GX Works2创建版本'),
            naturalize_display_text(error),
        )
        self.statusBar().showMessage(tr('GX Works2回读失败，原项目版本未改变'), 7000)

    def _gxworks2_pull_thread_finished(self):
        self._pending_gx_pull = None
        self._gx_sync_intent = "idle"
        self._reset_gx_action_buttons()
        self._update_gx_sync_button_enabled()

    def _import_current_version_to_gxworks2(
        self,
        *,
        project_id=None,
        version_id=None,
        expected_current_program_sha256=None,
        expected_current_comment_sha256=None,
    ):
        if self._gxworks2_import_thread is not None:
            return
        project_id = project_id or self.current_project_id
        version_id = version_id or self.current_version_id
        if not project_id or not version_id:
            QMessageBox.warning(self, tr('无法导入'), tr('请先选择一个已生成的梯形图版本。'))
            return
        version = self.store.get_version(
            project_id, version_id
        )
        if not version or version.get("target_mode") != "ladder":
            QMessageBox.warning(self, tr('无法导入'), tr('只有梯形图版本可导入GX Works2。'))
            return
        version_dir = self.store.version_dir(
            project_id, version_id
        )
        csv_path = version_dir / version.get("artifacts", {}).get("program_csv", "")
        if not csv_path.is_file():
            QMessageBox.critical(self, tr('导入失败'), tr('当前版本缺少程序CSV。'))
            return
        comment_name = version.get("artifacts", {}).get("comment_csv", "")
        if not comment_name:
            QMessageBox.critical(
                self,
                tr('导入失败'),
                tr('当前版本缺少软元件注释CSV，请重新生成该版本后再导入。'),
            )
            return
        comment_csv_path = version_dir / comment_name
        if not comment_csv_path.is_file():
            QMessageBox.critical(self, tr('导入失败'), tr('当前版本缺少软元件注释CSV。'))
            return

        self._set_gx_action_buttons_enabled(False)
        self.gxworks2_import_button.setText(tr('正在写入…'))
        self._set_gx_sync_status("pushing", tr('正在备份并写入GX Works2'))
        self.statusBar().showMessage(tr('正在检查GX Works2与目标工程…'))
        import_context = {
            "project_id": project_id,
            "version_id": version_id,
            "revision": version.get("revision"),
            "program_name": version.get("program_name") or "MAIN",
            "ir_schema_version": version.get("ir_schema_version"),
            "ir_sha256": version.get("ir_sha256"),
            "ladder_sha256": version.get("ladder_sha256"),
        }
        thread = GXWorks2ImportThread(
            csv_path,
            comment_csv_path,
            import_context=import_context,
            expected_current_program_sha256=expected_current_program_sha256,
            expected_current_comment_sha256=expected_current_comment_sha256,
            synchronize_comments=True,
            verify_roundtrip=True,
            save_project=True,
        )
        self._gxworks2_import_thread = thread
        thread.progress_changed.connect(self._gxworks2_import_progress)
        thread.completed.connect(self._gxworks2_import_finished)
        thread.finished.connect(self._gxworks2_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _gxworks2_import_progress(self, stage, message):
        labels = {
            "validate_csv": tr('校验CSV…'),
            "validate_comments": tr('校验注释…'),
            "check_project": tr('检查工程…'),
            "backup": tr('备份当前MAIN…'),
            "backup_comments": tr('备份当前注释…'),
            "compare_baseline": tr('检查外部修改…'),
            "import": tr('从CSV读取…'),
            "import_comments": tr('导入软元件注释…'),
            "verify_roundtrip": tr('回读复核…'),
            "save_project": tr('保存GX工程…'),
            "verify": tr('检查结果…'),
        }
        self.gxworks2_import_button.setText(labels.get(stage, tr('处理中…')))
        self.statusBar().showMessage(naturalize_display_text(message))

    def _gxworks2_import_finished(self, result):
        self._reset_gx_action_buttons()
        display_message = naturalize_display_text(result.message)
        # ``completed`` is emitted from inside QThread.run(), just before the
        # worker actually reaches QThread.finished.  Keep the owning Python
        # reference (and the import button disabled) until ``finished``;
        # otherwise a fast, non-modal success path can destroy a still-running
        # QThread and Qt terminates the whole process.
        if result.success:
            if hasattr(self, "_set_gx_sync_status"):
                project_save = result.details.get("project_save") or {}
                self._set_gx_sync_status(
                    "unsaved"
                    if project_save and not project_save.get("success")
                    else "synced",
                    project_save.get("message") or display_message,
                )
            backup_lines = []
            if result.backup_path:
                backup_lines.append(tr('程序：{v0}', v0=result.backup_path))
            comment_backup = result.details.get("comment_backup_path", "")
            if comment_backup:
                backup_lines.append(tr('软元件注释：{v0}', v0=comment_backup))
            details = (
                tr('\n\n导入前备份：\n') + "\n".join(backup_lines)
                if backup_lines
                else ""
            )
            if result.error_code is not None:
                self.showNormal()
                self.raise_()
                self.activateWindow()
                QMessageBox.warning(
                    self,
                    tr('写入完成（需要核对）'),
                    display_message + details,
                )
                self.statusBar().showMessage(display_message, 10000)
            else:
                # GX Works2 is normally the foreground application at this
                # point.  A modal success box owned by this window can remain
                # hidden behind it, leaving the workbench disabled and looking
                # stuck (or briefly exposing an empty white native window).
                # Success needs no decision, so report it in the persistent
                # workbench status instead of blocking on a modal dialog.
                self.activity_panel.set_status(display_message)
                self.statusBar().showMessage(
                    display_message + (tr('；导入前备份已保留') if details else ""),
                    10000,
                )
            return
        error_value = getattr(result.error_code, "value", str(result.error_code or ""))
        if hasattr(self, "_set_gx_sync_status"):
            self._set_gx_sync_status(
                "conflict" if error_value == "external_modification_detected" else "error",
                display_message,
            )
        backup_note = (
            tr('\n\n已保留导入前程序备份：\n{v0}', v0=result.backup_path)
            if result.backup_path
            else ""
        )
        self.showNormal()
        self.raise_()
        self.activateWindow()
        QMessageBox.warning(
            self,
            tr('GX Works2写入未完成'),
            display_message + backup_note,
        )
        self.statusBar().showMessage(display_message, 8000)

    def _gxworks2_import_thread_finished(self):
        self._gxworks2_import_thread = None
        self._gx_sync_intent = "idle"
        self._reset_gx_action_buttons()
        if hasattr(self, "_update_gx_sync_button_enabled"):
            self._update_gx_sync_button_enabled()
        else:
            version = (
                self.store.get_version(self.current_project_id, self.current_version_id)
                if self.current_project_id and self.current_version_id
                else None
            )
            self.gxworks2_import_button.setEnabled(
                bool(version and version.get("target_mode") == "ladder")
            )

    def _show_simulator_test_menu(self, position):
        menu = QMenu(self.simulator_test_button)
        regenerate = menu.addAction(tr('重新生成测试方案'))
        regenerate.setEnabled(
            self._simulator_test_plan_thread is None
            and self._simulator_test_execute_thread is None
            and bool(self.current_version_id)
        )
        selected = menu.exec(
            self.simulator_test_button.mapToGlobal(position)
        )
        if selected is regenerate:
            self._generate_simulator_test_plan(force_regenerate=True)

    def _generate_simulator_test_plan(self, _checked=False, *, force_regenerate=False):
        """Qt-safe entry point for preparing a simulator test.

        Exceptions escaping a PyQt signal callback can terminate the native Qt
        process on Windows instead of producing a Python dialog.  Keep all
        synchronous cache/version validation failures inside the application.
        """

        try:
            return self._generate_simulator_test_plan_impl(
                _checked,
                force_regenerate=force_regenerate,
            )
        except Exception as error:
            self.simulator_test_button.setText(tr('仿真测试'))
            self._set_busy(False, tr('仿真测试准备失败'))
            self.simulator_test_button.setEnabled(bool(self.current_version_id))
            message = tr('无法准备仿真测试：{v0}', v0=naturalize_display_text(error))
            self.statusBar().showMessage(message, 8000)
            QMessageBox.warning(self, tr('无法测试'), message)
            return None

    def _generate_simulator_test_plan_impl(
        self,
        _checked=False,
        *,
        force_regenerate=False,
    ):
        if (
            self._simulator_test_plan_thread is not None
            or self._simulator_test_execute_thread is not None
        ):
            return
        if not self.current_project_id or not self.current_version_id:
            QMessageBox.warning(self, tr('无法测试'), tr('请先选择一个已生成的梯形图版本。'))
            return
        version = self.store.get_version(
            self.current_project_id, self.current_version_id
        )
        project = self.store.get_project(self.current_project_id)
        if not version or version.get("target_mode") != "ladder":
            QMessageBox.warning(self, tr('无法测试'), tr('只有梯形图版本可进行仿真测试。'))
            return
        if not project or project.get("active_version_id") != self.current_version_id:
            QMessageBox.warning(self, tr('无法测试'), tr('只能测试当前启用版本。'))
            return

        if not force_regenerate:
            cached_plan = self.store.load_latest_simulator_test_plan(
                self.current_project_id, self.current_version_id
            )
            if cached_plan is not None:
                self.statusBar().showMessage(
                    tr('已复用当前版本保存的仿真测试方案。'), 6000
                )
                self._simulator_test_plan_ready("cached", cached_plan)
                return
        if not self._ensure_api_configured():
            return

        task_id = uuid.uuid4().hex
        self.simulator_test_button.setEnabled(False)
        self.simulator_test_button.setText(tr('生成测试中…'))
        self._set_busy(True, tr('AI 正在生成仿真测试方案'))
        self.activity_panel.reset()
        self.activity_panel.set_status(tr('正在整理程序行为和 I/O'))
        thread = SimulatorTestPlanThread(
            task_id,
            self.store,
            self.current_project_id,
            self.current_version_id,
            effort=None,
        )
        self._retain_worker_thread("_simulator_test_plan_thread", thread)
        thread.progress_updated.connect(self._simulator_test_plan_progress)
        thread.thinking_updated.connect(self._simulator_test_plan_reasoning)
        thread.content_updated.connect(self._simulator_test_plan_content)
        thread.completed.connect(self._simulator_test_plan_ready)
        thread.failed.connect(self._simulator_test_plan_failed)
        thread.start()

    def _is_current_simulator_plan_task(self, task_id):
        thread = self._simulator_test_plan_thread
        return thread is not None and str(thread.task_id) == str(task_id)

    def _simulator_test_plan_reasoning(self, task_id, token):
        if self._is_current_simulator_plan_task(task_id):
            rendered = self._activity_stream_chunk(task_id, "reasoning", token)
            if rendered:
                self.activity_panel.append_reasoning(rendered)

    def _simulator_test_plan_content(self, task_id, token):
        if self._is_current_simulator_plan_task(task_id):
            rendered = self._activity_stream_chunk(task_id, "content", token)
            if rendered:
                self.activity_panel.append_content(rendered)

    def _simulator_test_plan_progress(self, task_id, message):
        if not self._is_current_simulator_plan_task(task_id):
            return
        self._flush_activity_display_streams()
        text = naturalize_display_text(message).strip()
        if not text:
            return
        self.activity_panel.set_status(text)
        self.activity_panel.append_content(f"\n[{text}]\n")
        self.conversation_status.setText(text)
        self.statusBar().showMessage(text)

    def _simulator_test_plan_ready(self, task_id, plan):
        try:
            return self._simulator_test_plan_ready_impl(task_id, plan)
        except Exception as error:
            self._simulator_test_plan_failed(task_id, str(error))
            return None

    def _simulator_test_plan_ready_impl(self, task_id, plan):
        self.simulator_test_button.setText(tr('仿真测试'))
        self._set_busy(False, tr('测试方案待确认'))
        self.activity_panel.set_status(tr('测试方案生成完成'))
        suite = plan.get("suite") or {}
        tests = suite.get("tests") or []
        step_count = sum(len(item.get("steps") or []) for item in tests)
        invariant_count = sum(len(item.get("invariants") or []) for item in tests)
        fault_count = sum(len(item.get("fault_injections") or []) for item in tests)
        names = "\n".join(
            f"• {index}. "
            f"{preferred_display_name(item, kind=tr('测试项目'), index=index)}"
            for index, item in enumerate(tests[:8], start=1)
        )
        if len(tests) > 8:
            names += tr('\n• 其余 {v0} 项', v0=len(tests) - 8)
        cache_note = (
            tr('当前版本与程序内容未变化，已复用保存的测试方案。\n如需重新生成，请右键“仿真测试”。\n\n')
            if plan.get("cache_reused")
            else ""
        )
        suite_display_name = preferred_display_name(
            suite,
            kind=tr('测试方案'),
            descriptive_keys=("display_name", "description", "title", "label"),
        )
        message = cache_note + (
            tr('测试方案：{v0}\n测试 {v1} 项，步骤 {v2} 个，运行约束 {v3} 项，故障场景 {v4} 项。\n\n{v5}\n\n确认后将依次导入当前程序和注释、启动 GX Simulator2、执行测试并保存完整轨迹。', v0=suite_display_name, v1=len(tests), v2=step_count, v3=invariant_count, v4=fault_count, v5=names)
        )
        answer = QMessageBox.question(
            self,
            tr('确认运行仿真测试'),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(tr('测试方案已保存，未修改 GX Works2。'), 6000)
            self.simulator_test_button.setEnabled(True)
            return
        self._execute_simulator_test_plan(plan)

    def _simulator_test_plan_failed(self, task_id, error):
        self.simulator_test_button.setText(tr('仿真测试'))
        self._set_busy(False, tr('测试方案生成失败'))
        self.simulator_test_button.setEnabled(bool(self.current_version_id))
        display_error = naturalize_display_text(error)
        self.activity_panel.show_error(display_error)
        QMessageBox.warning(self, tr('仿真测试方案未生成'), display_error)

    def _execute_simulator_test_plan(self, plan):
        task_id = uuid.uuid4().hex
        project_id = str((plan.get("binding") or {}).get("project_id") or "")
        version_id = str((plan.get("binding") or {}).get("version_id") or "")
        self._active_simulator_test_task_id = task_id
        self.simulation_progress_panel.setVisible(True)
        self.simulation_progress_bar.setValue(0)
        self.simulation_progress_percent.setText("0%")
        self.simulation_progress_current.setText(tr('正在准备 GX Simulator2'))
        self.simulation_progress_log.clear()
        self.simulation_progress_log.appendPlainText(tr('0%  开始仿真测试工作流'))
        self.simulator_test_button.setEnabled(False)
        self.simulator_test_button.setText(tr('仿真运行中…'))
        self._set_busy(True, tr('正在准备 GX Simulator2'))
        thread = SimulatorTestExecuteThread(
            task_id,
            self.store,
            project_id,
            version_id,
            plan,
        )
        self._retain_worker_thread("_simulator_test_execute_thread", thread)
        thread.progress_updated.connect(self._simulator_test_workflow_progress)
        thread.test_progress_updated.connect(self._simulator_test_progress)
        thread.completed.connect(self._simulator_test_finished)
        thread.failed.connect(self._simulator_test_failed)
        thread.start()

    def _simulator_test_workflow_progress(self, task_id, message):
        if task_id != getattr(self, "_active_simulator_test_task_id", None):
            return
        display_message = naturalize_display_text(message)
        self.conversation_status.setText(display_message)
        self.statusBar().showMessage(display_message)
        self.simulation_progress_panel.setVisible(True)
        self.simulation_progress_current.setText(display_message)
        if display_message:
            self.simulation_progress_log.appendPlainText(
                f"• {display_message}"
            )

    def _simulator_test_progress(self, task_id, payload):
        if task_id != getattr(self, "_active_simulator_test_task_id", None):
            return
        update = dict(payload or {})
        percent = max(0, min(100, int(update.get("percent") or 0)))
        message = naturalize_display_text(
            update.get("message") or tr('正在执行仿真测试')
        )
        self.simulation_progress_panel.setVisible(True)
        self.simulation_progress_bar.setValue(percent)
        self.simulation_progress_percent.setText(f"{percent}%")

        test_index = update.get("test_index")
        test_count = update.get("test_count")
        step_index = update.get("step_index")
        step_count = update.get("step_count")
        location = []
        if test_index and test_count:
            location.append(tr('测试 {v0}/{v1}', v0=test_index, v1=test_count))
        if step_index and step_count:
            location.append(tr('步骤 {v0}/{v1}', v0=step_index, v1=step_count))
        current = " · ".join(location + [message]) if location else message
        self.simulation_progress_current.setText(current)

        event = str(update.get("event") or "")
        log_events = {
            "workflow_stage",
            "suite_started",
            "test_started",
            "cpu_reset",
            "initial_write",
            "step_started",
            "device_write",
            "assertion",
            "test_error",
            "test_completed",
            "suite_completed",
        }
        if event in log_events:
            marker = "•"
            if event == "assertion":
                marker = "✓" if update.get("passed") else "✕"
            elif event == "test_completed":
                marker = "✓" if update.get("passed") else "✕"
            self.simulation_progress_log.appendPlainText(
                f"{percent:>3}% {marker} {current}"
            )
            scrollbar = self.simulation_progress_log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _simulator_test_finished(self, task_id, workflow):
        try:
            return self._simulator_test_finished_impl(task_id, workflow)
        except Exception as error:
            self._simulator_test_failed(task_id, str(error))
            return None

    def _show_simulator_report(self, workflow):
        """Show the complete persisted result instead of a count-only alert."""

        try:
            from simulator.reporting import build_simulator_report
            from ui.desktop.dialogs.simulator_report import SimulatorReportDialog

            execution = (workflow or {}).get("execution") or {}
            record = execution.get("record") or {}
            evidence_path = None
            relative = str(record.get("trace_artifact") or "").strip()
            project_id = str(record.get("project_id") or self.current_project_id or "")
            version_id = str(record.get("version_id") or self.current_version_id or "")
            suite = {}
            program = {}
            if relative and project_id and version_id:
                version_root = self.store.version_dir(
                    project_id,
                    version_id,
                ).resolve()
                candidate = (version_root / relative).resolve()
                if candidate == version_root or version_root in candidate.parents:
                    evidence_path = candidate
            if project_id and version_id:
                try:
                    program = self.store.load_program_ir(project_id, version_id) or {}
                except Exception:
                    program = {}
                run_id = str(record.get("run_id") or "").strip()
                if run_id:
                    try:
                        saved_run = self.store.load_simulator_run(
                            project_id,
                            version_id,
                            run_id,
                        ) or {}
                        suite = saved_run.get("suite") or {}
                    except Exception:
                        suite = {}
            report = build_simulator_report(
                workflow or {},
                evidence_path=evidence_path,
                suite=suite,
                program=program,
            )
            dialog = SimulatorReportDialog(report, self)
            dialog.exec()
            return str(getattr(dialog, "requested_action", "") or "")
        except Exception as report_error:
            fallback = str(
                (workflow or {}).get("message")
                or ((workflow or {}).get("execution") or {}).get("result", {}).get("error")
                or report_error
            )
            QMessageBox.warning(self, tr('仿真测试结果'), fallback)
            return ""

    def _simulator_test_finished_impl(self, task_id, workflow):
        if task_id == getattr(self, "_active_simulator_test_task_id", None):
            self._active_simulator_test_task_id = None
        self.simulation_progress_panel.setVisible(True)
        self.simulation_progress_bar.setValue(100)
        self.simulation_progress_percent.setText("100%")
        self.simulator_test_button.setText(tr('仿真测试'))
        self._set_busy(False, tr('仿真测试已结束'))
        version = (
            self.store.get_version(self.current_project_id, self.current_version_id)
            if self.current_project_id and self.current_version_id
            else None
        )
        self.simulator_test_button.setEnabled(
            bool(version and version.get("target_mode") == "ladder")
        )
        status = str(workflow.get("status") or "error")
        execution = workflow.get("execution") or {}
        result = execution.get("result") or {}
        record = execution.get("record") or {}
        counts = result.get("counts") or {}
        details = (
            tr('\n\n通过：{v0}\n失败：{v1}\n错误：{v2}\n未执行：{v3}', v0=counts.get('passed', 0), v1=counts.get('failed', 0), v2=counts.get('error', 0), v3=result.get('not_executed_count', 0))
        )
        scan_rows = [
            item.get("scan_monitor") or {}
            for item in result.get("results", []) or []
            if isinstance(item, dict)
            and isinstance(item.get("scan_monitor"), dict)
            and item["scan_monitor"].get("sampled")
        ]
        if scan_rows:
            current_values = [
                row.get("latest_current_ms")
                for row in scan_rows
                if row.get("latest_current_ms") is not None
            ]
            minimum_values = [
                row.get("observed_minimum_ms")
                for row in scan_rows
                if row.get("observed_minimum_ms") is not None
            ]
            maximum_values = [
                row.get("observed_maximum_ms")
                for row in scan_rows
                if row.get("observed_maximum_ms") is not None
            ]
            if current_values or minimum_values or maximum_values:
                current_text = (
                    f"{current_values[-1]:g} ms" if current_values else tr('无数据')
                )
                minimum_text = (
                    f"{min(minimum_values):g} ms" if minimum_values else tr('无数据')
                )
                maximum_text = (
                    f"{max(maximum_values):g} ms" if maximum_values else tr('无数据')
                )
                details += (
                    tr('\n扫描时间：当前 {v0} / 最小 {v1} / 最大 {v2}', v0=current_text, v1=minimum_text, v2=maximum_text)
                )
        status_message = naturalize_display_text(
            workflow.get("message") or tr('仿真测试已结束。')
        )
        message = status_message + details
        self.simulation_progress_current.setText(message)
        self.simulation_progress_log.appendPlainText(f"100% • {message}")
        report_action = self._show_simulator_report(workflow)
        if report_action == "debug" and record.get("run_id"):
            project_id = str(record.get("project_id") or self.current_project_id or "")
            version_id = str(record.get("version_id") or self.current_version_id or "")
            project = self.store.get_project(project_id) if project_id else None
            debug_version = (
                self.store.get_version(project_id, version_id)
                if project_id and version_id
                else None
            )
            if project and debug_version:
                debug_index = self.workflow_combo.findData("debug")
                if debug_index >= 0:
                    self.workflow_combo.setCurrentIndex(debug_index)
                if self._start_evidence_debug_plan(
                    project,
                    debug_version,
                    record,
                ):
                    return
        self.statusBar().showMessage(status_message, 8000)

    def _simulator_test_failed(self, task_id, error):
        if task_id == getattr(self, "_active_simulator_test_task_id", None):
            self._active_simulator_test_task_id = None
        self.simulation_progress_panel.setVisible(True)
        display_error = naturalize_display_text(error)
        self.simulation_progress_current.setText(tr('仿真测试未完成：{v0}', v0=display_error))
        self.simulation_progress_log.appendPlainText(f"✕ {display_error}")
        self.simulator_test_button.setText(tr('仿真测试'))
        self._set_busy(False, tr('仿真测试执行失败'))
        self.simulator_test_button.setEnabled(bool(self.current_version_id))
        self._show_simulator_report(
            {
                "status": "error",
                "message": tr('仿真测试执行失败。'),
                "execution": {
                    "result": {
                        "status": "error",
                        "name": tr('当前程序仿真测试'),
                        "counts": {
                            "passed": 0,
                            "failed": 0,
                            "error": 1,
                            "unavailable": 0,
                        },
                        "test_count": 0,
                        "attempted_count": 0,
                        "executed_count": 0,
                        "not_executed_count": 0,
                        "results": [],
                        "error": str(error),
                    }
                },
            }
        )


# Keep selected inspection methods available on the retained two-column
# compatibility UI.  The production entry below uses the industrial workbench.
for _compatibility_method in (
    "_json_sha256",
    "_start_inspection_task",
    "_inspection_local_ready",
    "_inspection_done",
    "_inspection_failed",
    "_start_inspection_repair",
    "_version_with_json",
    "_compile_success",
):
    setattr(
        PLCSystemUI,
        _compatibility_method,
        getattr(_IndustrialWorkbenchUI, _compatibility_method),
    )




