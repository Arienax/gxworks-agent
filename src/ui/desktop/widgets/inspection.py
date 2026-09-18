"""Qt widgets for version inspection and guided fault-debugging context.

The widgets in this module deliberately accept plain dictionaries as well as
the dataclass-like report objects used by the inspection engine.  Keeping the
UI at this boundary makes persisted v1 reports and legacy debug reports render
without coupling the widgets to a particular model implementation.
"""

from __future__ import annotations

from shared.i18n import tr

from dataclasses import asdict, is_dataclass
from enum import Enum

from ui.desktop.qt import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    Qt,
    pyqtSignal,
)
from ui.desktop.icons import set_codicon
from shared.display_names import (
    naturalize_display_text,
    naturalize_identifier,
    version_display_name,
)
from rendering.display import rung_index_from_path
from inspection.presenter import (
    category_label,
    confidence_label,
    evidence_lines,
    location_text,
    resolution_label,
    rung_display_map,
    source_label,
    technical_location_tooltip,
)
from ui.desktop.theme import ThemeMode, get_theme_manager, normalize_theme


def _plain(value):
    """Return a JSON-like representation for dictionaries or model objects."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    for name in ("to_dict", "as_dict", "model_dump", "dict"):
        method = getattr(value, name, None)
        if callable(method):
            result = method()
            if isinstance(result, dict):
                return dict(result)
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _enum_text(value):
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "")


def _first(mapping, *keys, default=None):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _string_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [f"{key}: {item}" for key, item in value.items()]
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            if isinstance(item, dict):
                text = _first(
                    item,
                    "message",
                    "instruction",
                    "check",
                    "description",
                    "value",
                    "address",
                )
                rendered = str(text if text is not None else item)
                address = str(item.get("address") or "").strip()
                expected = str(item.get("expected") or "").strip()
                if address and address not in rendered:
                    rendered = f"{address}：{rendered}"
                if expected:
                    rendered += tr('（期望：{v0}）', v0=expected)
                result.append(rendered)
            elif str(item).strip():
                result.append(str(item))
        return result
    return [str(value)]


def _finding_location(finding):
    locations = finding.get("locations") or []
    location = _plain(locations[0]) if locations else {}
    rung = _first(finding, "rung_id", "rung", "ladder_rung")
    rung_ids = finding.get("rung_ids") or []
    if rung in (None, "") and rung_ids:
        rung = rung_ids[0]
    if rung in (None, ""):
        rung = _first(location, "rung_id", "rung", default="")
    related_rungs = finding.get("related_rungs") or []
    if rung in (None, "") and related_rungs:
        rung = related_rungs[0]
    path = _first(finding, "json_path", "path", default="")
    json_paths = finding.get("json_paths") or []
    if not path and json_paths:
        path = json_paths[0]
    if not path:
        path = _first(location, "json_path", "path", default="")
    addresses = _first(finding, "addresses", "address", default=[])
    if not addresses:
        addresses = _first(location, "addresses", "address", default=[])
    return str(rung or ""), str(path or ""), _string_list(addresses)


def _evidence_text(value):
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            values = _string_list(item)
            if values:
                parts.append(f"{key}: " + "；".join(values))
        return "\n".join(parts)
    return "\n".join(_string_list(value))


def _is_safety_finding(finding):
    if finding.get("safety_related") is True:
        return True
    haystack = " ".join(
        str(finding.get(key, ""))
        for key in ("category", "title", "message", "suggestion")
    ).lower()
    return any(
        term in haystack
        for term in (
            tr('急停'),
            tr('安全门'),
            tr('安全回路'),
            tr('安全类'),
            tr('限位'),
            "emergency stop",
            "safety gate",
            "safety circuit",
        )
    )


def _normalise_finding(raw, index, legacy=False):
    finding = _plain(raw)
    finding_id = _first(finding, "id", "finding_id", "stable_id")
    if not finding_id:
        finding_id = f"legacy-{index + 1}" if legacy else f"finding-{index + 1}"
    severity = _enum_text(_first(finding, "severity", "level", default="info")).lower()
    severity = {
        "error": "error",
        "错误": "error",
        "critical": "error",
        # ``high`` is a confidence value, not a hard-error severity.
        "high": "warning",
        "warning": "warning",
        "warn": "warning",
        "警告": "warning",
        "medium": "warning",
        "info": "info",
        "提示": "info",
        "suggestion": "info",
        "low": "info",
    }.get(severity, "info")
    evidence = _first(finding, "evidence", "reason", "details", "message", default="")
    suggestion = _first(
        finding,
        "suggestion",
        "recommended_change",
        "recommendation",
        default="",
    )
    rung, path, addresses = _finding_location(finding)
    explicit_complete = finding.get("evidence_complete")
    has_location = bool(rung or path or addresses)
    evidence_complete = (
        bool(explicit_complete)
        if explicit_complete is not None
        else bool(_evidence_text(evidence).strip() and has_location)
    )
    fix_instruction = str(
        _first(finding, "fix_instruction", "repair_instruction", default="") or ""
    ).strip()
    fixable = bool(_first(finding, "fixable", "can_fix", default=False))
    if legacy and finding.get("needs_fix"):
        fixable = True
    fixable = bool(
        fixable
        and evidence_complete
        and fix_instruction
        and not _is_safety_finding(finding)
    )
    return {
        **finding,
        "id": str(finding_id),
        "source": _enum_text(finding.get("source") or ("legacy" if legacy else "local")),
        "severity": severity,
        "category": str(finding.get("category") or tr('通用检查')),
        "title": str(_first(finding, "title", "message", default=tr('问题 {v0}', v0=index + 1))),
        "evidence": evidence,
        "suggestion": suggestion,
        "rung_id": rung,
        "json_path": path,
        "addresses": addresses,
        "fixable": fixable,
        "fix_instruction": fix_instruction,
        "confidence": _enum_text(finding.get("confidence") or ""),
        "resolution_status": _enum_text(
            _first(finding, "resolution_status", "resolved_status", default="")
        ),
    }


def normalise_inspection_report(report):
    """Normalise v1 and legacy debug reports for display only."""
    data = _plain(report)
    nested_base = _plain(data.get("base"))
    findings = data.get("findings") or []
    normalised = [
        _normalise_finding(item, index)
        for index, item in enumerate(findings)
        if isinstance(item, dict) or hasattr(item, "to_dict") or is_dataclass(item)
    ]

    # Old reports stored local findings and the AI diagnosis in separate fields.
    if not normalised:
        for item in data.get("local_findings") or []:
            if isinstance(item, dict):
                legacy_item = dict(item)
            else:
                legacy_item = {"message": str(item), "evidence": str(item)}
            normalised.append(_normalise_finding(legacy_item, len(normalised), True))

        if data.get("needs_fix") or data.get("possible_causes") or data.get("recommended_changes"):
            rungs = data.get("related_rungs") or []
            summary = str(data.get("summary") or tr('旧版故障调试结果'))
            causes = _string_list(data.get("possible_causes"))
            changes = _string_list(data.get("recommended_changes"))
            legacy_item = {
                "id": "legacy-debug-finding",
                "source": "ai",
                "severity": "warning" if data.get("needs_fix") else "info",
                "category": tr('故障诊断'),
                "title": summary,
                "evidence": "；".join(causes) or summary,
                "suggestion": "；".join(changes),
                "related_rungs": rungs,
                "needs_fix": bool(data.get("needs_fix")),
                "fix_instruction": data.get("fix_instruction", ""),
                "evidence_complete": bool((causes or summary) and rungs),
            }
            normalised.append(_normalise_finding(legacy_item, len(normalised), True))

    status = _enum_text(
        _first(data, "status", "execution_status", "completion_status", default="complete")
    ).lower()
    if not data.get("findings") and (
        data.get("possible_causes") is not None or data.get("needs_fix") is not None
    ):
        status = "complete"
    report_type = _enum_text(_first(data, "report_type", "type", default="debug")).lower()
    return {
        **data,
        "id": str(_first(data, "id", "report_id", default="") or ""),
        "report_type": report_type,
        "status": status,
        "base_version_id": str(
            _first(
                data,
                "base_version_id",
                "version_id",
                default=_first(nested_base, "version_id", "base_version_id", default=""),
            )
            or ""
        ),
        "base_json_hash": str(
            _first(
                data,
                "base_json_hash",
                "json_hash",
                default=_first(nested_base, "json_hash", "base_json_hash", default=""),
            )
            or ""
        ),
        "plc_model": str(data.get("plc_model") or nested_base.get("plc_model") or ""),
        "summary": str(data.get("summary") or tr('未提供报告摘要')),
        "findings": normalised,
    }


class _FindingWidget(QFrame):
    locate_clicked = pyqtSignal(str, str)
    selection_changed = pyqtSignal()
    EVIDENCE_PREVIEW_LIMIT = 4

    def __init__(self, finding, parent=None):
        super().__init__(parent)
        self.finding = finding
        self.setObjectName("InspectionFinding")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(5)

        header = QHBoxLayout()
        self.checkbox = None
        if finding.get("fixable"):
            self.checkbox = QCheckBox()
            self.checkbox.setObjectName("FindingCheckBox")
            self.checkbox.setToolTip(tr('选择后可生成修复版本；默认不选择'))
            self.checkbox.stateChanged.connect(lambda _state: self.selection_changed.emit())
            header.addWidget(self.checkbox)
        title = QLabel(
            naturalize_display_text(
                finding.get("title") or finding.get("category") or tr('未命名问题')
            )
        )
        title.setObjectName("FindingTitle")
        title.setWordWrap(True)
        header.addWidget(title, 1)
        self.source_label = QLabel(source_label(finding.get("source") or "local"))
        self.source_label.setObjectName("FindingSource")
        header.addWidget(self.source_label)
        layout.addLayout(header)

        meta = []
        if finding.get("category"):
            meta.append(category_label(finding["category"]))
        if finding.get("confidence"):
            meta.append(tr('置信度 {v0}', v0=confidence_label(finding['confidence'])))
        if finding.get("resolution_status"):
            meta.append(tr('状态 {v0}', v0=resolution_label(finding['resolution_status'])))
        self.meta_label = None
        if meta:
            self.meta_label = QLabel(" · ".join(meta))
            self.meta_label.setObjectName("FindingMeta")
            self.meta_label.setWordWrap(True)
            layout.addWidget(self.meta_label)

        self._evidence_lines = evidence_lines(finding)
        self._evidence_expanded = False
        self.evidence_label = None
        self.evidence_toggle_button = None
        if self._evidence_lines:
            self.evidence_label = QLabel()
            self.evidence_label.setObjectName("FindingEvidence")
            self.evidence_label.setWordWrap(True)
            self.evidence_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(self.evidence_label)
            if len(self._evidence_lines) > self.EVIDENCE_PREVIEW_LIMIT:
                hidden_count = len(self._evidence_lines) - self.EVIDENCE_PREVIEW_LIMIT
                self.evidence_toggle_button = QPushButton(tr('展开其余 {v0} 处', v0=hidden_count))
                self.evidence_toggle_button.setObjectName("SecondaryButton")
                self.evidence_toggle_button.setToolTip(tr('展开或收起全部证据位置'))
                self.evidence_toggle_button.setSizePolicy(
                    QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
                )
                self.evidence_toggle_button.clicked.connect(self._toggle_evidence)
                toggle_row = QHBoxLayout()
                toggle_row.setContentsMargins(0, 0, 0, 0)
                toggle_row.addWidget(self.evidence_toggle_button)
                toggle_row.addStretch()
                layout.addLayout(toggle_row)
            self._update_evidence_text()

        suggestion = _evidence_text(finding.get("suggestion"))
        if suggestion:
            suggestion_label = QLabel(
                tr('建议：{v0}', v0=naturalize_display_text(suggestion))
            )
            suggestion_label.setObjectName("FindingSuggestion")
            suggestion_label.setWordWrap(True)
            suggestion_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(suggestion_label)

        rung = finding.get("_locate_raw_rung_id", finding.get("rung_id", ""))
        path = finding.get("json_path", "")
        visible_location = location_text(finding)
        self.location_label = None
        if visible_location:
            location_row = QHBoxLayout()
            self.location_label = QLabel(visible_location)
            self.location_label.setObjectName("FindingLocation")
            self.location_label.setWordWrap(True)
            tooltip = technical_location_tooltip(finding)
            if tooltip:
                self.location_label.setToolTip(tooltip)
            location_row.addWidget(self.location_label, 1)
            if rung or path:
                self.locate_button = QPushButton(tr('定位'))
                self.locate_button.setObjectName("SecondaryButton")
                set_codicon(self.locate_button, "preview", tr('定位'), 8)
                self.locate_button.clicked.connect(
                    lambda: self.locate_clicked.emit(str(rung or ""), str(path or ""))
                )
                location_row.addWidget(self.locate_button)
            else:
                self.locate_button = None
            layout.addLayout(location_row)
        else:
            self.locate_button = None

    def _update_evidence_text(self):
        if self.evidence_label is None:
            return
        limit = len(self._evidence_lines) if self._evidence_expanded else self.EVIDENCE_PREVIEW_LIMIT
        visible = self._evidence_lines[:limit]
        if len(visible) == 1:
            rendered = tr('证据：') + visible[0]
        else:
            rendered = tr('证据：\n') + "\n".join("• " + item for item in visible)
        self.evidence_label.setText(rendered)
        if self.evidence_toggle_button is not None:
            if self._evidence_expanded:
                self.evidence_toggle_button.setText(tr('收起证据'))
            else:
                hidden_count = len(self._evidence_lines) - self.EVIDENCE_PREVIEW_LIMIT
                self.evidence_toggle_button.setText(tr('展开其余 {v0} 处', v0=hidden_count))

    def _toggle_evidence(self):
        self._evidence_expanded = not self._evidence_expanded
        self._update_evidence_text()

    def is_selected(self):
        return bool(self.checkbox and self.checkbox.isChecked())


class InspectionReportCard(QFrame):
    """Unified card for local/AI review and fault-debugging reports."""

    locate_requested = pyqtSignal(str, str, str)
    repair_requested = pyqtSignal(str, object)
    retry_ai_requested = pyqtSignal(str)

    # Compatibility with the previous DebugReportCard public surface.
    fix_requested = pyqtSignal(object)
    copy_fix_requested = pyqtSignal(str)

    STATUS_TEXT = {
        "complete": tr('已完成'),
        "completed": tr('已完成'),
        "success": tr('已完成'),
        "local_only": tr('仅本地'),
        "local": tr('仅本地'),
        "partial": tr('部分完成'),
        "partially_complete": tr('部分完成'),
        "failed": tr('执行失败'),
        "error": tr('执行失败'),
        "running": tr('检查中'),
        "pending": tr('等待检查'),
        "unsupported": tr('当前类型不支持'),
    }

    def __init__(
        self,
        report,
        latest_version_id=None,
        parent=None,
        current_version_id=None,
        base_ladder=None,
    ):
        super().__init__(parent)
        self.source_report = report
        self.report = normalise_inspection_report(report)
        verified_rungs = rung_display_map(base_ladder)
        if verified_rungs.get("by_index"):
            for finding in self.report.get("findings", []):
                finding["_rung_display_map"] = verified_rungs
                finding["_base_ladder"] = base_ladder
                path = str(
                    finding.get("json_path")
                    or finding.get("path")
                    or ""
                )
                index = rung_index_from_path(path)
                location = (
                    verified_rungs["by_index"].get(index)
                    if index is not None
                    else None
                )
                if isinstance(location, dict) and location.get("raw_rung_id") not in (
                    None,
                    "",
                ):
                    # Path is bound to the report version and therefore wins
                    # over a stale/display-only rung number from an AI result.
                    finding["_locate_raw_rung_id"] = location["raw_rung_id"]
        self.latest_version_id = latest_version_id
        self.current_version_id = current_version_id
        self.finding_widgets = {}
        self.setObjectName("InspectionReportCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        report_type = self.report.get("report_type", "")
        title_text = tr('故障调试报告') if "debug" in report_type or "fault" in report_type else tr('版本评审报告')
        self.title_label = QLabel(title_text)
        self.title_label.setObjectName("InspectionTitle")
        header.addWidget(self.title_label)
        version = self.report.get("base_version_id") or "-"
        model = self.report.get("plc_model")
        version_label = version_display_name(version)
        target = f"{version_label} · {model}" if model else version_label
        self.version_badge = QLabel(tr('基于 {v0}', v0=target))
        self.version_badge.setObjectName("InspectionBadge")
        header.addWidget(self.version_badge)
        header.addStretch()
        status = self.STATUS_TEXT.get(
            self.report.get("status"),
            naturalize_identifier(self.report.get("status"), kind=tr('未知')),
        )
        self.status_label = QLabel(status)
        self.status_label.setObjectName("InspectionStatus")
        header.addWidget(self.status_label)
        layout.addLayout(header)

        hash_value = self.report.get("base_json_hash")
        if hash_value:
            hash_label = QLabel(tr('程序内容：已与该版本精确绑定'))
            hash_label.setObjectName("InspectionHash")
            hash_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(hash_label)

        self.summary_label = QLabel(
            naturalize_display_text(
                self.report.get("summary") or tr('未提供报告摘要')
            )
        )
        self.summary_label.setObjectName("InspectionSummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary_label)

        trigger_text = {
            "automatic": tr('自动基础评审'),
            "manual": tr('手动检查'),
            "ai_retry": tr('AI 重试'),
            "legacy": tr('旧版报告'),
        }.get(
            str(self.report.get("trigger") or ""),
            naturalize_identifier(self.report.get("trigger"), kind=tr('未知来源')),
        )
        depth_text = {
            "basic": tr('本地基础'),
            "deep": tr('本地 + AI 深查'),
        }.get(
            str(self.report.get("depth") or ""),
            naturalize_identifier(self.report.get("depth"), kind="")
            if self.report.get("depth")
            else "",
        )
        source_parts = [tr('来源：{v0}', v0=trigger_text)]
        if depth_text:
            source_parts.append(tr('深度：{v0}', v0=depth_text))
        self.source_label = QLabel(" · ".join(source_parts))
        self.source_label.setObjectName("InspectionSource")
        layout.addWidget(self.source_label)

        counts = self.finding_counts()
        self.count_label = QLabel(
            tr('错误 {v0}  ·  警告 {v1}  ·  提示 {v2}', v0=counts['error'], v1=counts['warning'], v2=counts['info'])
        )
        self.count_label.setObjectName("InspectionCounts")
        layout.addWidget(self.count_label)

        for severity, label in (("error", tr('错误')), ("warning", tr('警告')), ("info", tr('提示'))):
            findings = [item for item in self.report["findings"] if item["severity"] == severity]
            if not findings:
                continue
            group = QGroupBox(f"{label} ({len(findings)})")
            group.setObjectName(f"FindingGroup_{severity}")
            group_layout = QVBoxLayout(group)
            for finding in findings:
                widget = _FindingWidget(finding)
                widget.selection_changed.connect(self._update_repair_button)
                widget.locate_clicked.connect(
                    lambda rung, path, version=version: self.locate_requested.emit(
                        str(version), str(rung), str(path)
                    )
                )
                self.finding_widgets[finding["id"]] = widget
                group_layout.addWidget(widget)
            layout.addWidget(group)

        checks = _first(
            self.report,
            "online_checks",
            "online_verification",
            "online_check_items",
            default=[],
        )
        check_lines = _string_list(checks)
        if check_lines:
            check_group = QGroupBox(tr('在线核查步骤（需人工执行）'))
            check_layout = QVBoxLayout(check_group)
            check_label = QLabel("\n".join(f"{index + 1}. {item}" for index, item in enumerate(check_lines)))
            check_label.setObjectName("OnlineChecks")
            check_label.setWordWrap(True)
            check_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            check_layout.addWidget(check_label)
            layout.addWidget(check_group)

        actions = QHBoxLayout()
        # Give conditionally visible actions a parent before calling setVisible().
        # Otherwise Qt briefly treats a visible, unparented button as its own
        # top-level window while persisted report cards are being rebuilt.
        self.retry_button = QPushButton(tr('重试 AI 深查'), self)
        self.retry_button.setObjectName("SecondaryButton")
        set_codicon(self.retry_button, "sync", tr('重试 AI 深查'), 9)
        retry_states = {"local_only", "local", "partial", "partially_complete", "failed", "error"}
        ai_error = _first(self.report, "ai_error", "error_message", default="")
        self.retry_button.setVisible(self.report.get("status") in retry_states or bool(ai_error))
        self.retry_button.clicked.connect(
            lambda: self.retry_ai_requested.emit(self.report.get("id", ""))
        )
        actions.addWidget(self.retry_button)

        legacy_instruction = str(self.report.get("fix_instruction") or "").strip()
        self.copy_button = QPushButton(tr('复制修复要求到输入框'), self)
        self.copy_button.setObjectName("SecondaryButton")
        set_codicon(self.copy_button, "copy", tr('复制修复要求到输入框'), 9)
        self.copy_button.setVisible(bool(legacy_instruction))
        self.copy_button.clicked.connect(
            lambda: self.copy_fix_requested.emit(legacy_instruction)
        )
        actions.addWidget(self.copy_button)
        actions.addStretch()

        self.repair_button = QPushButton(tr('生成所选问题的修复版本'))
        self.repair_button.setObjectName("PrimaryButton")
        set_codicon(self.repair_button, "tools", tr('生成所选问题的修复版本'), 9)
        self.repair_button.clicked.connect(self._request_repair)
        actions.addWidget(self.repair_button)
        layout.addLayout(actions)

        self._update_repair_button()
        self.apply_theme(get_theme_manager().current_theme)

    def finding_counts(self):
        counts = {"error": 0, "warning": 0, "info": 0}
        for finding in self.report.get("findings", []):
            counts[finding.get("severity", "info")] += 1
        return counts

    def selected_finding_ids(self):
        return [
            finding_id
            for finding_id, widget in self.finding_widgets.items()
            if widget.is_selected()
        ]

    def _update_repair_button(self):
        selected = self.selected_finding_ids()
        self.repair_button.setEnabled(bool(selected))
        if selected:
            self.repair_button.setText(tr('生成修复版本（已选 {v0} 项）', v0=len(selected)))
        else:
            self.repair_button.setText(tr('生成所选问题的修复版本'))

    def _request_repair(self):
        selected = self.selected_finding_ids()
        if not selected:
            return
        report_id = self.report.get("id", "")
        self.repair_requested.emit(report_id, selected)
        # Old callers expect the whole report and perform their own confirmation.
        legacy_report = _plain(self.source_report) or dict(self.report)
        legacy_report["selected_finding_ids"] = list(selected)
        self.fix_requested.emit(legacy_report)

    def apply_theme(self, mode):
        if normalize_theme(mode) == ThemeMode.LIGHT:
            self.setStyleSheet("""
                QFrame#InspectionReportCard { background: #ffffff; border: 1px solid #cccedb; border-left: 3px solid #0078d4; border-radius: 4px; }
                QFrame#InspectionReportCard QLabel { color: #1e1e1e; background: transparent; }
                QLabel#InspectionTitle { font-size: 13px; font-weight: 600; }
                QLabel#InspectionBadge, QLabel#InspectionStatus { color: #005a9e; background: #e5f1fb; border: 1px solid #9cc2e5; border-radius: 8px; padding: 2px 7px; }
                QLabel#InspectionSummary { background: #f5f5f5; border: 1px solid #cccedb; border-radius: 3px; padding: 8px; }
                QFrame#InspectionFinding { background: #fafafa; border: 1px solid #dddddd; border-radius: 3px; }
                QLabel#FindingTitle { font-weight: 600; }
                QLabel#FindingMeta, QLabel#FindingLocation, QLabel#InspectionHash { color: #616161; }
                QLabel#FindingSource { color: #005a9e; }
            """)
            return
        self.setStyleSheet("""
            QFrame#InspectionReportCard { background: #1f1f1f; border: 1px solid #3c3c3c; border-left: 3px solid #0e639c; border-radius: 5px; }
            QFrame#InspectionReportCard QLabel { color: #d4d4d4; background: transparent; }
            QLabel#InspectionTitle { color: #ffffff; font-size: 13px; font-weight: 600; }
            QLabel#InspectionBadge, QLabel#InspectionStatus { color: #9cdcfe; background: #26384a; border: 1px solid #305d83; border-radius: 8px; padding: 2px 7px; }
            QLabel#InspectionSummary { background: #252526; border: 1px solid #3c3c3c; border-radius: 4px; padding: 8px; }
            QFrame#InspectionFinding { background: #252526; border: 1px solid #3c3c3c; border-radius: 3px; }
            QLabel#FindingTitle { color: #ffffff; font-weight: 600; }
            QLabel#FindingMeta, QLabel#FindingLocation, QLabel#InspectionHash { color: #a0a0a0; }
            QLabel#FindingSource { color: #9cdcfe; }
            QPushButton#SecondaryButton { color: #cccccc; background: #313131; }
            QPushButton#PrimaryButton { color: #ffffff; background: #0e639c; }
            QPushButton:disabled { color: #777777; background: #2a2a2a; }
        """)


class DebugReportCard(InspectionReportCard):
    """Backward-compatible import name for persisted legacy debug messages."""


class DebugContextWidget(QFrame):
    """Structured, manually entered context for a fault-debugging request."""

    context_changed = pyqtSignal(object)

    def __init__(self, context=None, parent=None):
        super().__init__(parent)
        self._updating = False
        self.setObjectName("DebugContextWidget")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(8)

        notice = QLabel(tr('仅记录人工观测信息；不会连接、下载或控制 PLC。'))
        notice.setObjectName("DebugContextNotice")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        self.expected_edit = self._add_text_field(
            layout,
            tr('期望行为'),
            tr('例如：按下启动后，Y0 保持运行直至停止信号到达'),
        )
        self.condition_edit = self._add_text_field(
            layout,
            tr('发生条件'),
            tr('例如：仅在回零完成后的第二次启动出现'),
        )
        self.trigger_condition_edit = self.condition_edit

        observation_group = QGroupBox(tr('人工观测值'))
        observation_layout = QVBoxLayout(observation_group)
        self.observation_table = QTableWidget(0, 3)
        self.observations_table = self.observation_table
        self.observation_table.setHorizontalHeaderLabels([tr('地址'), tr('值'), tr('观测时刻')])
        self.observation_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.observation_table.verticalHeader().setVisible(False)
        self.observation_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.observation_table.setMinimumHeight(92)
        self.observation_table.setMaximumHeight(170)
        self.observation_table.itemChanged.connect(self._emit_changed)
        observation_layout.addWidget(self.observation_table)

        observation_actions = QHBoxLayout()
        observation_actions.addStretch()
        self.add_observation_button = QPushButton(tr('新增观测'))
        self.add_observation_button.setObjectName("SecondaryButton")
        self.remove_observation_button = QPushButton(tr('删除选中'))
        self.remove_observation_button.setObjectName("SecondaryButton")
        self.add_observation_button.clicked.connect(self.add_observation)
        self.remove_observation_button.clicked.connect(self.remove_selected_observations)
        observation_actions.addWidget(self.add_observation_button)
        observation_actions.addWidget(self.remove_observation_button)
        observation_layout.addLayout(observation_actions)
        layout.addWidget(observation_group)

        self.recent_changes_edit = self._add_text_field(
            layout,
            tr('最近改动'),
            tr('例如：调整了 T0 设定值，或更换了 X3 传感器'),
        )
        self.notes_edit = self._add_text_field(
            layout,
            tr('补充说明'),
            tr('补充复现步骤、现场限制或希望优先核查的方向'),
        )

        for editor in (
            self.expected_edit,
            self.condition_edit,
            self.recent_changes_edit,
            self.notes_edit,
        ):
            editor.textChanged.connect(self._emit_changed)

        if context:
            self.set_context(context)
        self.apply_theme(get_theme_manager().current_theme)

    @staticmethod
    def _add_text_field(layout, title, placeholder):
        group = QGroupBox(title)
        group_layout = QVBoxLayout(group)
        editor = QTextEdit()
        editor.setMaximumHeight(65)
        editor.setPlaceholderText(placeholder)
        group_layout.addWidget(editor)
        layout.addWidget(group)
        return editor

    @staticmethod
    def _item(value=""):
        return QTableWidgetItem(str(value or ""))

    def add_observation(self, address="", value="", when=""):
        self._updating = True
        try:
            row = self.observation_table.rowCount()
            self.observation_table.insertRow(row)
            self.observation_table.setItem(row, 0, self._item(address))
            self.observation_table.setItem(row, 1, self._item(value))
            self.observation_table.setItem(row, 2, self._item(when))
        finally:
            self._updating = False
        self._emit_changed()

    def remove_selected_observations(self):
        rows = sorted(
            {item.row() for item in self.observation_table.selectedItems()},
            reverse=True,
        )
        if not rows:
            return
        self._updating = True
        try:
            for row in rows:
                self.observation_table.removeRow(row)
        finally:
            self._updating = False
        self._emit_changed()

    def _cell_text(self, row, column):
        item = self.observation_table.item(row, column)
        return item.text().strip() if item else ""

    def to_dict(self):
        observed = []
        for row in range(self.observation_table.rowCount()):
            item = {
                "address": self._cell_text(row, 0).upper().replace(" ", ""),
                "value": self._cell_text(row, 1),
                "when": self._cell_text(row, 2),
            }
            if any(item.values()):
                observed.append(item)
        return {
            "expected_behavior": self.expected_edit.toPlainText().strip(),
            "trigger_condition": self.condition_edit.toPlainText().strip(),
            "observed_values": observed,
            "recent_changes": self.recent_changes_edit.toPlainText().strip(),
            "notes": self.notes_edit.toPlainText().strip(),
        }

    def set_context(self, context):
        data = _plain(context)
        self._updating = True
        try:
            self.expected_edit.setPlainText(
                str(_first(data, "expected_behavior", "expected", default="") or "")
            )
            self.condition_edit.setPlainText(
                str(
                    _first(
                        data,
                        "trigger_condition",
                        "occurrence_conditions",
                        "conditions",
                        "condition",
                        default="",
                    )
                    or ""
                )
            )
            self.recent_changes_edit.setPlainText(
                str(_first(data, "recent_changes", "changes", default="") or "")
            )
            self.notes_edit.setPlainText(
                str(_first(data, "notes", "additional_notes", default="") or "")
            )
            self.observation_table.setRowCount(0)
            observations = _first(
                data,
                "observed_values",
                "observations",
                "observed_addresses",
                default=[],
            )
            if isinstance(observations, dict):
                observations = [
                    {"address": address, "value": value}
                    for address, value in observations.items()
                ]
            for item in observations or []:
                observation = _plain(item)
                row = self.observation_table.rowCount()
                self.observation_table.insertRow(row)
                self.observation_table.setItem(
                    row, 0, self._item(_first(observation, "address", "device", default=""))
                )
                self.observation_table.setItem(
                    row, 1, self._item(_first(observation, "value", "state", default=""))
                )
                self.observation_table.setItem(
                    row,
                    2,
                    self._item(
                        _first(observation, "when", "observed_at", "timestamp", default="")
                    ),
                )
        finally:
            self._updating = False
        self._emit_changed()

    def clear(self):
        self.set_context({})

    def _emit_changed(self, *_args):
        if not self._updating:
            self.context_changed.emit(self.to_dict())

    def apply_theme(self, mode):
        if normalize_theme(mode) == ThemeMode.LIGHT:
            self.setStyleSheet("""
                QFrame#DebugContextWidget { background: #ffffff; border: 1px solid #cccedb; border-radius: 4px; }
                QFrame#DebugContextWidget QLabel, QFrame#DebugContextWidget QGroupBox { color: #1e1e1e; }
                QLabel#DebugContextNotice { color: #664d03; background: #fff4ce; border: 1px solid #e7c65f; border-radius: 3px; padding: 6px; }
                QFrame#DebugContextWidget QTextEdit, QFrame#DebugContextWidget QTableWidget { color: #1e1e1e; background: #ffffff; border: 1px solid #cccedb; }
                QFrame#DebugContextWidget QHeaderView::section { color: #1e1e1e; background: #f3f3f3; border: 1px solid #cccedb; padding: 4px; }
            """)
            return
        self.setStyleSheet("""
            QFrame#DebugContextWidget { background: #252526; border: 1px solid #3c3c3c; border-radius: 4px; }
            QFrame#DebugContextWidget QLabel, QFrame#DebugContextWidget QGroupBox { color: #d4d4d4; }
            QLabel#DebugContextNotice { color: #f0d97a; background: #3d3318; border: 1px solid #6b5717; border-radius: 3px; padding: 6px; }
            QFrame#DebugContextWidget QTextEdit, QFrame#DebugContextWidget QTableWidget { color: #cccccc; background: #1f1f1f; border: 1px solid #3c3c3c; }
            QFrame#DebugContextWidget QHeaderView::section { color: #cccccc; background: #2d2d2d; border: 1px solid #3c3c3c; padding: 4px; }
            QPushButton#SecondaryButton { color: #cccccc; background: #313131; }
        """)


__all__ = [
    "DebugContextWidget",
    "DebugReportCard",
    "InspectionReportCard",
    "normalise_inspection_report",
]
