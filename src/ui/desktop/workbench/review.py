"""Specification review dialog and summary card; editor state lives in editor.py."""

from __future__ import annotations

from shared.i18n import on_language_changed, tr

import copy
import sys
from pathlib import Path

from ui.desktop.qt import (
    QApplication,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    Qt,
    pyqtSignal,
)
from ui.desktop.icons import set_codicon
from ui.desktop.theme import get_theme_manager, normalize_theme, theme_tokens


from . import editor as _legacy

# Preserve the old module's public API. The replacement RequirementReviewCard
# is defined below and intentionally overwrites the legacy export.
for _name in dir(_legacy):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_legacy, _name)

_LegacyRequirementReviewCard = _legacy.RequirementReviewCard


def _group_by_title(root: QWidget, title: str):
    for group in root.findChildren(QGroupBox):
        if str(group.title()).strip() == title:
            return group
    return None


def _detach_widget(widget):
    if widget is None:
        return None
    widget.setParent(None)
    widget.show()
    return widget


def _page_with_scroll(widget: QWidget | None, *, empty_text: str = tr('暂无内容')) -> QWidget:
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    if widget is None:
        placeholder = QLabel(empty_text)
        placeholder.setObjectName("SpecEmptyState")
        placeholder.setWordWrap(True)
        outer.addWidget(placeholder)
        outer.addStretch()
        return page

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(
        QFrame.Shape.NoFrame if hasattr(QFrame, "Shape") else QFrame.NoFrame
    )
    host = QWidget()
    host_layout = QVBoxLayout(host)
    host_layout.setContentsMargins(2, 2, 8, 8)
    host_layout.addWidget(widget)
    host_layout.addStretch()
    scroll.setWidget(host)
    outer.addWidget(scroll)
    return page


class SpecificationWorkbenchDialog(QDialog):
    """Maximized specification editor with navigation and live validation."""

    confirmed = pyqtSignal(object)
    revise_requested = pyqtSignal(str)
    draft_changed = pyqtSignal(object)
    draft_revise_requested = pyqtSignal(str, object)
    revise_with_draft_requested = pyqtSignal(str, object)

    NAV_ITEMS = (
        (tr('概览'), "dashboard"),
        (tr('实现方案'), "list-selection"),
        (tr('控制参数'), "settings-gear"),
        (tr('I/O 映射'), "symbol-field"),
        (tr('高级约束'), "shield"),
    )

    def __init__(
        self,
        analysis,
        original_request,
        previous_spec=None,
        parent=None,
        plc_model=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr('生成前规格确认 · 规格工作台'))
        self.setModal(True)
        self.setMinimumSize(1000, 700)
        self.analysis = copy.deepcopy(analysis or {})
        self.original_request = str(original_request or "")
        self.previous_spec = copy.deepcopy(previous_spec)
        self.plc_model = str(
            plc_model
            or self.analysis.get("plc_model")
            or (self.previous_spec or {}).get("plc_model")
            or "FX3U"
        ).upper()
        self._theme = normalize_theme(get_theme_manager().current_theme)

        self.editor = _LegacyRequirementReviewCard(
            self.analysis,
            self.original_request,
            self.previous_spec,
            plc_model=self.plc_model,
        )
        self.editor.setParent(self)
        # The old card remains the state/validation engine. Its editable
        # sections are re-parented into dedicated workbench pages below.
        self.editor.hide()

        self._build_ui()
        self._wire_editor()
        self._sync_live_state()
        self.apply_theme(self._theme)
        self.destroyed.connect(on_language_changed(self._language_changed))

    def _language_changed(self, _language):
        self._sync_live_state()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("SpecWorkbenchHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 14, 22, 14)
        header_layout.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel(tr('生成前规格确认'))
        title.setObjectName("SpecWorkbenchTitle")
        subtitle = QLabel(tr('在生成 PLC 程序前确认实现方案、参数、I/O 与生成约束'))
        subtitle.setObjectName("SpecWorkbenchSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box)
        header_layout.addStretch()
        self.model_badge = QLabel(self.plc_model)
        self.model_badge.setObjectName("SpecBadge")
        mode_text = tr('差异确认') if bool(self.previous_spec) else tr('首次确认')
        self.mode_badge = QLabel(mode_text)
        self.mode_badge.setObjectName("SpecBadge")
        self.header_status = QLabel(tr('检查规格…'))
        self.header_status.setObjectName("SpecStatusBadge")
        header_layout.addWidget(self.model_badge)
        header_layout.addWidget(self.mode_badge)
        header_layout.addWidget(self.header_status)
        root.addWidget(header)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setObjectName("SpecWorkbenchBody")
        body.setChildrenCollapsible(False)

        left = QFrame()
        left.setObjectName("SpecNavPanel")
        left.setMinimumWidth(170)
        left.setMaximumWidth(220)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 14, 12, 14)
        nav_title = QLabel(tr('规格导航'))
        nav_title.setObjectName("SpecPanelTitle")
        left_layout.addWidget(nav_title)
        self.nav = QListWidget()
        self.nav.setObjectName("SpecNav")
        for label, _icon in self.NAV_ITEMS:
            self.nav.addItem(label)
        self.nav.setCurrentRow(0)
        left_layout.addWidget(self.nav, 1)
        nav_hint = QLabel(tr('逐项检查后再生成；未通过的字段会在右侧实时显示。'))
        nav_hint.setObjectName("SpecHint")
        nav_hint.setWordWrap(True)
        left_layout.addWidget(nav_hint)
        body.addWidget(left)

        center = QFrame()
        center.setObjectName("SpecEditorPanel")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(18, 16, 18, 16)
        center_layout.setSpacing(10)
        self.page_title = QLabel(tr('概览'))
        self.page_title.setObjectName("SpecPageTitle")
        center_layout.addWidget(self.page_title)
        self.stack = QStackedWidget()
        self.stack.setObjectName("SpecEditorStack")
        center_layout.addWidget(self.stack, 1)
        body.addWidget(center)

        right = QFrame()
        right.setObjectName("SpecValidationPanel")
        right.setMinimumWidth(270)
        right.setMaximumWidth(360)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)
        right_title = QLabel(tr('实时检查'))
        right_title.setObjectName("SpecPanelTitle")
        right_layout.addWidget(right_title)
        self.validation_summary = QLabel()
        self.validation_summary.setObjectName("SpecValidationSummary")
        self.validation_summary.setWordWrap(True)
        right_layout.addWidget(self.validation_summary)
        self.validation_details = _detach_widget(
            getattr(self.editor, "validation_details", None)
        )
        if self.validation_details is not None:
            self.validation_details.setObjectName("SpecValidationDetails")
            right_layout.addWidget(self.validation_details)
        self.contract_preview = QLabel()
        self.contract_preview.setObjectName("SpecContractPreview")
        self.contract_preview.setWordWrap(True)
        self.contract_preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        right_layout.addWidget(self.contract_preview)
        right_layout.addStretch()
        body.addWidget(right)

        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        body.setSizes([190, 850, 310])
        root.addWidget(body, 1)

        self._build_pages()
        self.nav.currentRowChanged.connect(self._switch_page)

        footer = QFrame()
        footer.setObjectName("SpecWorkbenchFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 10, 18, 10)
        footer_layout.setSpacing(8)
        self.footer_state = QLabel(tr('规格草稿'))
        self.footer_state.setObjectName("SpecFooterState")
        footer_layout.addWidget(self.footer_state)
        footer_layout.addStretch()
        self.revise_button = QPushButton(tr('修改原始需求'))
        self.revise_button.setObjectName("SecondaryButton")
        set_codicon(self.revise_button, "edit", tr('修改原始需求'), 10)
        self.save_button = QPushButton(tr('保存草稿'))
        self.save_button.setObjectName("SecondaryButton")
        set_codicon(self.save_button, "save", tr('保存草稿'), 10)
        self.confirm_button = QPushButton(tr('确认并生成'))
        self.confirm_button.setObjectName("PrimaryButton")
        set_codicon(self.confirm_button, "play", tr('确认并生成'), 10)
        footer_layout.addWidget(self.revise_button)
        footer_layout.addWidget(self.save_button)
        footer_layout.addWidget(self.confirm_button)
        root.addWidget(footer)

        self.revise_button.clicked.connect(self._request_revision)
        self.save_button.clicked.connect(self._save_draft)
        self.confirm_button.clicked.connect(self._confirm)

    def _build_pages(self):
        summary = None
        for label in self.editor.findChildren(QLabel):
            if label.objectName() == "ReviewSummary":
                summary = _detach_widget(label)
                break

        overview_host = QWidget()
        overview_layout = QVBoxLayout(overview_host)
        overview_layout.setContentsMargins(4, 4, 8, 8)
        if summary is not None:
            summary.setObjectName("SpecOverviewSummary")
            overview_layout.addWidget(summary)
        if bool(self.previous_spec):
            for title in (tr('本轮变更'), tr('沿用项（点击展开）')):
                group = _detach_widget(_group_by_title(self.editor, title))
                if group is not None:
                    overview_layout.addWidget(group)
        self.overview_metrics = QLabel()
        self.overview_metrics.setObjectName("SpecOverviewMetrics")
        self.overview_metrics.setWordWrap(True)
        overview_layout.addWidget(self.overview_metrics)
        overview_layout.addStretch()
        self.stack.addWidget(_page_with_scroll(overview_host))

        approach_group = _detach_widget(_group_by_title(self.editor, tr('实现方案')))
        self.stack.addWidget(
            _page_with_scroll(approach_group, empty_text=tr('当前规格没有可选实现方案。'))
        )

        parameter_group = _detach_widget(_group_by_title(self.editor, tr('关键参数')))
        if hasattr(self.editor, "parameter_table"):
            table = self.editor.parameter_table
            table.setMaximumHeight(16777215)
            table.setMinimumHeight(360)
            table.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
        self.stack.addWidget(
            _page_with_scroll(
                parameter_group,
                empty_text=tr('当前规格没有需要确认的控制参数。'),
            )
        )

        io_group = _detach_widget(_group_by_title(self.editor, tr('I/O 分配')))
        if hasattr(self.editor, "io_table_widget"):
            table = self.editor.io_table_widget
            table.setMaximumHeight(16777215)
            table.setMinimumHeight(400)
            table.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
        if hasattr(self.editor, "raw_preview"):
            self.editor.raw_preview.setMaximumHeight(120)
        self.stack.addWidget(
            _page_with_scroll(io_group, empty_text=tr('当前规格没有 I/O 分配。'))
        )

        advanced_host = QWidget()
        advanced_layout = QVBoxLayout(advanced_host)
        advanced_layout.setContentsMargins(4, 4, 8, 8)
        notes_group = _detach_widget(_group_by_title(self.editor, tr('本轮补充说明')))
        if notes_group is not None:
            if hasattr(self.editor, "notes_edit"):
                self.editor.notes_edit.setMaximumHeight(16777215)
                self.editor.notes_edit.setMinimumHeight(150)
            advanced_layout.addWidget(notes_group)
        advanced_title = QLabel(tr('当前生成约束'))
        advanced_title.setObjectName("SpecSectionTitle")
        advanced_layout.addWidget(advanced_title)
        self.advanced_contract = QLabel()
        self.advanced_contract.setObjectName("SpecAdvancedContract")
        self.advanced_contract.setWordWrap(True)
        self.advanced_contract.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        advanced_layout.addWidget(self.advanced_contract)
        advanced_layout.addStretch()
        self.stack.addWidget(_page_with_scroll(advanced_host))

        # The legacy final-action controls remain hidden; the fixed footer is
        # now the only confirmation surface.
        for button in self.editor.findChildren(QPushButton):
            if button.text().strip() in {tr('返回修改'), tr('确认并生成')}:
                button.hide()

    def _wire_editor(self):
        self.editor.confirmed.connect(self._on_confirmed)
        self.editor.revise_requested.connect(self.revise_requested.emit)
        self.editor.draft_changed.connect(self._on_draft_changed)
        self.editor.draft_revise_requested.connect(self.draft_revise_requested.emit)
        self.editor.revise_with_draft_requested.connect(
            self.revise_with_draft_requested.emit
        )

    def _switch_page(self, row):
        if row < 0 or row >= self.stack.count():
            return
        self.stack.setCurrentIndex(row)
        self.page_title.setText(self.NAV_ITEMS[row][0])

    def _current_draft(self):
        return self.editor._current_draft()

    def _validation_result(self):
        return _legacy.validate_spec_draft(self._current_draft(), self.plc_model)

    @staticmethod
    def _issue_text(issue):
        return _LegacyRequirementReviewCard._validation_message(issue)

    def _selected_approach(self, draft):
        approach = draft.get("selected_approach") or {}
        if not isinstance(approach, dict):
            return {}
        return approach

    def _contract_text(self, draft):
        approach = self._selected_approach(draft)
        if not approach:
            return tr('当前未选择实现方案。')
        name = _legacy.preferred_display_name(approach, kind=tr('方案'), index=1)
        summary = _legacy.format_contract_summary(approach, localized=True)
        lines = [tr('实现方案：{v0}', v0=name)]
        if summary:
            lines.append(
                tr('生成硬约束：{v0}', v0=summary)
            )
        guide = str(approach.get("generation_guide") or "").strip()
        if guide:
            lines.append(tr('生成要点：') + _legacy.naturalize_display_text(guide))
        return tr("\n").join(lines)

    def _sync_live_state(self):
        try:
            self.editor._validate_review()
        except Exception:
            pass
        draft = self._current_draft()
        result = self._validation_result()
        errors = list(result.get("errors") or [])
        warnings = list(result.get("warnings") or [])
        parameters = list(draft.get("parameters") or [])
        io_rows = list(draft.get("io_table") or [])
        approach = self._selected_approach(draft)
        approach_name = (
            _legacy.preferred_display_name(approach, kind=tr('方案'), index=1)
            if approach
            else tr('未选择')
        )

        if errors:
            state = tr('{v0} 个问题待处理', v0=len(errors))
            self.header_status.setText(tr('需要修改'))
            self.header_status.setProperty("state", "error")
            self.confirm_button.setEnabled(False)
        elif warnings:
            state = tr('可生成 · {v0} 个提醒', v0=len(warnings))
            self.header_status.setText(tr('可生成'))
            self.header_status.setProperty("state", "warning")
            self.confirm_button.setEnabled(True)
        else:
            state = tr('规格完整 · 可以生成')
            self.header_status.setText(tr('规格可生成'))
            self.header_status.setProperty("state", "ok")
            self.confirm_button.setEnabled(True)
        self.footer_state.setText(state)

        lines = []
        if errors:
            lines.append(tr('❌ {v0} 个错误', v0=len(errors)))
            lines.extend(f"• {self._issue_text(item)}" for item in errors[:8])
        else:
            lines.append(tr('✓ 必填规格已满足'))
        if warnings:
            lines.append(tr('\n⚠ {v0} 个提醒', v0=len(warnings)))
            lines.extend(f"• {self._issue_text(item)}" for item in warnings[:6])
        self.validation_summary.setText("\n".join(lines))

        metrics = [
            f"PLC：{self.plc_model}",
            tr('实现方案：{v0}', v0=approach_name),
            tr('控制参数：{v0} 项', v0=len(parameters)),
            tr('I/O 映射：{v0} 项', v0=len(io_rows)),
        ]
        self.overview_metrics.setText("\n".join(metrics))
        contract_text = self._contract_text(draft)
        self.contract_preview.setText(contract_text)
        self.advanced_contract.setText(contract_text)

        # Updating a dynamic property does not always repolish Qt widgets.
        self.header_status.style().unpolish(self.header_status)
        self.header_status.style().polish(self.header_status)
        self.header_status.update()

    def _on_draft_changed(self, draft):
        self.draft_changed.emit(copy.deepcopy(draft))
        self._sync_live_state()

    def _save_draft(self):
        draft = self._current_draft()
        self.editor.draft = copy.deepcopy(draft)
        self.draft_changed.emit(copy.deepcopy(draft))
        self._sync_live_state()
        self.footer_state.setText(
            tr('草稿已保存 · ') + self.footer_state.text()
        )

    def _request_revision(self):
        self.editor._request_revision()
        self.close()

    def _confirm(self):
        self.editor._emit_confirmed()
        self._sync_live_state()

    def _on_confirmed(self, spec):
        self.confirmed.emit(spec)
        self.accept()

    def show_full_size(self):
        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            width = max(1000, int(geometry.width() * 0.92))
            height = max(700, int(geometry.height() * 0.90))
            self.resize(width, height)
        self.showMaximized()
        self.raise_()
        self.activateWindow()

    def apply_theme(self, mode):
        self._theme = normalize_theme(mode)
        colors = theme_tokens(self._theme)
        self.editor.apply_theme(self._theme)
        self.setStyleSheet(f"""
            QDialog {{
                background: {colors['shell']};
                color: {colors['text']};
            }}
            QFrame#SpecWorkbenchHeader, QFrame#SpecWorkbenchFooter {{
                background: {colors['surface']};
                border: none;
            }}
            QFrame#SpecWorkbenchHeader {{ border-bottom: 1px solid {colors['border']}; }}
            QFrame#SpecWorkbenchFooter {{ border-top: 1px solid {colors['border']}; }}
            QLabel#SpecWorkbenchTitle {{ color: {colors['text_strong']}; font-size: 20px; font-weight: 700; }}
            QLabel#SpecWorkbenchSubtitle, QLabel#SpecHint, QLabel#SpecFooterState {{ color: {colors['text_muted']}; }}
            QLabel#SpecBadge {{
                color: {colors['text']}; background: {colors['surface_alt']};
                border: 1px solid {colors['border']}; border-radius: 10px; padding: 4px 9px;
            }}
            QLabel#SpecStatusBadge {{
                color: #ffffff; background: {colors['accent_button']};
                border-radius: 10px; padding: 4px 9px; font-weight: 600;
            }}
            QLabel#SpecStatusBadge[state="error"] {{ background: #c42b1c; }}
            QLabel#SpecStatusBadge[state="warning"] {{ background: #9a6700; }}
            QLabel#SpecStatusBadge[state="ok"] {{ background: #107c10; }}
            QFrame#SpecNavPanel, QFrame#SpecValidationPanel {{
                background: {colors['surface']}; border: none;
            }}
            QFrame#SpecNavPanel {{ border-right: 1px solid {colors['border']}; }}
            QFrame#SpecValidationPanel {{ border-left: 1px solid {colors['border']}; }}
            QFrame#SpecEditorPanel {{ background: {colors['shell']}; border: none; }}
            QLabel#SpecPanelTitle, QLabel#SpecPageTitle, QLabel#SpecSectionTitle {{
                color: {colors['text_strong']}; font-weight: 700;
            }}
            QLabel#SpecPageTitle {{ font-size: 18px; }}
            QListWidget#SpecNav {{
                color: {colors['text']}; background: transparent; border: none; outline: none;
            }}
            QListWidget#SpecNav::item {{ padding: 11px 10px; margin: 2px 0; border-radius: 5px; }}
            QListWidget#SpecNav::item:hover {{ background: {colors['hover']}; }}
            QListWidget#SpecNav::item:selected {{
                color: {colors['text_strong']}; background: {colors['selection']};
                border-left: 3px solid {colors['accent']};
            }}
            QStackedWidget#SpecEditorStack, QScrollArea {{ background: transparent; border: none; }}
            QGroupBox {{
                color: {colors['text_strong']}; background: {colors['surface']};
                border: 1px solid {colors['border']}; border-radius: 6px;
                margin-top: 12px; padding-top: 10px; font-weight: 600;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QLabel#SpecOverviewSummary, QLabel#SpecOverviewMetrics,
            QLabel#SpecValidationSummary, QLabel#SpecValidationDetails,
            QLabel#SpecContractPreview, QLabel#SpecAdvancedContract, QLabel#SpecEmptyState {{
                color: {colors['text']}; background: {colors['surface']};
                border: 1px solid {colors['border']}; border-radius: 5px; padding: 10px;
            }}
            QTableWidget, QTextEdit, QLineEdit, QComboBox {{
                color: {colors['text']}; background: {colors['input']};
                border: 1px solid {colors['border']};
            }}
            QHeaderView::section {{
                color: {colors['text']}; background: {colors['surface_alt']};
                border: 1px solid {colors['border']}; padding: 6px;
            }}
            QPushButton {{
                min-height: 34px; padding: 0 14px; border-radius: 4px;
                color: {colors['text']}; background: {colors['surface_alt']};
                border: 1px solid {colors['border']}; font-weight: 600;
            }}
            QPushButton:hover {{ background: {colors['hover']}; }}
            QPushButton#PrimaryButton {{
                color: #ffffff; background: {colors['accent_button']}; border-color: {colors['accent_button']};
            }}
            QPushButton#PrimaryButton:disabled {{ color: {colors['text_muted']}; background: {colors['surface_alt']}; border-color: {colors['border']}; }}
            QSplitter::handle {{ background: {colors['border']}; width: 1px; }}
        """)


class RequirementReviewCard(QFrame):
    """Compact chat summary that opens the full specification workbench."""

    confirmed = pyqtSignal(object)
    revise_requested = pyqtSignal(str)
    draft_changed = pyqtSignal(object)
    draft_revise_requested = pyqtSignal(str, object)
    revise_with_draft_requested = pyqtSignal(str, object)

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
        self.setObjectName("SpecSummaryCard")
        self.analysis = copy.deepcopy(analysis or {})
        self.original_request = str(original_request or "")
        self.previous_spec = copy.deepcopy(previous_spec)
        self.plc_model = str(
            plc_model
            or self.analysis.get("plc_model")
            or (self.previous_spec or {}).get("plc_model")
            or "FX3U"
        ).upper()
        self.draft = _legacy.build_review_draft(self.analysis, self.previous_spec)
        self.draft["plc_model"] = self.plc_model
        self._draft_modified = False
        self._workbench = None
        self._theme = normalize_theme(get_theme_manager().current_theme)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel(tr('生成前规格确认'))
        title.setObjectName("SpecSummaryTitle")
        self.status = QLabel(tr('待确认'))
        self.status.setObjectName("SpecSummaryStatus")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.status)
        layout.addLayout(header)

        self.summary = QLabel()
        self.summary.setObjectName("SpecSummaryBody")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)

        actions = QHBoxLayout()
        actions.addStretch()
        self.open_button = QPushButton(tr('打开规格工作台'))
        self.open_button.setObjectName("PrimaryButton")
        set_codicon(self.open_button, "open-preview", tr('打开规格工作台'), 10)
        self.open_button.clicked.connect(self.open_workbench)
        actions.addWidget(self.open_button)
        layout.addLayout(actions)

        self._refresh_summary(self.draft)
        self.apply_theme(self._theme)

    def _validation_result(self, draft):
        try:
            return _legacy.validate_spec_draft(draft, self.plc_model)
        except Exception:
            return {"errors": [], "warnings": []}

    def _refresh_summary(self, draft):
        result = self._validation_result(draft)
        errors = list(result.get("errors") or [])
        warnings = list(result.get("warnings") or [])
        approach = draft.get("selected_approach") or {}
        approach_name = (
            _legacy.preferred_display_name(approach, kind=tr('方案'), index=1)
            if isinstance(approach, dict) and approach
            else tr('未选择')
        )
        summary_text = _legacy.naturalize_display_text(
            draft.get("summary")
            or self.analysis.get("summary")
            or tr('规格草案已生成')
        )
        lines = [summary_text]
        lines.append(
            tr('{v0} · {v1} · {v2} 项参数 · {v3} 项 I/O', v0=self.plc_model, v1=approach_name, v2=len(draft.get('parameters') or []), v3=len(draft.get('io_table') or []))
        )
        if errors:
            self.status.setText(tr('{v0} 项待处理', v0=len(errors)))
            self.status.setProperty("state", "error")
        elif warnings:
            self.status.setText(tr('可确认 · {v0} 个提醒', v0=len(warnings)))
            self.status.setProperty("state", "warning")
        else:
            self.status.setText(tr('可确认'))
            self.status.setProperty("state", "ok")
        self.summary.setText("\n".join(lines))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.update()

    def open_workbench(self):
        if self._workbench is not None and self._workbench.isVisible():
            self._workbench.raise_()
            self._workbench.activateWindow()
            return
        # Preserve the original full-vs-delta semantics on the first open.
        # Only reuse the in-session draft as the previous spec after the user
        # has actually edited/saved something in the workbench.
        previous = self.draft if self._draft_modified else self.previous_spec
        dialog = SpecificationWorkbenchDialog(
            self.analysis,
            self.original_request,
            previous,
            parent=self.window(),
            plc_model=self.plc_model,
        )
        self._workbench = dialog
        dialog.confirmed.connect(self._on_confirmed)
        dialog.revise_requested.connect(self.revise_requested.emit)
        dialog.draft_changed.connect(self._on_draft_changed)
        dialog.draft_revise_requested.connect(self.draft_revise_requested.emit)
        dialog.revise_with_draft_requested.connect(
            self.revise_with_draft_requested.emit
        )
        dialog.finished.connect(self._on_workbench_closed)
        dialog.show_full_size()

    def _on_workbench_closed(self, _result):
        self._workbench = None

    def _on_draft_changed(self, draft):
        self._draft_modified = True
        self.draft = copy.deepcopy(draft)
        self._refresh_summary(self.draft)
        self.draft_changed.emit(copy.deepcopy(draft))

    def _on_confirmed(self, spec):
        self.draft = copy.deepcopy(spec)
        self._draft_modified = False
        self._refresh_summary(self.draft)
        self.status.setText(tr('已确认'))
        self.status.setProperty("state", "ok")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.confirmed.emit(spec)

    def apply_theme(self, mode):
        self._theme = normalize_theme(mode)
        colors = theme_tokens(self._theme)
        self.setStyleSheet(f"""
            QFrame#SpecSummaryCard {{
                background: {colors['surface']}; border: 1px solid {colors['border']};
                border-left: 3px solid {colors['accent']}; border-radius: 6px;
            }}
            QLabel#SpecSummaryTitle {{ color: {colors['text_strong']}; font-size: 13px; font-weight: 700; }}
            QLabel#SpecSummaryBody {{ color: {colors['text']}; background: transparent; }}
            QLabel#SpecSummaryStatus {{
                color: #ffffff; background: {colors['accent_button']};
                border-radius: 9px; padding: 3px 8px; font-size: 10px; font-weight: 600;
            }}
            QLabel#SpecSummaryStatus[state="error"] {{ background: #c42b1c; }}
            QLabel#SpecSummaryStatus[state="warning"] {{ background: #9a6700; }}
            QLabel#SpecSummaryStatus[state="ok"] {{ background: #107c10; }}
            QPushButton#PrimaryButton {{
                min-height: 34px; padding: 0 14px; color: #ffffff;
                background: {colors['accent_button']}; border: 1px solid {colors['accent_button']};
                border-radius: 4px; font-weight: 600;
            }}
            QPushButton#PrimaryButton:hover {{ background: {colors['accent']}; }}
        """)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("_") and name not in {"copy", "importlib", "sys", "Path"}
)
