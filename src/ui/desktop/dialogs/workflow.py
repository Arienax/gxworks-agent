"""Workflow."""
from shared.i18n import tr
import json
import copy
from ui.desktop.qt import QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel, QScrollArea, QFrame, QComboBox, QDialog, QButtonGroup, QRadioButton, QGroupBox, QPlainTextEdit
from ui.desktop.qt import Qt
from ui.desktop.qt import QColor, QFont, QPainter
from ui.desktop.workbench import RequirementReviewCard
from ui.desktop.theme import ThemeMode, get_theme_manager, normalize_theme, theme_tokens
from ui.desktop.chrome import DialogTitleBar, WINDOW_CHROME_QSS, window_chrome_qss
from ui.desktop.icons import codicon, codicon_font, set_codicon
from plc.specification.confirmed import canonicalize_confirmed_spec
from shared.display_names import naturalize_display_text

class GXWorks2SyncErrorDialog(QDialog):
    """Structured, expandable diagnostics for a failed read-side sync."""

    STAGE_LABELS = {
        "validate_local": tr('当前项目CSV校验'),
        "check_gxworks2": tr('检查GX Works2'),
        "retry_check_gxworks2": tr('重试前检查GX Works2'),
        "check_project": tr('检查GX Works2工程'),
        "check_program": tr('检查MAIN程序'),
        "inspect_project": tr('检查GX Works2工程状态'),
        "retry_inspect_project": tr('重试前检查工程状态'),
        "activate_main": tr('激活MAIN程序'),
        "activate_comments": tr('打开软元件注释'),
        "open_export_menu": tr('打开“写入至CSV文件”'),
        "wait_program_file_dialog": tr('等待程序文件选择窗口'),
        "wait_comment_file_dialog": tr('等待注释文件选择窗口'),
        "submit_program_export_path": tr('提交程序CSV导出路径'),
        "submit_comment_export_path": tr('提交注释CSV导出路径'),
        "wait_program_export_file": tr('等待程序CSV生成'),
        "wait_comment_export_file": tr('等待注释CSV生成'),
        "export_program": tr('程序CSV导出'),
        "validate_program_csv": tr('校验程序CSV'),
        "export_comments": tr('注释CSV导出'),
        "validate_comment_csv": tr('校验注释CSV'),
        "write_manifest": tr('保存导出校验清单'),
        "resolve_baseline": tr('确定同步基线'),
        "compare": tr('读取并比较同步基线'),
        "save_baseline": tr('保存同步基线'),
        "unexpected": tr('同步服务内部处理'),
    }

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.result = result
        self.retry_requested = False
        self.setWindowTitle(tr('GX Works2操作未完成'))
        self.setModal(True)
        self.setMinimumWidth(560)
        dialog_font = QFont("Microsoft YaHei")
        dialog_font.setPointSize(10)
        self.setFont(dialog_font)

        details = dict(getattr(result, "details", {}) or {})
        stage = str(getattr(result, "stage", "") or details.get("stage") or "")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        headline = QLabel("⚠ " + self._headline(result, details))
        headline.setObjectName("GXSyncErrorHeadline")
        headline.setStyleSheet("font-size: 17px; font-weight: 600;")
        layout.addWidget(headline)

        stage_label = QLabel(
            tr('阶段\n') + self.STAGE_LABELS.get(stage, stage or tr('未知阶段'))
        )
        stage_label.setWordWrap(True)
        layout.addWidget(stage_label)

        reason = naturalize_display_text(getattr(result, "message", ""))
        if reason:
            reason_label = QLabel(tr('原因\n') + reason)
            reason_label.setWordWrap(True)
            layout.addWidget(reason_label)

        checks_label = QLabel(tr('检测结果\n') + self._checks_text(details, stage))
        checks_label.setWordWrap(True)
        layout.addWidget(checks_label)

        suggestion = naturalize_display_text(
            details.get("suggestion") or tr('请查看技术详情后重试。')
        )
        suggestion_label = QLabel(tr('建议\n') + suggestion)
        suggestion_label.setWordWrap(True)
        layout.addWidget(suggestion_label)

        self.details_editor = QPlainTextEdit(self)
        self.details_editor.setReadOnly(True)
        self.details_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.details_editor.setPlainText(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str)
        )
        self.details_editor.setMinimumHeight(220)
        self.details_editor.setVisible(False)
        layout.addWidget(self.details_editor)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        if bool(getattr(result, "retryable", False)):
            self.retry_button = QPushButton(tr('重试'), self)
            self.retry_button.clicked.connect(self._accept_retry)
            buttons.addWidget(self.retry_button)
        else:
            self.retry_button = None
        self.details_button = QPushButton(tr('查看技术详情'), self)
        self.details_button.clicked.connect(self._toggle_details)
        buttons.addWidget(self.details_button)
        cancel_button = QPushButton(tr('取消'), self)
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

    @staticmethod
    def _status_line(label, value):
        if value is True:
            marker = "✓"
        elif value is False:
            marker = "✕"
        else:
            marker = "—"
        return f"{marker} {label}"

    @classmethod
    def _checks_text(cls, details, stage):
        lines = [
            cls._status_line(
                tr('GX Works2正在运行'),
                details.get("gx_running"),
            ),
            cls._status_line(tr('工程已打开'), details.get("project_open")),
            cls._status_line(tr('MAIN已打开'), details.get("program_ready")),
        ]
        comment_stage = (
            "comment" in stage or details.get("operation") == "comment_export"
        )
        if stage in {"write_manifest", "compare", "save_baseline"}:
            lines.append(cls._status_line(tr('程序CSV已导出并校验'), True))
            lines.append(cls._status_line(tr('注释CSV已导出并校验'), True))
        elif comment_stage:
            lines.append(cls._status_line(tr('程序CSV已导出并校验'), True))
            lines.append(cls._status_line(tr('注释CSV已导出并校验'), False))
        elif any(
            token in stage
            for token in ("program", "export_menu", "file_dialog", "activate_main")
        ):
            lines.append(cls._status_line(tr('程序CSV已导出并校验'), False))
        return "\n".join(lines)

    @staticmethod
    def _headline(result, details):
        stage = str(getattr(result, "stage", "") or details.get("stage") or "")
        if "comment" in stage or details.get("operation") == "comment_export":
            return tr('读取软元件注释失败')
        if any(
            token in stage
            for token in ("program", "export_menu", "file_dialog", "activate_main")
        ):
            return tr('读取MAIN失败')
        return naturalize_display_text(result.message) or tr('GX Works2同步未完成')

    def _toggle_details(self):
        visible = self.details_editor.isHidden()
        self.details_editor.setVisible(visible)
        self.details_button.setText(tr('收起技术详情') if visible else tr('查看技术详情'))
        self.adjustSize()

    def _accept_retry(self):
        self.retry_requested = True
        self.accept()


class ArrowCombo(QComboBox):
    """带方向箭头的下拉框——收起▼ 展开▲"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._popped = False

    def showPopup(self):
        self._popped = True
        self.update()
        super().showPopup()

    def hidePopup(self):
        self._popped = False
        self.update()
        super().hidePopup()

    def paintEvent(self, e):
        super().paintEvent(e)
        arrow = "▲" if self._popped else "▼"
        p = QPainter(self)
        p.setPen(QColor("#64748b"))
        r = self.rect()
        p.drawText(r.adjusted(0, 0, -10, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, arrow)
        p.end()


class RequirementConfirmDialog(QDialog):
    """展示 AI 分析结果，让用户选择指令、补充缺失信息、确认软元件分配"""

    def __init__(self, analysis_json, parent=None):
        super().__init__(parent)
        self.analysis = analysis_json
        self.confirmed_spec = None  # 用户确认后的规格
        self.setWindowTitle(tr('AI 理解确认与补充'))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setMinimumSize(620, 560)
        self.resize(720, 680)
        self.setModal(True)
        self._init_ui()
        self._apply_styles()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # ---- 标题 ----
        title = QLabel(tr('AI 需求分析确认'))
        title.setStyleSheet("color:#0f172a;font-size:18px;font-weight:700;")
        layout.addWidget(title)

        # ---- AI 理解摘要 ----
        summary = naturalize_display_text(
            self.analysis.get("summary", tr('(无法解析)'))
        )
        summary_label = QLabel(tr('AI 理解：{v0}', v0=summary))
        summary_label.setWordWrap(True)
        summary_label.setStyleSheet(
            "color:#334155;background:#ecfdf5;border:1px solid #a7f3d0;"
            "border-radius:8px;font-size:13px;font-weight:600;padding:9px;"
        )
        layout.addWidget(summary_label)

        # ---- 实现方案选择 ----
        approaches = self.analysis.get("approaches", [])
        self.approach_radios = {}
        self.custom_approach_edit = None
        if approaches:
            inst_group = QGroupBox(tr('选择编程方案'))
            inst_layout = QVBoxLayout(inst_group)
            self.approach_group = QButtonGroup(self)
            for i, app in enumerate(approaches):
                approach_name = naturalize_display_text(app.get("name", ""))
                approach_description = naturalize_display_text(
                    app.get("description", "")
                )
                text = f"{approach_name} — {approach_description}".rstrip(" —")
                if app.get('pros'):
                    text += tr('（优点: {v0}', v0=naturalize_display_text(app['pros']))
                    if app.get('cons'):
                        text += tr('，缺点: {v0}', v0=naturalize_display_text(app['cons']))
                    text += "）"
                rb = QRadioButton(text)
                rb.setStyleSheet("font-size:12px;color:#334155;padding:4px 0;")
                if i == 0:
                    rb.setChecked(True)
                self.approach_group.addButton(rb, i)
                self.approach_radios[i] = app
                inst_layout.addWidget(rb)
            # 自定义方案
            custom_rb = QRadioButton(tr('自定义方案'))
            custom_rb.setStyleSheet("font-size:12px;color:#475569;padding:4px 0;")
            self.approach_group.addButton(custom_rb, len(approaches))
            self.approach_radios[len(approaches)] = {"name":tr('自定义'),"description":"","generation_guide":""}
            inst_layout.addWidget(custom_rb)
            # 自定义输入框（选中时显示）
            self.custom_approach_edit = QTextEdit()
            self.custom_approach_edit.setPlaceholderText(tr('在此描述你自己的实现方案...'))
            self.custom_approach_edit.setMaximumHeight(60)
            self.custom_approach_edit.setStyleSheet("font-size:12px;")
            self.custom_approach_edit.setVisible(False)
            custom_rb.toggled.connect(lambda checked, e=self.custom_approach_edit: e.setVisible(checked))
            inst_layout.addWidget(self.custom_approach_edit)
            layout.addWidget(inst_group)
        else:
            self.approach_group = None

        # ---- 缺失信息补充 ----
        missing = self.analysis.get("missing_info", [])
        self.missing_widgets = {}
        if missing:
            missing_group = QGroupBox(tr('需要补充的信息'))
            missing_layout = QVBoxLayout(missing_group)
            for item in missing:
                row = QHBoxLayout()
                q = QLabel(naturalize_display_text(item["question"]))
                q.setStyleSheet("font-size:12px;color:#334155;")
                q.setMinimumWidth(180)
                row.addWidget(q)
                combo = ArrowCombo()
                combo.setEditable(True)  # 允许手动输入，不限于 AI 选项
                options = [str(option) for option in item.get("options", [])]
                for option in options:
                    combo.addItem(naturalize_display_text(option), option)
                default = str(item.get("default", "") or "")
                if default:
                    default_index = next(
                        (
                            index
                            for index in range(combo.count())
                            if str(combo.itemData(index) or "") == default
                        ),
                        -1,
                    )
                    if default_index >= 0:
                        combo.setCurrentIndex(default_index)
                    else:
                        combo.setCurrentText(naturalize_display_text(default))
                row.addWidget(combo, stretch=1)
                self.missing_widgets[item["question"]] = combo
                missing_layout.addLayout(row)
            layout.addWidget(missing_group)

        # ---- 软元件分配 ----
        io = self.analysis.get("suggested_io", {})
        if io:
            io_group = QGroupBox(tr('建议软元件分配（可编辑）'))
            io_layout = QVBoxLayout(io_group)
            io_text = self._format_io(io)
            self.io_edit = QTextEdit()
            self.io_edit.setPlainText(io_text)
            self.io_edit.setMaximumHeight(120)
            self.io_edit.setStyleSheet("font-family:Consolas;font-size:12px;")
            io_layout.addWidget(self.io_edit)
            layout.addWidget(io_group)
        else:
            self.io_edit = None

        # ---- 补充说明（自由输入） ----
        notes_group = QGroupBox(tr('补充说明（可选，会直接注入生成指令）'))
        notes_layout = QVBoxLayout(notes_group)
        self.user_notes = QTextEdit()
        self.user_notes.setPlaceholderText(tr('在此输入你对梯形图结构的额外要求、偏好或修正意见...'))
        self.user_notes.setMaximumHeight(80)
        self.user_notes.setStyleSheet("font-size:12px;")
        notes_layout.addWidget(self.user_notes)
        layout.addWidget(notes_group)

        # ---- 按钮 ----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.back_btn = QPushButton(tr('返回修改需求'))
        self.back_btn.setObjectName("CancelBtn")
        self.back_btn.clicked.connect(self._on_back)
        self.confirm_btn = QPushButton(tr('确认并生成'))
        self.confirm_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(self.back_btn)
        btn_layout.addWidget(self.confirm_btn)
        layout.addLayout(btn_layout)

    def _format_io(self, io):
        lines = []
        for category in ["X", "Y", "M", "T", "C", "D"]:
            items = io.get(category, {})
            if items:
                if isinstance(items, dict):
                    parts = [
                        f"{k}={naturalize_display_text(v)}"
                        for k, v in items.items()
                    ]
                    lines.append(f"{category}: {', '.join(parts)}")
                elif isinstance(items, list):
                    lines.append(f"{category}: {', '.join(str(i) for i in items)}")
        special = io.get("special_relays", [])
        if special:
            lines.append(tr('特殊M: {v0}', v0=', '.join(special)))
        return "\n".join(lines)

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background-color:#f8fafc; }
            QLabel { color:#334155; font-family:"Segoe UI","Microsoft YaHei"; font-size:13px; }
            QGroupBox {
                color:#0f172a; font-size:13px; font-weight:600;
                background-color:#ffffff; border:1px solid #cbd5e1;
                border-radius:9px; margin-top:10px; padding-top:18px;
            }
            QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 7px; }
            QPushButton {
                min-height:38px; padding:0 18px; color:#ffffff;
                background-color:#0f766e; border:1px solid #0f766e;
                border-radius:8px; font-size:13px; font-weight:600;
            }
            QPushButton:hover { background-color:#0d9488; border-color:#0d9488; }
            QPushButton#CancelBtn { background-color:#ffffff;color:#475569;border:1px solid #cbd5e1; }
            QPushButton#CancelBtn:hover { background-color:#f1f5f9;border-color:#94a3b8; }
            QComboBox {
                min-height:34px; color:#0f172a; background-color:#ffffff;
                border:1px solid #cbd5e1; border-radius:7px;
                padding:0 28px 0 9px; font-size:12px;
            }
            QComboBox:focus, QTextEdit:focus { border:2px solid #0f766e; }
            QComboBox::drop-down { subcontrol-origin:padding;subcontrol-position:top right;width:22px;border:none; }
            QComboBox::down-arrow { width:10px;height:10px; }
            QComboBox QAbstractItemView {
                background-color:#ffffff; color:#0f172a; border:1px solid #cbd5e1;
                padding:4px; selection-background-color:#0f766e;
                selection-color:#ffffff; outline:none;
            }
            QTextEdit {
                color:#0f172a; background-color:#ffffff; border:1px solid #cbd5e1;
                border-radius:7px; padding:8px; selection-background-color:#99f6e4;
            }
            QRadioButton { color:#334155; spacing:7px; }
            QRadioButton::indicator { width:16px; height:16px; }
        """)

    def _on_confirm(self):
        """收集用户选择，构建 confirmed_spec"""
        spec = {
            "summary": self.analysis.get("summary", ""),
            "selected_approach": None,
            "missing_answers": {},
            "io_allocation": {},
        }

        # 选中的方案
        if self.approach_group:
            checked = self.approach_group.checkedId()
            if checked >= 0 and checked in self.approach_radios:
                app = dict(self.approach_radios[checked])  # copy
                # 自定义方案：用用户输入的内容
                if app.get("name") == tr('自定义') and self.custom_approach_edit:
                    custom_text = self.custom_approach_edit.toPlainText().strip()
                    if custom_text:
                        app["description"] = custom_text
                        app["generation_guide"] = custom_text
                spec["selected_approach"] = app

        # 用户补充说明
        spec["user_notes"] = self.user_notes.toPlainText().strip() if hasattr(self, 'user_notes') else ""

        # 缺失信息回答
        for question, combo in self.missing_widgets.items():
            index = combo.currentIndex()
            raw_value = combo.itemData(index) if index >= 0 else None
            # Editable combos may contain free-form user input.  Only map back
            # to the stable option value while the visible text still matches
            # the selected option; otherwise preserve what the user typed.
            if (
                raw_value is not None
                and index >= 0
                and combo.currentText() == combo.itemText(index)
            ):
                value = str(raw_value)
            else:
                value = combo.currentText()
            spec["missing_answers"][question] = value

        # 软元件分配
        if self.io_edit:
            spec["io_allocation_raw"] = self.io_edit.toPlainText().strip()

        self.confirmed_spec = spec
        self.accept()

    def _on_back(self):
        self.confirmed_spec = None
        self.reject()

    def get_confirmed_spec(self):
        return self.confirmed_spec


class SimpleRequirementConfirmDialog(QDialog):
    """Original-style modal shell backed by the current confirmed_spec v3 core."""

    def __init__(
        self,
        analysis,
        original_request,
        *,
        plc_model="FX3U",
        parent=None,
    ):
        super().__init__(parent)
        self._confirmed_spec = None
        self.setWindowTitle(tr('生成前确认'))
        self.setMinimumSize(760, 620)
        self.resize(880, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self.review_card = RequirementReviewCard(
            analysis,
            original_request,
            previous_spec=None,
            plc_model=plc_model,
        )
        self.review_card.apply_theme(ThemeMode.LIGHT)
        self.review_card.confirmed.connect(self._accept_spec)
        self.review_card.revise_requested.connect(lambda _text: self.reject())
        scroll.setWidget(self.review_card)
        layout.addWidget(scroll)

    def _accept_spec(self, spec):
        self._confirmed_spec = canonicalize_confirmed_spec(spec)
        self.accept()

    def get_confirmed_spec(self):
        return copy.deepcopy(self._confirmed_spec)


class WorkbenchConfirmDialog(QDialog):
    """Compact destructive-action dialog matching the workbench chrome."""

    def __init__(
        self,
        title,
        message,
        confirm_text=tr('确认'),
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.setModal(True)
        self.setFixedWidth(460)
        self.setStyleSheet(
            """
            QDialog { background: transparent; }
            QFrame#ConfirmDialogFrame {
                background: #1f1f1f;
                border: 1px solid #454545;
            }
            QFrame#ConfirmDialogBody { border: none; background: #1f1f1f; }
            QLabel#ConfirmWarningIcon {
                color: #f48771;
                background: transparent;
            }
            QLabel#ConfirmMessage {
                color: #d4d4d4;
                background: transparent;
                font-size: 13px;
            }
            QLabel#ConfirmHint {
                color: #9d9d9d;
                background: transparent;
                font-size: 11px;
            }
            QPushButton {
                min-width: 82px;
                min-height: 30px;
                padding: 0 12px;
                color: #cccccc;
                background: #313131;
                border: 1px solid #3c3c3c;
                border-radius: 2px;
            }
            QPushButton:hover {
                color: #ffffff;
                background: #3c3c3c;
                border-color: #5a5a5a;
            }
            QPushButton:focus { border-color: #0078d4; }
            QPushButton#DangerButton {
                color: #ffffff;
                background: #c42b1c;
                border-color: #c42b1c;
            }
            QPushButton#DangerButton:hover {
                background: #d13438;
                border-color: #d13438;
            }
            """
            + WINDOW_CHROME_QSS
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("ConfirmDialogFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        self.title_bar = DialogTitleBar(
            self,
            title,
            icon_name="warning",
            allow_minimize=False,
            allow_maximize=False,
        )
        frame_layout.addWidget(self.title_bar)

        body = QFrame()
        body.setObjectName("ConfirmDialogBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 18, 20, 16)
        body_layout.setSpacing(14)

        message_row = QHBoxLayout()
        message_row.setSpacing(12)
        warning_icon = QLabel(codicon("warning"))
        warning_icon.setObjectName("ConfirmWarningIcon")
        warning_icon.setFont(codicon_font(22))
        warning_icon.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )
        warning_icon.setFixedWidth(28)
        self.message_label = QLabel(message)
        self.message_label.setObjectName("ConfirmMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        message_row.addWidget(warning_icon)
        message_row.addWidget(self.message_label, 1)
        body_layout.addLayout(message_row)

        hint = QLabel(tr('此操作无法撤销。'))
        hint.setObjectName("ConfirmHint")
        body_layout.addWidget(hint)

        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_button = QPushButton(tr('取消'))
        self.confirm_button = QPushButton(confirm_text)
        self.confirm_button.setObjectName("DangerButton")
        set_codicon(self.cancel_button, "close", tr('取消'), 10)
        set_codicon(self.confirm_button, "trash", confirm_text, 10)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.confirm_button)
        body_layout.addLayout(actions)

        frame_layout.addWidget(body)
        outer.addWidget(frame)
        self.apply_theme(get_theme_manager().current_theme)

    def apply_theme(self, mode):
        selected = normalize_theme(mode)
        colors = theme_tokens(selected)
        self.setStyleSheet("""
            QDialog { background: transparent; }
            QFrame#ConfirmDialogFrame { background: %(surface)s; border: 1px solid %(border)s; }
            QFrame#ConfirmDialogBody { border: none; background: %(surface)s; }
            QLabel#ConfirmWarningIcon { color: #c42b1c; background: transparent; }
            QLabel#ConfirmMessage { color: %(text)s; background: transparent; font-size: 13px; }
            QLabel#ConfirmHint { color: %(text_muted)s; background: transparent; font-size: 11px; }
            QPushButton { min-width: 82px; min-height: 30px; padding: 0 12px; color: %(text)s; background: %(surface_alt)s; border: 1px solid %(border)s; border-radius: 2px; }
            QPushButton:hover { color: %(text_strong)s; background: %(hover)s; }
            QPushButton:focus { border-color: %(accent)s; }
            QPushButton#DangerButton { color: #ffffff; background: #c42b1c; border-color: #c42b1c; }
            QPushButton#DangerButton:hover { background: #d13438; border-color: #d13438; }
        """ % colors + window_chrome_qss(selected))

