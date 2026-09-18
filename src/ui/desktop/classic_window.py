"""Classic window."""
from shared.i18n import tr
import os
import json
import copy
import shutil
import uuid
from pathlib import Path
from ui.desktop.qt import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel, QMessageBox, QScrollArea, QFrame, QComboBox, QStackedWidget, QFileDialog, QMenu, QDialog, QSplitter
from ui.desktop.qt import QSvgWidget
from ui.desktop.qt import Qt, QTimer
from ui.desktop.qt import QAction
from application.model_api import _detect_plc_model, _build_model_context
from model_runtime.provider import reload_model_provider
from storage.config import get_api_key, load_full_config
from ui.desktop.sfc.editor import SFCEditorWidget
from plc.specification.confirmed import canonicalize_confirmed_spec
from shared.display_names import naturalize_display_text
from ui.desktop.dialogs.workflow import SimpleRequirementConfirmDialog
from ui.desktop.styles import QSS_TEMPLATE
from ui.desktop.widgets.activity import ThinkingPanel
from ui.desktop.workers import AnalysisThread, CompilerThread

class PLCSystemUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self._compiler_thread = None  # 避免与 QObject.thread() 冲突
        self._analysis_thread = None
        self._active_task_id = None
        self._active_output_dir = None
        self._last_ladder_json = None
        self._last_confirmed_spec = None
        self._last_result = None
        self._last_target_mode = None
        self._pending_original_request = ""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowSystemMenuHint |       
            Qt.WindowType.WindowMinimizeButtonHint |   
            Qt.WindowType.WindowMaximizeButtonHint     
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1280, 800)
        self.init_ui()
        self.setStyleSheet(QSS_TEMPLATE)

    def on_lang_mode_changed(self, index):
        if index == 0:
            self.canvas_title.setText(tr('梯形图预览'))
            self._refresh_plc_target_labels()
            self.display_container.setCurrentIndex(0)
        else:
            self.canvas_title.setText(tr('ST 代码预览'))
            self.format_badge.setText("Structured Text")
            self.display_container.setCurrentIndex(1)

    def _current_plc_model(self):
        if hasattr(self, "plc_combo"):
            return self.plc_combo.currentText().strip().upper() or "FX3U"
        return "FX3U"

    def _refresh_plc_target_labels(self):
        if not hasattr(self, "format_badge"):
            return
        model = self._current_plc_model()
        gx_tool = "GX Works3" if model == "FX5U" else "GX Works2"
        if hasattr(self, "lang_combo") and self.lang_combo.currentIndex() == 0:
            self.format_badge.setText(gx_tool)
        if hasattr(self, "export_csv_btn"):
            self.export_csv_btn.setText(
                tr('导出 GX Works2 CSV')
                if model == "FX3U"
                else tr('导出程序文件（FX5U）')
            )

    def _on_plc_model_changed(self, _index):
        self._refresh_plc_target_labels()
        try:
            from storage.config import save_config

            config = load_full_config()
            config["plc_model"] = self._current_plc_model()
            save_config(config)
        except Exception:
            pass

    def _on_input_mode_toggled(self, checked):
        """按钮文字 = 当前模式（高亮）。checked=True → SFC，else → 文本。"""
        if checked:
            self.sfc_toggle_btn.setText(tr('流程图模式'))
            self.input_stack.setCurrentIndex(1)  # SFC
        else:
            self.sfc_toggle_btn.setText(tr('文本模式'))
            self.input_stack.setCurrentIndex(0)  # 文本

    def _on_sfc_text_generated(self, text: str):
        self.input_edit.setPlainText(text)
        self.sfc_toggle_btn.setChecked(False)

    def copy_st_to_clipboard(self):
        code_text = self.st_viewer.toPlainText().strip()
        if code_text:
            clipboard = QApplication.clipboard()
            clipboard.setText(code_text)
            QMessageBox.information(self, tr('成功'), tr('ST 代码已成功复制到剪贴板！'))
        else:
            QMessageBox.warning(self, tr('警告'), tr('当前无代码内容可供复制。'))

    # 【新增】手动选择路径保存 CSV 文件的功能
    def manual_export_csv(self):
        if self._current_plc_model() != "FX3U":
            QMessageBox.information(
                self,
                tr('FX5U 导出说明'),
                tr('当前已验证的语句表导出仅适用于 FX3U / GX Works2。FX5U 程序仍可生成和检查，但不会把 GX Works2 格式冒充为 GX Works3 文件。'),
            )
            return
        artifacts = (self._last_result or {}).get("artifacts", {})
        if self._active_output_dir is None:
            source_program_csv = ""
            source_comment_csv = ""
        else:
            source_program_csv = str(
                self._active_output_dir / artifacts.get("program_csv", "")
            )
            source_comment_csv = str(
                self._active_output_dir / artifacts.get("comment_csv", "")
            )

        # 1. 检查主程序文件是否存在
        if not source_program_csv or not os.path.isfile(source_program_csv):
            QMessageBox.warning(self, tr('提示'), tr('请先输入需求并完成【编译】后再尝试导出！'))
            return

        # 唤起标准另存为对话框
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            tr('导出 GX Works2 兼容明细表与软元件注释'),
            "plc_import_program.csv",
            "CSV Files (*.csv);;All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )

        # 确认选择路径后复制文件
        if file_path:
            try:
                # 2. 复制主程序 CSV 到用户指定的路径
                shutil.copy(source_program_csv, file_path)
                
                # 3. 自动计算注释文件的配套路径（例如将 xxx.csv 转换为 xxx_注释.csv）
                base_name, ext = os.path.splitext(file_path)
                comment_file_path = tr('{v0}_注释{v1}', v0=base_name, v1=ext)
                
                # 4. 判断并同步复制软元件注释 CSV
                msg_append = ""
                if os.path.exists(source_comment_csv):
                    shutil.copy(source_comment_csv, comment_file_path)
                    msg_append = tr('\n\n配套的软元件注释已自动保存至：\n{v0}', v0=comment_file_path)
                else:
                    msg_append = tr('\n\n(提示: 未检测到伴随的注释数据)')

                QMessageBox.information(
                    self, 
                    tr('导出成功'),
                    tr('主程序文件已成功保存至：\n{v0}{v1}\n\n【GX Works2 导入方法】:\n· 导入程序：点击菜单栏【工程】->【打开其他格式文件】->【导入 Excel 语句表】。\n· 导入注释：在左侧导航栏双击打开【软元件注释】，右键点击列表选择【导入 CSV 文件】。', v0=file_path, v1=msg_append)
                )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    tr('错误'),
                    tr('文件导出失败: {v0}', v0=naturalize_display_text(e)),
                )

    def init_ui(self):

        self.bg_frame = QFrame()
        self.bg_frame.setObjectName("MainBgFrame")
        self.bg_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(self.bg_frame)
        
        root_layout = QVBoxLayout(self.bg_frame)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)


        self.title_bar = QFrame()
        self.title_bar.setObjectName("CustomTitleBar")
        self.title_bar.setFixedHeight(48)


        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(14, 0, 8, 0)
        title_layout.setSpacing(9)

        app_mark = QLabel("GX")
        app_mark.setObjectName("AppMark")
        app_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_mark.setFixedSize(28, 28)

        title_text_layout = QVBoxLayout()
        title_text_layout.setContentsMargins(0, 0, 0, 0)
        title_text_layout.setSpacing(0)

        self.window_title = QLabel("PLC AI Studio")
        self.window_title.setObjectName("WindowTitleLabel")
        window_subtitle = QLabel(tr('工业控制程序生成工作台'))
        window_subtitle.setObjectName("WindowSubtitle")
        title_text_layout.addWidget(self.window_title)
        title_text_layout.addWidget(window_subtitle)

        self.options_btn = QPushButton(tr('菜单'))
        self.options_btn.setObjectName("OptionsBtn")
        self.options_btn.setFixedHeight(30)
        self.options_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.options_btn.setToolTip(tr('新建对话或配置 API 请求'))

        self.top_menu = QMenu(self)

        self.new_chat_action = QAction(tr('开始新的对话'), self)
        self.new_chat_action.triggered.connect(self.clear_chat_data)
        self.top_menu.addAction(self.new_chat_action)

        self.top_menu.addSeparator()

        self.api_config_action = QAction(tr('API 请求格式配置'), self)
        self.api_config_action.triggered.connect(self.open_api_config_dialog)
        self.top_menu.addAction(self.api_config_action)

        self.options_btn.setMenu(self.top_menu)

        self.min_btn = QPushButton("—")
        self.min_btn.setObjectName("MinBtn")
        self.min_btn.setFixedSize(36, 32)
        self.min_btn.setToolTip(tr('最小化'))
        self.min_btn.clicked.connect(self.showMinimized)

        self.max_btn = QPushButton("□")
        self.max_btn.setObjectName("MaxBtn")
        self.max_btn.setFixedSize(36, 32)
        self.max_btn.setToolTip(tr('最大化或还原'))
        self.max_btn.clicked.connect(self.toggle_maximize)

         
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("CloseBtn")
        self.close_btn.setFixedSize(36, 32)
        self.close_btn.setToolTip(tr('关闭'))
        self.close_btn.clicked.connect(self.close)

        title_layout.addWidget(app_mark)
        title_layout.addLayout(title_text_layout)
        title_layout.addStretch()
        title_layout.addWidget(self.options_btn)
        title_layout.addWidget(self.min_btn)
        title_layout.addWidget(self.max_btn)
        title_layout.addWidget(self.close_btn)

        root_layout.addWidget(self.title_bar)


        content_widget = QWidget()
        main_layout = QHBoxLayout(content_widget)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(0)
        
        root_layout.addWidget(content_widget)

        left_card = QFrame()
        left_card.setObjectName("ControlCard")
        left_card.setMinimumWidth(340)
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(18, 18, 18, 18)
        left_layout.setSpacing(12)

        title_label = QLabel(tr('控制需求'))
        title_label.setObjectName("HeaderTitle")
        title_description = QLabel(tr('描述控制逻辑，选择目标语言并生成可导入程序。'))
        title_description.setObjectName("HeaderDescription")
        title_description.setWordWrap(True)

        self.lang_combo = QComboBox()
        self.lang_combo.addItems([tr('梯形图 / GX Works2'), tr('ST 结构化文本')])
        self.lang_combo.setFixedHeight(34)
        self.lang_combo.setToolTip(tr('选择最终生成的程序类型'))
        self.lang_combo.currentIndexChanged.connect(self.on_lang_mode_changed)

        # ---- 文本输入 ----
        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText(
            tr('输入控制需求，例如：\nX0 启动，X1 停止，Y0 电机自锁运行；过载时立即停机并报警。')
        )
        self.input_edit.setAccessibleName(tr('PLC 控制需求输入'))

        # ---- SFC 流程图编辑器 ----
        self.sfc_editor = SFCEditorWidget()
        self.sfc_editor.text_generated.connect(self._on_sfc_text_generated)

        # ---- 输入模式切换（QStackedWidget） ----
        self.input_stack = QStackedWidget()
        self.input_stack.addWidget(self.input_edit)      # index 0: 文本
        self.input_stack.addWidget(self.sfc_editor)       # index 1: 流程图

        self.compile_btn = QPushButton(tr('分析并生成'))
        self.compile_btn.setObjectName("PrimaryButton")
        self.compile_btn.setFixedHeight(46)
        self.compile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.compile_btn.setToolTip(tr('分析需求、确认方案并生成程序'))
        self.compile_btn.clicked.connect(self.start_compile)

        left_layout.addWidget(title_label)
        left_layout.addWidget(title_description)

        settings_surface = QFrame()
        settings_surface.setObjectName("ToolbarSurface")
        settings_layout = QVBoxLayout(settings_surface)
        settings_layout.setContentsMargins(10, 9, 10, 9)
        settings_layout.setSpacing(8)

        input_mode_row = QHBoxLayout()
        input_mode_row.setSpacing(8)
        input_mode_label = QLabel(tr('输入方式'))
        input_mode_label.setObjectName("SectionLabel")

        self.sfc_toggle_btn = QPushButton(tr('文本模式'))
        self.sfc_toggle_btn.setObjectName("ModeToggleBtn")
        self.sfc_toggle_btn.setCheckable(True)
        self.sfc_toggle_btn.setFixedHeight(34)
        self.sfc_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sfc_toggle_btn.setToolTip(tr('在文本需求与流程图编辑器之间切换'))
        self.sfc_toggle_btn.toggled.connect(self._on_input_mode_toggled)
        self.sfc_toggle_btn.setChecked(False)  # 默认文本模式

        input_mode_row.addWidget(input_mode_label)
        input_mode_row.addStretch()
        input_mode_row.addWidget(self.sfc_toggle_btn)
        settings_layout.addLayout(input_mode_row)

        plc_row = QHBoxLayout()
        plc_row.setSpacing(8)
        plc_label = QLabel(tr('PLC 型号'))
        plc_label.setObjectName("SectionLabel")
        self.plc_combo = QComboBox()
        self.plc_combo.addItems(["FX3U", "FX5U"])
        self.plc_combo.setFixedHeight(34)
        self.plc_combo.setToolTip(tr('选择目标 PLC；地址、特殊软元件和指令规则随型号切换'))
        try:
            configured_model = str(
                load_full_config().get("plc_model", "FX3U")
            ).strip().upper()
        except Exception:
            configured_model = "FX3U"
        model_index = self.plc_combo.findText(configured_model)
        self.plc_combo.setCurrentIndex(model_index if model_index >= 0 else 0)
        self.plc_combo.currentIndexChanged.connect(
            self._on_plc_model_changed
        )
        plc_row.addWidget(plc_label)
        plc_row.addWidget(self.plc_combo, stretch=1)
        settings_layout.addLayout(plc_row)

        target_row = QHBoxLayout()
        target_row.setSpacing(8)
        target_label = QLabel(tr('输出格式'))
        target_label.setObjectName("SectionLabel")
        target_row.addWidget(target_label)
        target_row.addWidget(self.lang_combo, stretch=1)
        settings_layout.addLayout(target_row)
        left_layout.addWidget(settings_surface)

        left_layout.addWidget(self.input_stack, stretch=1)
        input_helper = QLabel(tr('生成前会进行需求确认、软元件一致性和双线圈硬校验。'))
        input_helper.setObjectName("HelperText")
        input_helper.setWordWrap(True)
        left_layout.addWidget(input_helper)
        left_layout.addWidget(self.compile_btn)

        right_card = QFrame()
        right_card.setObjectName("CanvasCard")
        right_card.setMinimumWidth(560)
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(12)
         
        result_header = QHBoxLayout()
        result_header.setSpacing(8)
        self.canvas_title = QLabel(tr('梯形图预览'))
        self.canvas_title.setObjectName("CanvasTitle")
        self.format_badge = QLabel("GX Works2")
        self.format_badge.setObjectName("FormatBadge")
        self.result_status = QLabel(tr('等待生成'))
        self.result_status.setObjectName("StatusBadge")
        result_header.addWidget(self.canvas_title)
        result_header.addWidget(self.format_badge)
        result_header.addStretch()
        result_header.addWidget(self.result_status)
        right_layout.addLayout(result_header)

        self.thinking_panel = ThinkingPanel()
        right_layout.addWidget(self.thinking_panel)

        self.display_container = QStackedWidget()
        
        ladder_page_widget = QWidget()
        ladder_page_layout = QVBoxLayout(ladder_page_widget)
        ladder_page_layout.setContentsMargins(0, 0, 0, 0)
        ladder_page_layout.setSpacing(12)

        self.scroll_area = QScrollArea()
        self.svg_viewer = QSvgWidget() 
        self.scroll_area.viewport().setStyleSheet("background-color: #ffffff;")
        self.scroll_area.setWidget(self.svg_viewer)
        self.scroll_area.setWidgetResizable(False) 
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.export_csv_btn = QPushButton(tr('导出 GX Works2 CSV'))
        self.export_csv_btn.setObjectName("PrimaryButton")
        self.export_csv_btn.setFixedHeight(42)
        self.export_csv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_csv_btn.clicked.connect(self.manual_export_csv)

        ladder_page_layout.addWidget(self.scroll_area, stretch=1)
        ladder_page_layout.addWidget(self.export_csv_btn)
        self.display_container.addWidget(ladder_page_widget)
        
        st_page_widget = QWidget()
        st_page_layout = QVBoxLayout(st_page_widget)
        st_page_layout.setContentsMargins(0, 0, 0, 0)
        st_page_layout.setSpacing(12)

        self.st_viewer = QTextEdit()
        self.st_viewer.setReadOnly(True)
        self.st_viewer.setAccessibleName(tr('ST 代码预览'))
        self.st_viewer.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | 
            Qt.TextInteractionFlag.TextSelectableByKeyboard
        )

        self.copy_btn = QPushButton(tr('复制 ST 代码'))
        self.copy_btn.setObjectName("PrimaryButton")
        self.copy_btn.setFixedHeight(42)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self.copy_st_to_clipboard)

        st_page_layout.addWidget(self.st_viewer, stretch=1)
        st_page_layout.addWidget(self.copy_btn)
        self.display_container.addWidget(st_page_widget)

        right_layout.addWidget(self.display_container, stretch=1)

        # 可拖动分栏更适合长需求与大型梯形图之间切换工作重心。
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(left_card)
        self.main_splitter.addWidget(right_card)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 7)
        self.main_splitter.setSizes([390, 850])
        main_layout.addWidget(self.main_splitter)

        self.setTabOrder(self.sfc_toggle_btn, self.plc_combo)
        self.setTabOrder(self.plc_combo, self.lang_combo)
        self.setTabOrder(self.lang_combo, self.input_edit)
        self.setTabOrder(self.input_edit, self.compile_btn)
        self._refresh_plc_target_labels()

    def start_compile(self):
        user_input = self.input_edit.toPlainText().strip()
        if not user_input:
            QMessageBox.warning(self, tr('警告'), tr('错误：请输入您的工业控制需求'))
            return
        if not self._api_key_available():
            self.open_api_config_dialog()
            if not self._api_key_available():
                return

        current_effort = None
        target_mode = "ladder" if self.lang_combo.currentIndex() == 0 else "st"
        plc_model = self._current_plc_model()
        previous_json = (
            copy.deepcopy(self._last_ladder_json)
            if target_mode == "ladder"
            and self._last_target_mode == "ladder"
            and isinstance(self._last_ladder_json, dict)
            else None
        )

        # ---- 保存上下文供后续使用 ----
        self._pending_effort = current_effort
        self._pending_target_mode = target_mode
        self._pending_user_input = user_input
        self._pending_original_request = user_input
        self._pending_previous_json = previous_json
        self._pending_plc_model = plc_model
        self._active_task_id = uuid.uuid4().hex

        # ---- 每次编译都先跑阶段1分析（含多轮编辑） ----
        self.compile_btn.setEnabled(False)
        self.compile_btn.setText(tr('正在分析需求...'))
        self.result_status.setText(tr('需求分析中'))

        analysis_input = tr('目标 PLC 型号：{v0}\n{v1}', v0=plc_model, v1=user_input)
        self._analysis_thread = AnalysisThread(
            self._active_task_id,
            analysis_input,
            confirmed_context=self._last_confirmed_spec,
            task_type="edit" if previous_json is not None else "generate",
        )
        self._analysis_thread.analysis_done.connect(self._on_analysis_done)
        self._analysis_thread.analysis_failed.connect(self._on_analysis_failed)
        self._analysis_thread.thinking_updated.connect(
            lambda task_id, token: (
                self.thinking_panel.append_reasoning(token)
                if task_id == self._active_task_id
                else None
            )
        )
        self._analysis_thread.content_updated.connect(
            lambda task_id, token: (
                self.thinking_panel.append_content(token)
                if task_id == self._active_task_id
                else None
            )
        )
        self.thinking_panel.reset()
        self.thinking_panel.setVisible(True)
        self._analysis_thread.start()

    def _on_analysis_done(self, task_id, analysis_json):
        """阶段1完成 → 弹出确认对话框"""
        if task_id != self._active_task_id:
            return
        self.compile_btn.setEnabled(True)
        self.compile_btn.setText(tr('分析并生成'))
        self.result_status.setText(tr('等待确认'))
        self._analysis_thread = None
        analysis_json = dict(analysis_json or {})
        analysis_json["plc_model"] = self._pending_plc_model

        # 自动填充 SFC 流程图（用户可手动切换到流程图模式查看）
        fc_steps = analysis_json.get("flowchart_steps", [])
        if fc_steps:
            self.sfc_editor.populate_flowchart(fc_steps)

        dialog = SimpleRequirementConfirmDialog(
            analysis_json,
            self._pending_original_request,
            plc_model=self._pending_plc_model,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            confirmed = dialog.get_confirmed_spec()
            if confirmed is None:
                return
            confirmed = canonicalize_confirmed_spec(confirmed)
            self._last_confirmed_spec = copy.deepcopy(confirmed)

            self._launch_compiler(
                self._pending_user_input,
                self._pending_effort,
                self._pending_target_mode,
                self._pending_previous_json,
                confirmed,
            )
        else:
            self.result_status.setText(tr('等待修改'))

    def _on_analysis_failed(self, task_id, err_msg):
        """阶段1失败 → 回退到直接编译"""
        if task_id != self._active_task_id:
            return
        self.compile_btn.setEnabled(True)
        self.compile_btn.setText(tr('分析并生成'))
        self.result_status.setText(tr('切换生成模式'))
        self._analysis_thread = None
        print(tr('需求分析失败，跳过确认直接编译: {v0}', v0=err_msg))
        self.thinking_panel.show_error(
            tr('需求确认暂不可用，将按所选 {v0} 直接生成并执行硬校验：{v1}', v0=self._pending_plc_model, v1=err_msg)
        )
        self._launch_compiler(
            self._pending_user_input,
            self._pending_effort,
            self._pending_target_mode,
            self._pending_previous_json,
            self._last_confirmed_spec,
        )

    def _build_confirmed_context(self, confirmed):
        """构建注入到用户输入中的确认信息文本。I/O 分配放在末尾以对抗注意力衰减。"""
        parts = []
        # 注入 PLC 型号上下文
        model = _detect_plc_model(self._pending_user_input)
        parts.append(_build_model_context(model))

        summary = confirmed.get("summary", "")
        if summary:
            parts.append(tr('确认后的需求摘要: {v0}', v0=summary))

        # 用户选的方案
        app = confirmed.get("selected_approach")
        if app:
            parts.append(tr('方案: {v0}——{v1}', v0=app.get('name', ''), v1=app.get('description', '')))
            generation_guide = app.get("generation_guide", "")
            if generation_guide:
                parts.append(tr('方案生成要点: {v0}', v0=generation_guide))

        # 用户补充说明
        notes = confirmed.get("user_notes", "")
        if notes:
            parts.append(tr('用户补充: {v0}', v0=notes))

        answers = confirmed.get("missing_answers", {})
        if answers:
            for q, a in answers.items():
                parts.append(f"{q}: {a}")

        # ---- I/O 分配放在末尾，对抗注意力衰减 ----
        io_raw = confirmed.get("io_allocation_raw", "")
        if io_raw:
            parts.append('\n【软元件分配——整个程序必须一致使用，device_comments 与 rungs 中的地址必须完全匹配】\n{v0}'.format(v0=io_raw))

        return "\n".join(parts)

    def _launch_compiler(
        self,
        user_input,
        effort,
        target_mode,
        previous_json,
        confirmed_spec=None,
    ):
        """启动 CompilerThread（阶段3 或跳过分析的直接编译）"""
        self.compile_btn.setEnabled(False)
        self.compile_btn.setText(tr('正在生成程序...'))
        self.result_status.setText(tr('程序生成中'))
        print(tr('准备编译，当前思考模式为: {v0}，目标语言: {v1}', v0=effort, v1=target_mode))
        task_id = self._active_task_id or uuid.uuid4().hex
        self._active_task_id = task_id
        self._active_output_dir = Path.cwd() / "generated_output"
        self._active_output_dir.mkdir(parents=True, exist_ok=True)

        # ---- 清理旧线程的信号连接 ----
        if self._compiler_thread is not None:
            old = self._compiler_thread
            for sig in [old.success, old.failure,
                        old.thinking_updated, old.content_updated,
                        old.progress_updated]:
                try:
                    sig.disconnect()
                except (TypeError, RuntimeError):
                    pass

        self._compiler_thread = CompilerThread(
            task_id,
            user_input,
            effort,
            target_mode,
            self._active_output_dir,
            previous_json=previous_json,
            confirmed_context=confirmed_spec,
            task_type="edit" if previous_json is not None else "generate",
            current_version_json=previous_json,
            plc_model=self._pending_plc_model,
            program_name="MAIN",
            revision=1,
            requirement_text=user_input,
        )

        # ---- 连接流式信号 ----
        self._compiler_thread.thinking_updated.connect(
            lambda emitted_task_id, token: (
                self.thinking_panel.append_reasoning(token)
                if emitted_task_id == self._active_task_id
                else None
            )
        )
        self._compiler_thread.content_updated.connect(
            lambda emitted_task_id, token: (
                self.thinking_panel.append_content(token)
                if emitted_task_id == self._active_task_id
                else None
            )
        )
        self._compiler_thread.progress_updated.connect(
            self._on_compile_progress
        )

        # ---- 连接结果信号 ----
        self._compiler_thread.success.connect(self.on_compile_success)
        self._compiler_thread.failure.connect(self.on_compile_failure)

        # ---- 初始化思考面板 ----
        self.thinking_panel.reset()

        self._compiler_thread.start()

    def _on_compile_progress(self, task_id, payload):
        if task_id != self._active_task_id:
            return
        if isinstance(payload, dict):
            stage = str(payload.get("stage", ""))
            message = str(payload.get("message", "")).strip()
            severity = str(payload.get("severity", ""))
        else:
            stage = str(payload)
            message = ""
            severity = ""
        labels = {
            "connecting": tr('连接模型'),
            "parsing": tr('解析输出'),
            "parsed": tr('解析完成'),
            "fallback": tr('切换普通调用'),
            "repairing": tr('自动修正'),
            "repairing_remote": tr('请求模型修正'),
            "repaired_local": tr('本地修正完成'),
        }
        label = labels.get(stage, message or tr('生成中'))
        self.result_status.setText(label)
        self.thinking_panel.set_status(label)
        if message and severity == "warning":
            self.thinking_panel.show_error(message)
        elif message and stage in {"parsing", "parsed"}:
            self.thinking_panel.append_content(f"\n[{message}]\n")

    def _on_stream_status(self, status: str):
        """处理流式调用的状态变化。"""
        if status == "connecting":
            self.thinking_panel.set_status(tr('连接中...'))
            self.result_status.setText(tr('连接模型'))
        elif status == "repairing":
            self.thinking_panel.set_status(tr('自动修复中'))
            self.result_status.setText(tr('自动修复中'))
        elif status == "done":
            self.thinking_panel.set_status(tr('解析输出中'))
            self.result_status.setText(tr('解析输出中'))
        elif status.startswith("error:"):
            self.thinking_panel.show_error(tr('流式调用失败，已降级至普通模式: {v0}', v0=status[6:].strip()))
            self.thinking_panel.set_status(tr('降级模式'))
            self.result_status.setText(tr('降级生成'))

    def on_compile_failure(self, task_id, err_msg):
        if task_id != self._active_task_id:
            return
        self.compile_btn.setEnabled(True)
        self.compile_btn.setText(tr('分析并生成'))
        self.thinking_panel.set_status(tr('编译失败'))
        self.result_status.setText(tr('生成失败'))
        self.thinking_panel.show_error(err_msg)
        QMessageBox.critical(self, tr('编译错误'), err_msg)
    
    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self.max_btn.setText("□")
            self.max_btn.setToolTip(tr('最大化'))
        else:
            self.showMaximized()
            self.max_btn.setText("❐")
            self.max_btn.setToolTip(tr('还原'))

    def notify_csv_export(self):
        if os.path.exists("plc_import_program.csv"):
            QMessageBox.information(self, tr('导出成功'), tr('GX Works2 兼容的程序明细表已生成！\n文件名：plc_import_program.csv\n\n您可以在 GX Works2 中点击【工程】->【打开其他格式文件】->【导入 Excel 语句表】直接引入此程序逻辑。'))
        else:
            QMessageBox.warning(self, tr('提示'), tr('请先输入需求并完成【编译】后再尝试导出！'))
    def on_compile_success(self, task_id, result):
        """线程执行成功后的刷新与展示槽函数"""
        if task_id != self._active_task_id:
            return
        self.compile_btn.setEnabled(True)
        self.compile_btn.setText(tr('分析并生成'))
        self._last_result = dict(result or {})
        contract_mismatch = self._last_result.get("contract_mismatch")
        if contract_mismatch:
            self.thinking_panel.set_status(tr('方案约束待处理'))
            self.result_status.setText(tr('CSV 已生成 · 方案约束待处理'))
        else:
            self.thinking_panel.set_status(tr('已完成'))
            self.result_status.setText(tr('生成完成 · 校验通过'))
        self._last_target_mode = self._last_result.get("target_mode")
        try:
            artifacts = self._last_result.get("artifacts", {})
            if self._last_target_mode == "ladder":
                output_path = self._active_output_dir / artifacts.get("svg", "")
                json_path = self._active_output_dir / artifacts.get("json", "")
                if json_path.is_file():
                    with json_path.open("r", encoding="utf-8") as stream:
                        self._last_ladder_json = json.load(stream)
                self.display_container.setCurrentIndex(0)
                safe_width = int(float(self._last_result.get("width", 1100)))
                safe_height = int(float(self._last_result.get("height", 700)))
                if safe_height > 30000:
                    safe_height = 30000
                self.svg_viewer.setFixedSize(safe_width, safe_height)
                self.svg_viewer.load(str(output_path))
                gx_tool = (
                    tr('GX Works3 目标梯形图')
                    if self._current_plc_model() == "FX5U"
                    else tr('梯形图与 GX Works2 语句表')
                )
                if contract_mismatch:
                    issues = contract_mismatch.get("issues") or [
                        contract_mismatch.get("message", tr('方案约束未满足'))
                    ]
                    issue_text = "；".join(str(item) for item in issues if item)
                    program_csv_path = self._active_output_dir / artifacts.get(
                        "program_csv", "program.csv"
                    )
                    answer = QMessageBox.question(
                        self,
                        tr('CSV 已生成，方案约束待处理'),
                        (
                            tr('原始程序已经生成，CSV 不会因为方案约束问题被丢弃。\nCSV：{v0}\n\n你可以先切换到 GX Works2 导入并检查这个版本。\n\n未满足的方案约束：{v1}\n\n是否让 AI 基于当前结果进行修复？', v0=program_csv_path, v1=issue_text)
                        ),
                        QMessageBox.StandardButton.Yes
                        | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if (
                        answer == QMessageBox.StandardButton.Yes
                        and self._last_ladder_json is not None
                        and self._last_confirmed_spec is not None
                    ):
                        repair_request = (
                            tr('基于当前已生成梯形图，仅修复以下已确认方案约束问题：{v0}。保持其余逻辑、I/O、参数和已选方案不变；不得重新分析需求或更换实现方案。', v0=issue_text)
                        )
                        previous = copy.deepcopy(self._last_ladder_json)
                        confirmed = copy.deepcopy(self._last_confirmed_spec)
                        QTimer.singleShot(
                            0,
                            lambda req=repair_request, prev=previous, spec=confirmed: (
                                self._launch_compiler(
                                    req, "high", "ladder", prev, spec
                                )
                            ),
                        )
                else:
                    QMessageBox.information(
                        self,
                        tr('成功'),
                        tr('{v0}生成成功，结构和型号硬校验已通过！', v0=gx_tool),
                    )
            elif self._last_target_mode == "st":
                output_path = self._active_output_dir / artifacts.get("st", "")
                self.display_container.setCurrentIndex(1)
                if output_path.is_file():
                    with output_path.open("r", encoding="utf-8") as f:
                        self.st_viewer.setPlainText(f.read())
                else:
                    self.st_viewer.setPlainText(str(output_path))
                QMessageBox.information(self, tr('成功'), tr('ST 结构化文本编译成功！'))
        except Exception as e:
            QMessageBox.critical(
                self,
                tr('错误'),
                tr('界面渲染失败: {v0}\n(注: 后端文件已正常生成，不影响导入)', v0=naturalize_display_text(e)),
            )
    def open_api_config_dialog(self):
        """打开 API 请求格式配置对话框"""
        from ui.desktop.dialogs.config import RequestTemplateConfigDialog
        dialog = RequestTemplateConfigDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                reload_model_provider()
            except Exception as e:
                QMessageBox.critical(
                    self,
                    tr('错误'),
                    tr('重新加载模型服务失败:\n{v0}', v0=naturalize_display_text(e)),
                )
                return
            QMessageBox.information(self, tr('成功'), tr('API 配置已更新并生效。'))

    @staticmethod
    def _api_key_available():
        try:
            return bool(get_api_key(load_full_config()))
        except Exception:
            return False

    def _ensure_api_configured(self, initial_setup=False):
        if self._api_key_available():
            return True
        from ui.desktop.dialogs.config import RequestTemplateConfigDialog

        dialog = RequestTemplateConfigDialog(
            self,
            initial_setup=initial_setup,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            reload_model_provider()
        except Exception as error:
            QMessageBox.critical(
                self,
                tr('API 配置错误'),
                tr('重新加载失败：\n{v0}', v0=naturalize_display_text(error)),
            )
            return False
        return self._api_key_available()

    def clear_chat_data(self):
        self.input_edit.clear()
        self.st_viewer.clear()
        self._last_ladder_json = None
        self._last_confirmed_spec = None
        self._last_result = None
        self._last_target_mode = None
        self._active_output_dir = None
        self._active_task_id = None

        self.svg_viewer.load(bytearray(b''))

        self.thinking_panel.content_edit.clear()
        self.thinking_panel.set_status(tr('等待中'))
        self.thinking_panel._collapse()
        self.result_status.setText(tr('等待生成'))

        chat_file = "chat_history.json"
        if os.path.exists(chat_file):
            try:
                with open(chat_file, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=4)
            except Exception as e:
                QMessageBox.warning(
                    self,
                    tr('警告'),
                    tr('清理本地历史记录失败: {v0}', v0=naturalize_display_text(e)),
                )
                return

        confirmed_file = "confirmed_requirements.json"
        if os.path.exists(confirmed_file):
            try:
                os.remove(confirmed_file)
            except Exception as e:
                QMessageBox.warning(
                    self,
                    tr('警告'),
                    tr('清理确认规格失败: {v0}', v0=naturalize_display_text(e)),
                )
                return

        QMessageBox.information(self, tr('成功'), tr('已开启新对话，历史记录已清空！'))
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 36:
            self._is_tracking = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and getattr(self, '_is_tracking', False):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_tracking = False
            event.accept()

