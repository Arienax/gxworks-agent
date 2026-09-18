from shared.i18n import on_language_changed, runtime_text, tr
import copy

from plc.specification.approach import format_contract_summary
from ui.desktop.qt import pyqtSignal, Qt
from ui.desktop.qt import QColor
from ui.desktop.qt import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)
from ui.desktop.icons import set_codicon
from ui.desktop.controls import BorderedComboBox
from ui.desktop.theme import ThemeMode, get_theme_manager, normalize_theme, theme_tokens
from plc.specification.confirmed import (
    build_review_draft,
    canonicalize_confirmed_spec,
    io_table_to_raw,
    normalize_missing_info,
    validate_spec_draft,
)
from shared.display_names import (
    naturalize_display_text,
    preferred_display_name,
    source_display_name,
    version_display_name,
)
from ui.desktop.widgets.inspection import (
    DebugContextWidget,
    DebugReportCard as _UnifiedDebugReportCard,
    InspectionReportCard,
)


class MessageBubble(QFrame):
    def __init__(self, role, content, kind="message", metadata=None, parent=None):
        super().__init__(parent)
        self.setObjectName(
            "UserMessage" if role == "user" else "AssistantMessage"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(5)

        title_text = tr('你') if role == "user" else "PLC AI"
        if kind == "system":
            title_text = tr('系统')
        elif kind == "agent":
            title_text = tr('PLC AI · 工具')
        title = QLabel(title_text)
        title.setObjectName("MessageAuthor")
        # User text and accepted model text are evidence. Only application-owned
        # system messages may use the operator-friendly identifier renderer.
        body = QLabel(naturalize_display_text(content) if kind == "system" else str(content or ""))
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setObjectName("MessageBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(title)
        layout.addWidget(body)

        metadata = metadata or {}
        image_attachments = [
            item
            for item in (metadata.get("image_attachments") or [])
            if isinstance(item, dict)
        ]
        if image_attachments:
            names = [
                str(item.get("filename") or tr('图片'))
                for item in image_attachments
            ]
            attachment_label = QLabel(
                tr('已附加 {v0} 张图片：', v0=len(names)) + "、".join(names)
            )
            attachment_label.setObjectName("MessageMeta")
            attachment_label.setWordWrap(True)
            attachment_label.setToolTip("\n".join(names))
            layout.addWidget(attachment_label)
        version_id = metadata.get("version_id")
        if version_id:
            version = QLabel(tr('生成{v0}', v0=version_display_name(version_id)))
            version.setObjectName("MessageMeta")
            layout.addWidget(version)


class DebugReportCard(QFrame):
    fix_requested = pyqtSignal(object)
    copy_fix_requested = pyqtSignal(str)

    def __init__(self, report, latest_version_id=None, parent=None):
        super().__init__(parent)
        self.report = dict(report or {})
        self.latest_version_id = latest_version_id
        self.setObjectName("DebugReportCard")
        self.apply_theme(get_theme_manager().current_theme)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel(tr('调试报告'))
        title.setObjectName("DebugTitle")
        base_version = self.report.get("base_version_id") or "-"
        badge = QLabel(tr('基于{v0}', v0=version_display_name(base_version)))
        badge.setObjectName("DebugBadge")
        header.addWidget(title)
        header.addWidget(badge)
        header.addStretch()
        layout.addLayout(header)

        body = QLabel(self._body_text())
        body.setObjectName("DebugBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)

        actions = QHBoxLayout()
        actions.addStretch()

        if self.report.get("needs_fix"):
            copy_button = QPushButton(tr('复制修复要求到输入框'))
            copy_button.setObjectName("SecondaryButton")
            set_codicon(copy_button, "copy", tr('复制修复要求到输入框'), 10)
            copy_button.clicked.connect(
                lambda: self.copy_fix_requested.emit(
                    self.report.get("fix_instruction", "")
                )
            )
            actions.addWidget(copy_button)
            fix_button = QPushButton(tr('生成修复版本'))
            fix_button.setObjectName("PrimaryButton")
            set_codicon(fix_button, "tools", tr('生成修复版本'), 10)
            is_current = (
                not latest_version_id
                or self.report.get("base_version_id") == latest_version_id
            )
            fix_button.setEnabled(is_current)
            if not is_current:
                fix_button.setToolTip(tr('该报告基于旧版本，请重新调试当前版本'))
            fix_button.clicked.connect(lambda: self.fix_requested.emit(self.report))
            actions.addWidget(fix_button)
        layout.addLayout(actions)

    def apply_theme(self, mode):
        selected = normalize_theme(mode)
        if selected == ThemeMode.LIGHT:
            self.setStyleSheet("""
                QFrame#DebugReportCard { background: #ffffff; border: 1px solid #cccedb; border-left: 3px solid #0078d4; border-radius: 4px; }
                QFrame#DebugReportCard QLabel { color: #1e1e1e; background: transparent; }
                QFrame#DebugReportCard QLabel#DebugTitle { color: #1e1e1e; font-size: 13px; font-weight: 600; }
                QFrame#DebugReportCard QLabel#DebugBadge { color: #005a9e; background: #e5f1fb; border: 1px solid #9cc2e5; border-radius: 8px; padding: 2px 7px; }
                QFrame#DebugReportCard QLabel#DebugBody { color: #1e1e1e; background: #f5f5f5; border: 1px solid #cccedb; border-radius: 3px; padding: 8px; }
            """)
            return
        self.setStyleSheet("""
            QFrame#DebugReportCard {
                background: #1f1f1f;
                border: 1px solid #3c3c3c;
                border-left: 3px solid #0e639c;
                border-radius: 5px;
            }
            QFrame#DebugReportCard QLabel {
                color: #d4d4d4;
                background: transparent;
            }
            QFrame#DebugReportCard QLabel#DebugTitle {
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
            }
            QFrame#DebugReportCard QLabel#DebugBadge {
                color: #9cdcfe;
                background: #26384a;
                border: 1px solid #305d83;
                border-radius: 8px;
                padding: 2px 7px;
            }
            QFrame#DebugReportCard QLabel#DebugBody {
                color: #cccccc;
                background: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 8px;
            }
            QFrame#DebugReportCard QPushButton#SecondaryButton {
                color: #cccccc;
                background: #313131;
            }
            QFrame#DebugReportCard QPushButton#PrimaryButton {
                color: #ffffff;
                background: #0e639c;
            }
            QFrame#DebugReportCard QPushButton:disabled {
                color: #6a6a6a;
                background: #2a2a2a;
            }
        """)

    def _body_text(self):
        parts = [self.report.get("summary", tr('调试分析完成'))]
        causes = self.report.get("possible_causes") or []
        if causes:
            parts.append(tr('\n可能原因：'))
            parts.extend(f"- {item}" for item in causes)
        rungs = self.report.get("related_rungs") or []
        if rungs:
            parts.append(tr('\n涉及梯级：') + ", ".join(map(str, rungs)))
        changes = self.report.get("recommended_changes") or []
        if changes:
            parts.append(tr('\n建议修改：'))
            parts.extend(f"- {item}" for item in changes)
        local = self.report.get("local_findings") or []
        if local:
            parts.append(tr('\n本地评审提示：'))
            for item in local[:6]:
                if isinstance(item, dict):
                    text = item.get("message") or item.get("suggestion") or str(item)
                else:
                    text = str(item)
                parts.append(f"- {text}")
        if self.report.get("needs_fix") and self.report.get("fix_instruction"):
            parts.append(tr('\n修复要求：'))
            parts.append(self.report["fix_instruction"])
        return naturalize_display_text(
            "\n".join(str(part) for part in parts if str(part).strip())
        )


class RequirementReviewCard(QFrame):
    confirmed = pyqtSignal(object)
    revise_requested = pyqtSignal(str)
    draft_changed = pyqtSignal(object)
    draft_revise_requested = pyqtSignal(str, object)
    revise_with_draft_requested = pyqtSignal(str, object)
    # ---- v3 specification-confirmation implementation.
    def __init__(
        self,
        analysis,
        original_request,
        previous_spec=None,
        parent=None,
        plc_model=None,
    ):
        if plc_model is None and isinstance(parent, str) and parent.upper() in {"FX3U", "FX5U"}:
            plc_model, parent = parent, None
        super().__init__(parent)
        self.setObjectName("ReviewCard")
        self._theme = get_theme_manager().current_theme
        self.setStyleSheet(self._review_stylesheet(self._theme))
        self.analysis = copy.deepcopy(analysis or {})
        self.analysis["missing_info"] = normalize_missing_info(
            self.analysis.get("missing_info", [])
        )
        self.original_request = original_request
        self.previous_spec = copy.deepcopy(previous_spec)
        self.is_delta = bool(previous_spec)
        self.draft = build_review_draft(self.analysis, self.previous_spec)
        self.plc_model = str(
            plc_model
            or self.analysis.get("plc_model")
            or self.draft.get("plc_model")
            or (self.previous_spec or {}).get("plc_model")
            or "FX3U"
        ).upper()
        self.draft["plc_model"] = self.plc_model
        self.approach_group = None
        self.approach_radios = {}
        self._updating_tables = False
        self._warning_signature = None
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        self.setMinimumWidth(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(tr('生成前规格确认'))
        title.setObjectName("ReviewTitle")
        badge = QLabel(tr('差异确认') if self.is_delta else tr('首次确认'))
        badge.setObjectName("ReviewBadge")
        self.review_status = QLabel(tr('请检查规格'))
        self.review_status.setObjectName("ReviewStatus")
        header.addWidget(title)
        header.addWidget(badge)
        header.addStretch()
        header.addWidget(self.review_status)
        layout.addLayout(header)

        summary = QLabel(
            naturalize_display_text(
                self.draft.get("summary") or tr('未返回需求摘要')
            )
        )
        summary.setObjectName("ReviewSummary")
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(summary)

        if self.is_delta:
            self._add_delta_summary(layout)

        self._add_approaches(layout)
        self._add_parameters_table(layout)
        self._add_io_table(layout)

        self.validation_details = QLabel()
        self.validation_details.setObjectName("ValidationDetails")
        self.validation_details.setWordWrap(True)
        self.validation_details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.validation_details)

        notes_group = QGroupBox(tr('本轮补充说明'))
        notes_layout = QVBoxLayout(notes_group)
        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(72)
        self.notes_edit.setPlaceholderText(
            tr('补充必须保持的逻辑、地址、时序或本轮修改边界')
        )
        self.notes_edit.setPlainText(self.draft.get("user_notes", ""))
        self.notes_edit.textChanged.connect(self._on_draft_text_changed)
        notes_layout.addWidget(self.notes_edit)
        layout.addWidget(notes_group)

        actions = QHBoxLayout()
        revise = QPushButton(tr('返回修改'))
        revise.setObjectName("SecondaryButton")
        self.confirm_button = QPushButton(tr('确认并生成'))
        self.confirm_button.setObjectName("PrimaryButton")
        set_codicon(revise, "edit", tr('返回修改'), 10)
        set_codicon(self.confirm_button, "play", tr('确认并生成'), 10)
        revise.clicked.connect(self._request_revision)
        self.confirm_button.clicked.connect(self._emit_confirmed)
        actions.addStretch()
        actions.addWidget(revise)
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)

        self._refresh_raw_preview()
        self._validate_review()
        self.apply_theme(self._theme)
        self.destroyed.connect(on_language_changed(self._language_changed))

    def _language_changed(self, _language):
        self._validate_review()

    def _add_delta_summary(self, layout):
        changes_group = QGroupBox(tr('本轮变更'))
        changes_group.setObjectName("CurrentChangesGroup")
        changes_layout = QVBoxLayout(changes_group)
        self.change_summary_label = QLabel()
        self.change_summary_label.setObjectName("CurrentChanges")
        self.change_summary_label.setWordWrap(True)
        self.change_summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        changes_layout.addWidget(self.change_summary_label)
        layout.addWidget(changes_group)

        self.carried_group = QGroupBox(tr('沿用项（点击展开）'))
        self.carried_group.setObjectName("CarriedItemsGroup")
        self.carried_group.setCheckable(True)
        self.carried_group.setChecked(False)
        carried_layout = QVBoxLayout(self.carried_group)
        self.carried_summary_label = QLabel()
        self.carried_summary_label.setObjectName("CarriedItems")
        self.carried_summary_label.setWordWrap(True)
        self.carried_summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.carried_summary_label.setVisible(False)
        self.carried_group.toggled.connect(self.carried_summary_label.setVisible)
        carried_layout.addWidget(self.carried_summary_label)
        layout.addWidget(self.carried_group)
        self._refresh_delta_summary()

    @staticmethod
    def _row_map(rows, key):
        result = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            value = str(row.get(key, "")).strip().upper() if key == "address" else str(row.get(key, "")).strip()
            if value:
                result[value] = row
        return result

    def _delta_summary_lines(self):
        previous = canonicalize_confirmed_spec(self.previous_spec or {})
        current = self._current_draft()
        changes = []
        carried = []

        previous_approach = (previous.get("selected_approach") or {}).get("name", "")
        current_approach = (current.get("selected_approach") or {}).get("name", "")
        previous_approach_display = naturalize_display_text(previous_approach)
        current_approach_display = naturalize_display_text(current_approach)
        if previous_approach != current_approach and current_approach:
            changes.append(
                tr('实现方案：{v0} → {v1}', v0=previous_approach_display or tr('未设置'), v1=current_approach_display)
            )
        elif current_approach:
            carried.append(tr('实现方案：{v0}', v0=current_approach_display))

        previous_parameters = self._row_map(previous.get("parameters"), "name")
        current_parameters = self._row_map(current.get("parameters"), "name")
        for name, parameter in current_parameters.items():
            before = previous_parameters.get(name)
            value = str(parameter.get("value", ""))
            display_name = naturalize_display_text(name)
            if before is None:
                changes.append(tr('新增参数：{v0} = {v1}', v0=display_name, v1=value or tr('未填写')))
            elif str(before.get("value", "")) != value:
                changes.append(
                    tr('参数 {v0}：{v1} → {v2}', v0=display_name, v1=before.get('value', '') or tr('未填写'), v2=value or tr('未填写'))
                )
            else:
                carried.append(tr('参数：{v0} = {v1}', v0=display_name, v1=value or tr('未填写')))
        for name in previous_parameters.keys() - current_parameters.keys():
            changes.append(tr('移除参数：{v0}', v0=naturalize_display_text(name)))

        previous_io = self._row_map(previous.get("io_table"), "address")
        current_io = self._row_map(current.get("io_table"), "address")
        for address, item in current_io.items():
            before = previous_io.get(address)
            label = str(item.get("label", ""))
            display_label = naturalize_display_text(label)
            if before is None:
                changes.append(tr('新增 I/O：{v0} {v1}', v0=address, v1=display_label).rstrip())
            elif str(before.get("label", "")) != label or str(before.get("kind", "")) != str(item.get("kind", "")):
                changes.append(
                    tr('修改 I/O：{v0}，{v1} → {v2}', v0=address, v1=naturalize_display_text(before.get('label', '')) or tr('无说明'), v2=display_label or tr('无说明'))
                )
            else:
                carried.append(f"I/O：{address} {display_label}".rstrip())
        for address in previous_io.keys() - current_io.keys():
            changes.append(tr('移除 I/O：{v0}', v0=address))

        previous_notes = str(previous.get("user_notes", "")).strip()
        current_notes = str(current.get("user_notes", "")).strip()
        if previous_notes != current_notes:
            changes.append(tr('补充说明已更新'))
        elif current_notes:
            carried.append(tr('补充说明保持不变'))
        return changes, carried

    def _refresh_delta_summary(self):
        if not self.is_delta or not hasattr(self, "change_summary_label"):
            return
        changes, carried = self._delta_summary_lines()
        if not changes:
            changes = [tr('未检测到结构化字段变化；本轮需求摘要仍需人工确认。')]
        if not carried:
            carried = [tr('没有可列出的沿用项。')]
        self.change_summary_label.setText("\n".join(f"• {item}" for item in changes))
        self.carried_summary_label.setText("\n".join(f"• {item}" for item in carried))

    def _current_draft(self):
        spec = copy.deepcopy(self.draft)
        spec["plc_model"] = self.plc_model
        if self.approach_group is not None:
            checked = self.approach_group.checkedId()
            if checked in self.approach_radios:
                spec["selected_approach"] = copy.deepcopy(self.approach_radios[checked])
        if hasattr(self, "parameter_table"):
            spec["parameters"] = self._collect_parameters()
        if hasattr(self, "io_table_widget"):
            spec["io_table"] = self._collect_io_table()
            spec["io_allocation_raw"] = io_table_to_raw(spec["io_table"])
        if hasattr(self, "notes_edit"):
            spec["user_notes"] = self.notes_edit.toPlainText().strip()
        spec["confirmation_mode"] = "delta" if self.is_delta else "full"
        return spec

    def _request_revision(self):
        text = self._revision_text()
        draft = self._current_draft()
        self.draft = copy.deepcopy(draft)
        self.draft_changed.emit(copy.deepcopy(draft))
        self.draft_revise_requested.emit(text, copy.deepcopy(draft))
        self.revise_with_draft_requested.emit(text, copy.deepcopy(draft))
        self.revise_requested.emit(text)

    def _emit_draft_changed(self):
        if not hasattr(self, "notes_edit"):
            return
        draft = self._current_draft()
        self.draft = copy.deepcopy(draft)
        self.draft_changed.emit(copy.deepcopy(draft))

    def _on_draft_text_changed(self):
        self._refresh_delta_summary()
        self._validate_review()
        self._emit_draft_changed()

    @staticmethod
    def _review_stylesheet(mode=ThemeMode.DARK):
        if normalize_theme(mode) == ThemeMode.LIGHT:
            return """
                QFrame#ReviewCard { background: #fffdf5; border: 1px solid #c8a000; border-radius: 4px; }
                QFrame#ReviewCard QLabel { color: #1e1e1e; background: transparent; }
                QFrame#ReviewCard QLabel#ReviewTitle { color: #1e1e1e; font-size: 13px; font-weight: 600; }
                QFrame#ReviewCard QLabel#ReviewBadge { color: #714f00; background: #fff4ce; border: 1px solid #d6b656; border-radius: 8px; padding: 2px 7px; font-size: 10px; }
                QFrame#ReviewCard QLabel#ReviewStatus { color: #0066b8; font-size: 11px; }
                QFrame#ReviewCard QLabel#ReviewSummary, QFrame#ReviewCard QLabel#LockedSpec, QFrame#ReviewCard QLabel#ValidationDetails { color: #1e1e1e; background: #ffffff; border: 1px solid #cccedb; border-radius: 3px; padding: 8px; }
                QFrame#ReviewCard QCheckBox#RiskAcknowledgement { color: #714f00; background: #fff4ce; border: 1px solid #d6b656; border-radius: 3px; padding: 6px; }
                QFrame#ReviewCard QLabel#ApproachDescription { color: #616161; padding-left: 22px; }
                QFrame#ReviewCard QGroupBox, QFrame#ReviewCard QRadioButton { color: #1e1e1e; }
                QFrame#ReviewCard QTextEdit, QFrame#ReviewCard QLineEdit, QFrame#ReviewCard QTableWidget { color: #1e1e1e; background: #ffffff; border: 1px solid #cccedb; selection-color: #1e1e1e; selection-background-color: #cde8ff; }
                QFrame#ReviewCard QHeaderView::section { color: #1e1e1e; background: #f3f3f3; border: 1px solid #cccedb; padding: 4px; }
                QFrame#ReviewCard QPushButton#SecondaryButton { color: #1e1e1e; background: #f3f3f3; }
                QFrame#ReviewCard QPushButton#PrimaryButton { color: #ffffff; background: #0078d4; }
                QFrame#ReviewCard QPushButton:disabled { color: #8a8a8a; background: #eeeeee; }
            """
        return """
            QFrame#ReviewCard {
                background: #252526;
                border: 1px solid #cca700;
                border-radius: 4px;
            }
            QFrame#ReviewCard QLabel {
                color: #d4d4d4;
                background: transparent;
            }
            QFrame#ReviewCard QLabel#ReviewTitle {
                color: #f0f0f0;
                font-size: 13px;
                font-weight: 600;
            }
            QFrame#ReviewCard QLabel#ReviewBadge {
                color: #f0d97a;
                background: #3d3318;
                border: 1px solid #6b5717;
                border-radius: 8px;
                padding: 2px 7px;
                font-size: 10px;
            }
            QFrame#ReviewCard QLabel#ReviewStatus {
                color: #9cdcfe;
                font-size: 11px;
            }
            QFrame#ReviewCard QLabel#ReviewSummary,
            QFrame#ReviewCard QLabel#LockedSpec,
            QFrame#ReviewCard QLabel#ValidationDetails {
                color: #d4d4d4;
                background: #1f1f1f;
                border: 1px solid #3c3c3c;
                border-radius: 5px;
                padding: 8px;
            }
            QFrame#ReviewCard QCheckBox#RiskAcknowledgement {
                color: #f0d97a;
                background: #3d3318;
                border: 1px solid #6b5717;
                border-radius: 3px;
                padding: 6px;
            }
            QFrame#ReviewCard QLabel#ApproachDescription {
                color: #a0a0a0;
                padding-left: 22px;
            }
            QFrame#ReviewCard QGroupBox,
            QFrame#ReviewCard QRadioButton {
                color: #cccccc;
            }
            QFrame#ReviewCard QTextEdit,
            QFrame#ReviewCard QLineEdit,
            QFrame#ReviewCard QTableWidget {
                color: #cccccc;
                background: #1f1f1f;
                border: 1px solid #3c3c3c;
                selection-color: #ffffff;
                selection-background-color: #264f78;
            }
            QFrame#ReviewCard QHeaderView::section {
                color: #cccccc;
                background: #2d2d2d;
                border: 1px solid #3c3c3c;
                padding: 4px;
            }
            QFrame#ReviewCard QPushButton#SecondaryButton {
                color: #cccccc;
                background: #313131;
            }
            QFrame#ReviewCard QPushButton#PrimaryButton {
                color: #ffffff;
                background: #0e639c;
            }
            QFrame#ReviewCard QPushButton:disabled {
                color: #777777;
                background: #2a2a2a;
            }
        """

    def _add_approaches(self, layout):
        approaches = self.draft.get("approaches", [])
        if not approaches and not self.draft.get("selected_approach"):
            return
        group = QGroupBox(tr('实现方案'))
        group_layout = QVBoxLayout(group)
        self.approach_group = QButtonGroup(self)
        if not approaches:
            approaches = [self.draft.get("selected_approach", {})]
        selected_name = (self.draft.get("selected_approach") or {}).get("name", "")
        for index, approach in enumerate(approaches):
            name = approach.get("name", tr('方案 {v0}', v0=index + 1))
            display_name = preferred_display_name(
                {"name": name}, kind=tr('方案'), index=index + 1
            )
            radio = QRadioButton(display_name)
            radio.setToolTip(display_name)
            radio.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            radio.setChecked(index == 0 or name == selected_name)
            self.approach_group.addButton(radio, index)
            self.approach_radios[index] = approach
            radio.toggled.connect(
                lambda checked, self=self: checked and self._on_draft_text_changed()
            )
            group_layout.addWidget(radio)
            details = []
            for key, label in (
                ("description", tr('说明')),
                ("pros", tr('优点')),
                ("cons", tr('限制')),
                ("generation_guide", tr('生成要点')),
            ):
                value = str(approach.get(key, "")).strip()
                if value:
                    details.append(tr("{label}: {value}", label=label, value=naturalize_display_text(value)))
            contract_summary = format_contract_summary(approach, localized=True)
            if contract_summary:
                details.append(
                    tr('生成硬约束: {v0}', v0=contract_summary)
                )
            if details:
                description_label = QLabel(tr("\n").join(details))
                description_label.setObjectName("ApproachDescription")
                description_label.setWordWrap(True)
                description_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                group_layout.addWidget(description_label)
        layout.addWidget(group)

    @staticmethod
    def _item(text="", editable=True):
        item = QTableWidgetItem(text if isinstance(text, str) else str(text))
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _add_parameters_table(self, layout):
        group = QGroupBox(tr('关键参数'))
        group_layout = QVBoxLayout(group)
        self.parameter_table = QTableWidget(0, 5)
        self.parameter_table.setHorizontalHeaderLabels(
            [tr('参数'), tr('当前值'), tr('来源'), tr('必填'), tr('备注')]
        )
        header = self.parameter_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((230, 180, 85, 55, 260)):
            self.parameter_table.setColumnWidth(column, width)
        self.parameter_table.verticalHeader().setVisible(False)
        self.parameter_table.setWordWrap(True)
        self.parameter_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.parameter_table.setMinimumHeight(120)
        self.parameter_table.setMaximumHeight(210)
        parameters = self.draft.get("parameters") or []
        self.parameter_table.setRowCount(len(parameters))
        for row, parameter in enumerate(parameters):
            raw_name = str(parameter.get("name") or "").strip()
            name_item = self._item(naturalize_display_text(raw_name), False)
            name_item.setData(Qt.ItemDataRole.UserRole, raw_name)
            raw_value = str(parameter.get("value") or "")
            value_item = self._item(
                naturalize_display_text(raw_value), True
            )
            value_item.setData(Qt.ItemDataRole.UserRole, raw_value)
            raw_source = str(parameter.get("source") or "").strip()
            source_item = self._item(source_display_name(raw_source), False)
            source_item.setData(Qt.ItemDataRole.UserRole, raw_source)
            name_item.setToolTip(name_item.text())
            value_item.setToolTip(tr('可双击修改；有候选值时也可从下拉列表选择'))
            source_item.setToolTip(source_item.text())
            self.parameter_table.setItem(row, 0, name_item)
            self.parameter_table.setItem(row, 1, value_item)
            self.parameter_table.setItem(row, 2, source_item)
            required_text = tr('条件') if parameter.get("required_when") else (
                tr('是') if parameter.get("required") else tr('否')
            )
            required_item = self._item(required_text, False)
            if parameter.get("required_when"):
                required_item.setToolTip(tr('仅在相关控制方式被选择时必填'))
            self.parameter_table.setItem(row, 3, required_item)
            raw_note = str(parameter.get("note") or "")
            note_item = self._item(
                naturalize_display_text(raw_note), False
            )
            note_item.setData(Qt.ItemDataRole.UserRole, raw_note)
            note_item.setToolTip(note_item.text())
            self.parameter_table.setItem(row, 4, note_item)
            options = self._parameter_options(parameter.get("name"))
            if options:
                editor = BorderedComboBox()
                editor.setEditable(True)
                editor.enableOptionCards(360)
                for option in options:
                    editor.addOptionCard(option)
                editor.setCanonicalText(raw_value)
                self._style_parameter_editor(editor, self._theme)
                editor.lineEdit().setTextMargins(0, 0, 0, 0)
                editor.setToolTip(value_item.text())
                editor.currentIndexChanged.connect(
                    lambda _index, combo=editor, item=value_item:
                    self._sync_parameter_option(combo, item)
                )
                editor.lineEdit().textEdited.connect(
                    lambda text, combo=editor, item=value_item:
                    self._sync_parameter_custom_text(combo, item, text)
                )
                self.parameter_table.setCellWidget(row, 1, editor)
        self.parameter_table.resizeRowsToContents()
        self.parameter_table.itemChanged.connect(self._on_review_table_changed)
        group_layout.addWidget(self.parameter_table)
        layout.addWidget(group)

    def _parameter_options(self, name):
        for item in self.analysis.get("missing_info", []) or []:
            if str(item.get("question", "")).strip() == str(name).strip():
                return [str(option) for option in item.get("options", []) if str(option)]
        return []

    @staticmethod
    def _sync_parameter_option(editor, value_item):
        value = editor.canonicalText()
        value_item.setText(value)
        editor.setToolTip(value)

    @staticmethod
    def _sync_parameter_custom_text(editor, value_item, text):
        value = str(text or "").strip()
        value_item.setText(value)
        editor.setToolTip(value)

    def _style_parameter_editor(self, editor, mode):
        selected = normalize_theme(mode)
        colors = theme_tokens(selected)
        editor.setProperty("darkTheme", selected == ThemeMode.DARK)
        editor.setStyleSheet(f"""
            QComboBox {{
                min-height: 30px;
                padding: 0 34px 0 8px;
                color: {colors['text']};
                background: {colors['input']};
                border: 1px solid {colors['border']};
                border-radius: 2px;
            }}
            QComboBox:focus {{ border-color: {colors['accent']}; }}
            QComboBox::drop-down {{ width: 32px; border: none; background: transparent; }}
            QComboBox QLineEdit {{
                min-height: 0;
                padding: 0;
                color: {colors['text']};
                background: transparent;
                border: none;
            }}
        """)
        editor._refresh_style()

    def apply_theme(self, mode):
        self._theme = normalize_theme(mode)
        self.setStyleSheet(self._review_stylesheet(self._theme))
        if hasattr(self, "parameter_table"):
            for row in range(self.parameter_table.rowCount()):
                editor = self.parameter_table.cellWidget(row, 1)
                if isinstance(editor, BorderedComboBox):
                    self._style_parameter_editor(editor, self._theme)
        if hasattr(self, "confirm_button"):
            self._validate_review()

    def _add_io_table(self, layout):
        group = QGroupBox(tr('I/O 分配'))
        group_layout = QVBoxLayout(group)
        self.io_table_widget = QTableWidget(0, 4)
        self.io_table_widget.setHorizontalHeaderLabels([tr('类别'), tr('地址'), tr('说明'), tr('来源')])
        self.io_table_widget.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.io_table_widget.verticalHeader().setVisible(False)
        self.io_table_widget.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.io_table_widget.setMinimumHeight(160)
        self.io_table_widget.setMaximumHeight(260)
        for row in self.draft.get("io_table") or []:
            self._append_io_row(row)
        self.io_table_widget.itemChanged.connect(self._on_review_table_changed)
        group_layout.addWidget(self.io_table_widget)

        buttons = QHBoxLayout()
        add_button = QPushButton(tr('新增 I/O'))
        add_button.setObjectName("SecondaryButton")
        delete_button = QPushButton(tr('删除选中'))
        delete_button.setObjectName("SecondaryButton")
        add_button.clicked.connect(lambda: self._append_io_row({"source": "user"}))
        delete_button.clicked.connect(self._delete_selected_io_rows)
        buttons.addStretch()
        buttons.addWidget(add_button)
        buttons.addWidget(delete_button)
        group_layout.addLayout(buttons)

        self.raw_preview = QTextEdit()
        self.raw_preview.setReadOnly(True)
        self.raw_preview.setMaximumHeight(76)
        self.raw_preview.setPlaceholderText(
            tr('I/O 分配预览（内部绑定保持不变）')
        )
        group_layout.addWidget(self.raw_preview)
        layout.addWidget(group)

    def _append_io_row(self, row):
        self._updating_tables = True
        try:
            index = self.io_table_widget.rowCount()
            self.io_table_widget.insertRow(index)
            self.io_table_widget.setItem(
                index, 0, self._item(row.get("kind", ""), True)
            )
            self.io_table_widget.setItem(
                index, 1, self._item(row.get("address", ""), True)
            )
            raw_label = str(row.get("label", "") or "")
            label_item = self._item(
                naturalize_display_text(raw_label), True
            )
            label_item.setData(Qt.ItemDataRole.UserRole, raw_label)
            self.io_table_widget.setItem(index, 2, label_item)
            raw_source = str(row.get("source", "user") or "user").strip()
            source_item = self._item(source_display_name(raw_source), False)
            source_item.setData(Qt.ItemDataRole.UserRole, raw_source)
            self.io_table_widget.setItem(index, 3, source_item)
        finally:
            self._updating_tables = False
        self._refresh_raw_preview()
        if hasattr(self, "confirm_button"):
            self._refresh_delta_summary()
            self._validate_review()
            self._emit_draft_changed()

    def _delete_selected_io_rows(self):
        rows = sorted(
            {item.row() for item in self.io_table_widget.selectedItems()},
            reverse=True,
        )
        if not rows:
            return
        for row in rows:
            self.io_table_widget.removeRow(row)
        self._refresh_raw_preview()
        self._refresh_delta_summary()
        self._validate_review()
        self._emit_draft_changed()

    def _on_review_table_changed(self, item):
        if self._updating_tables:
            return
        if item and item.tableWidget() is self.parameter_table and item.column() == 1:
            source_item = self.parameter_table.item(item.row(), 2)
            if source_item is not None and item.text().strip():
                self._updating_tables = True
                try:
                    source_item.setText(source_display_name("user"))
                    source_item.setData(Qt.ItemDataRole.UserRole, "user")
                finally:
                    self._updating_tables = False
        if item and item.tableWidget() is self.io_table_widget:
            if item.column() == 1:
                normalized = item.text().strip().upper().replace(" ", "")
                if normalized != item.text():
                    self._updating_tables = True
                    try:
                        item.setText(normalized)
                    finally:
                        self._updating_tables = False
            elif item.column() == 0:
                normalized = item.text().strip().upper()
                if normalized and normalized != tr('特殊') and normalized not in {
                    "X", "Y", "M", "T", "C", "D", "S"
                }:
                    normalized = tr('特殊')
                if normalized != item.text():
                    self._updating_tables = True
                    try:
                        item.setText(normalized)
                    finally:
                        self._updating_tables = False
            if item.column() in {0, 1, 2}:
                source_item = self.io_table_widget.item(item.row(), 3)
                if source_item is not None:
                    self._updating_tables = True
                    try:
                        source_item.setText(source_display_name("user"))
                        source_item.setData(Qt.ItemDataRole.UserRole, "user")
                    finally:
                        self._updating_tables = False
        self._refresh_raw_preview()
        self._refresh_delta_summary()
        self._validate_review()
        self._emit_draft_changed()

    def _collect_parameters(self):
        parameters = []
        base_parameters = self.draft.get("parameters") or []
        for row in range(self.parameter_table.rowCount()):
            name = self._table_value(self.parameter_table, row, 0)
            if not name:
                continue
            base = base_parameters[row] if row < len(base_parameters) else {}
            parameter = {
                "id": str(base.get("id", "")).strip(),
                "name": name,
                "value": self._table_editable_value(
                    self.parameter_table, row, 1
                ),
                "source": self._table_value(self.parameter_table, row, 2),
                # The required column is read-only presentation, not a boolean
                # encoded in translated Yes/No text.
                "required": bool(base.get("required", False)),
                "note": self._table_value(self.parameter_table, row, 4),
            }
            if isinstance(base.get("required_when"), dict):
                parameter["required_when"] = copy.deepcopy(base["required_when"])
            parameters.append(parameter)
        return parameters

    def _collect_io_table(self):
        rows = []
        for row in range(self.io_table_widget.rowCount()):
            address = self._table_text(self.io_table_widget, row, 1).upper()
            label = self._table_editable_value(self.io_table_widget, row, 2)
            if not address and not label:
                continue
            rows.append(
                {
                    "kind": self._table_text(self.io_table_widget, row, 0),
                    "address": address,
                    "label": label,
                    "source": self._table_value(self.io_table_widget, row, 3) or "user",
                }
            )
        return rows

    @staticmethod
    def _table_text(table, row, column):
        item = table.item(row, column)
        return item.text().strip() if item else ""

    @staticmethod
    def _table_value(table, row, column):
        item = table.item(row, column)
        if not item:
            return ""
        raw = item.data(Qt.ItemDataRole.UserRole)
        return str(raw).strip() if raw not in (None, "") else item.text().strip()

    @staticmethod
    def _table_editable_value(table, row, column):
        """Keep the stable value unless its natural display was edited."""

        item = table.item(row, column)
        if not item:
            return ""
        display = item.text().strip()
        raw = item.data(Qt.ItemDataRole.UserRole)
        if raw in (None, ""):
            return display
        raw_text = str(raw).strip()
        if display == naturalize_display_text(raw_text):
            return raw_text
        return display

    def _refresh_raw_preview(self):
        if not hasattr(self, "raw_preview"):
            return
        display_rows = copy.deepcopy(self._collect_io_table())
        for row in display_rows:
            row["label"] = naturalize_display_text(row.get("label", ""))
        self.raw_preview.setPlainText(io_table_to_raw(display_rows))

    def _set_cell_warning(self, table, row, column, enabled):
        self._set_cell_state(table, row, column, "error" if enabled else "")

    def _set_cell_state(self, table, row, column, state=""):
        item = table.item(row, column)
        if not item:
            return
        colors = theme_tokens(self._theme)
        if state == "error":
            color = "#fde7e9" if self._theme == ThemeMode.LIGHT else "#5a1d1d"
        elif state == "warning":
            color = "#fff4ce" if self._theme == ThemeMode.LIGHT else "#5a4818"
        else:
            color = colors["surface"]
        was_updating = self._updating_tables
        self._updating_tables = True
        try:
            item.setBackground(QColor(color))
        finally:
            self._updating_tables = was_updating

    @staticmethod
    def _validation_message(issue):
        if isinstance(issue, dict):
            return naturalize_display_text(
                runtime_text(issue.get("message") or issue.get("code") or tr('未说明的问题'))
            )
        return naturalize_display_text(runtime_text(issue))

    def _mark_validation_issue(self, issue, state):
        if not isinstance(issue, dict):
            return
        row = issue.get("row")
        try:
            row = int(row)
        except (TypeError, ValueError):
            return
        path = str(issue.get("path") or issue.get("field") or "")
        if "parameter" in path and 0 <= row < self.parameter_table.rowCount():
            column = 0 if path.endswith(".name") else 1
            self._set_cell_state(self.parameter_table, row, column, state)
        elif ("io_table" in path or issue.get("address")) and 0 <= row < self.io_table_widget.rowCount():
            if path.endswith(".kind"):
                column = 0
            elif path.endswith(".label"):
                column = 2
            else:
                column = 1
            self._set_cell_state(self.io_table_widget, row, column, state)

    def _validate_review(self, *_args):
        if not hasattr(self, "confirm_button"):
            return

        for row in range(self.parameter_table.rowCount()):
            for column in (0, 1):
                self._set_cell_state(self.parameter_table, row, column)
        for row in range(self.io_table_widget.rowCount()):
            for column in (0, 1, 2):
                self._set_cell_state(self.io_table_widget, row, column)

        result = validate_spec_draft(self._current_draft(), self.plc_model)
        errors = list(result.get("errors") or [])
        warnings = list(result.get("warnings") or [])
        for issue in warnings:
            self._mark_validation_issue(issue, "warning")
        for issue in errors:
            self._mark_validation_issue(issue, "error")

        self._warning_signature = tuple(
            sorted(self._validation_message(issue) for issue in warnings)
        )
        self.confirm_button.setEnabled(not errors)
        if errors:
            status = tr('{v0} 项错误，无法确认', v0=len(errors))
        elif warnings:
            status = tr('{v0} 项提示，可继续', v0=len(warnings))
        else:
            status = tr('规格可确认')
        self.review_status.setText(status)

        details = []
        if errors:
            details.append(tr('错误：'))
            details.extend(f"• {self._validation_message(issue)}" for issue in errors)
        if warnings:
            details.append(tr('提示：'))
            details.extend(f"• {self._validation_message(issue)}" for issue in warnings)
        self.validation_details.setText("\n".join(details))
        self.validation_details.setVisible(bool(details))

    def _revision_text(self):
        summary = self.draft.get("summary", "")
        return (
            tr('{v0}\n\n请根据以下分析继续修改需求：\n{v1}', v0=self.original_request, v1=summary)
        ).strip()

    @staticmethod
    def _build_locked_summary(spec):
        approach = spec.get("selected_approach") or {}
        approach_name = naturalize_display_text(
            approach.get("name", tr('沿用已确认方案'))
        )
        io_count = len(spec.get("io_table") or [])
        parameter_count = len(spec.get("parameters") or [])
        return (
            tr('已锁定：{v0}；I/O {v1} 项，关键参数 {v2} 项。未在本卡中明确修改的内容将继续沿用。', v0=approach_name, v1=io_count, v2=parameter_count)
        )

    def _emit_confirmed(self):
        spec = self._current_draft()
        result = validate_spec_draft(spec, self.plc_model)
        errors = list(result.get("errors") or [])
        warnings = list(result.get("warnings") or [])
        if errors:
            self._validate_review()
            return
        spec["missing_answers"] = {}
        spec["plc_model"] = self.plc_model
        spec.pop("risk_acknowledged", None)
        spec["validation_warnings"] = [
            self._validation_message(issue) for issue in warnings
        ]
        self.confirmed.emit(canonicalize_confirmed_spec(spec))


# Keep the historical import path while rendering old and new reports through
# the unified report component.
DebugReportCard = _UnifiedDebugReportCard


__all__ = [
    "DebugContextWidget",
    "DebugReportCard",
    "InspectionReportCard",
    "MessageBubble",
    "RequirementReviewCard",
    "validate_spec_draft",
]
