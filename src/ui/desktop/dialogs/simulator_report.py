"""Blue-and-white, user-facing Simulator2 result dialog."""

from __future__ import annotations

from shared.i18n import tr

import html
from pathlib import Path
from typing import Any, Mapping

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from shared.display_names import naturalize_identifier, preferred_display_name
from simulator.reporting import render_simulator_report_text


_STATUS_COLORS = {
    "passed": ("#107c10", "#dff6dd"),
    "failed": ("#a4262c", "#fde7e9"),
    "error": ("#a4262c", "#fde7e9"),
    "unavailable": ("#8a4b08", "#fff4ce"),
    "prepare_failed": ("#8a4b08", "#fff4ce"),
    "import_failed": ("#8a4b08", "#fff4ce"),
}


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _case_display_name(case: Mapping[str, Any], index: int | None = None) -> str:
    return preferred_display_name(
        case,
        kind=tr('测试项目'),
        index=index,
        descriptive_keys=("display_name", "description", "title", "label"),
    )


def _summary_html(report: Mapping[str, Any]) -> str:
    status = str(report.get("status") or "error")
    foreground, background = _STATUS_COLORS.get(status, ("#a4262c", "#fde7e9"))
    cases = [
        item for item in report.get("cases", []) or [] if isinstance(item, Mapping)
    ]
    problem_cases = [item for item in cases if item.get("status") != "passed"]
    issue_blocks = []
    for case_index, case in enumerate(problem_cases, start=1):
        friendly_issues = case.get("friendly_issues") or case.get("issues") or []
        issues = "".join(f"<li>{_escape(item)}</li>" for item in friendly_issues)
        issues = issues or tr('<li>执行器没有返回更具体的错误信息。</li>')
        stage = str(case.get("stage_label") or "")
        stage_line = (
            tr("<div class='meta'>未完成阶段：{v0}</div>", v0=_escape(stage))
            if stage and str(case.get("stage") or "") != "complete"
            else ""
        )
        timeline_rows = []
        for item in case.get("timeline", []) or []:
            if not isinstance(item, Mapping):
                continue
            item_status = str(item.get("status") or "action")
            marker = {"passed": "✓", "failed": "✕"}.get(item_status, "•")
            marker_color = {
                "passed": "#107c10",
                "failed": "#a4262c",
            }.get(item_status, "#0078d4")
            timeline_rows.append(
                "<tr>"
                f"<td class='time'>{int(item.get('at_ms') or 0)} ms</td>"
                f"<td class='marker' style='color:{marker_color}'>{marker}</td>"
                f"<td>{_escape(item.get('text'))}</td>"
                "</tr>"
            )
        timeline_html = ""
        if timeline_rows:
            timeline_html = (
                tr("<div class='timeline-title'>操作过程</div><table class='timeline'>")
                + "".join(timeline_rows)
                + "</table>"
            )
        issue_blocks.append(
            tr("<div class='issue'><div class='scenario-label'>测试场景</div><div class='issue-title'>{v0}</div>{v1}<div class='result-label'>异常结果</div><ul>{v2}</ul>{v3}</div>", v0=_escape(_case_display_name(case, case_index)), v1=stage_line, v2=issues, v3=timeline_html)
        )
    if not issue_blocks:
        if status == "passed":
            issue_blocks.append(
                tr("<div class='success'>所有已执行测试均符合预期，没有发现逻辑问题。</div>")
            )
        else:
            issue_blocks.append(
                tr("<div class='issue'><div class='result-label'>失败原因</div><div>{v0}</div></div>", v0=_escape(report.get('primary_reason')))
            )
    recommendations = [
        str(item)
        for item in report.get("recommendations", []) or []
        if str(item).strip()
    ]
    recommendation_html = ""
    if recommendations:
        recommendation_html = (
            tr('<h2>建议处理</h2><ol>')
            + "".join(f"<li>{_escape(item)}</li>" for item in recommendations)
            + "</ol>"
        )
    return tr("\n    <html><head><style>\n      body {{ color:#1e1e1e; font-family:'Microsoft YaHei UI','Segoe UI',sans-serif; font-size:13px; }}\n      h2 {{ color:#323130; font-size:15px; margin:18px 0 8px 0; }}\n      .conclusion {{ border-left:4px solid {v0}; background:{v1}; padding:11px 13px; margin:2px 0 14px 0; }}\n      .conclusion-title {{ color:{v2}; font-weight:700; margin-bottom:5px; }}\n      .issue {{ border:1px solid #edc7ca; background:#fffafb; padding:11px 13px; margin-bottom:10px; }}\n      .issue-title {{ color:#323130; font-weight:700; font-size:14px; margin:3px 0 7px 0; }}\n      .scenario-label, .result-label, .timeline-title {{ color:#605e5c; font-size:12px; font-weight:600; margin-top:7px; }}\n      .meta {{ color:#605e5c; margin-top:3px; }}\n      .success {{ border:1px solid #b7dfb5; background:#f3fbf2; color:#107c10; padding:10px 12px; }}\n      table.timeline {{ border-collapse:collapse; width:100%; margin-top:5px; }}\n      table.timeline td {{ border-top:1px solid #eadfe0; padding:7px 5px; vertical-align:top; }}\n      table.timeline td.time {{ width:64px; color:#605e5c; white-space:nowrap; }}\n      table.timeline td.marker {{ width:16px; font-weight:700; text-align:center; }}\n      li {{ margin:4px 0; }}\n    </style></head><body>\n      <div class='conclusion'>\n        <div class='conclusion-title'>测试结论</div>\n        <div>{v3}</div>\n      </div>\n      <h2>问题定位</h2>\n      {v4}\n      {v5}\n    </body></html>\n    ", v0=foreground, v1=background, v2=foreground, v3=_escape(report.get('primary_reason')), v4=''.join(issue_blocks), v5=recommendation_html)


def _technical_html(report: Mapping[str, Any]) -> str:
    cases = [
        item for item in report.get("cases", []) or [] if isinstance(item, Mapping)
    ]
    rows = []
    for case_index, case in enumerate(cases, start=1):
        passed = case.get("status") == "passed"
        color = "#107c10" if passed else "#a4262c"
        stage = str(case.get("stage_label") or "")
        issue = "；".join(
            str(item) for item in case.get("friendly_issues", []) or []
        )
        issue = issue or tr('测试通过')
        rows.append(
            "<tr>"
            f"<td><span style='color:{color};font-weight:600'>{_escape(case.get('status_label'))}</span></td>"
            f"<td>{_escape(_case_display_name(case, case_index))}</td>"
            f"<td>{_escape(stage)}</td>"
            f"<td>{_escape(issue)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append(tr("<tr><td colspan='4'>没有进入单项测试执行。</td></tr>"))
    workflow_errors = [
        str(item)
        for item in (
            report.get("display_workflow_errors")
            or report.get("workflow_errors")
            or []
        )
        if str(item).strip()
    ]
    errors = "<br>".join(_escape(item) for item in workflow_errors) or tr('无')
    evidence = tr('运行轨迹已保存，可通过窗口下方的“打开证据目录”查看。')
    if not report.get("evidence_path"):
        evidence = tr('本次没有可打开的持久化运行轨迹。')
    suite_name = report.get("suite_display_name") or naturalize_identifier(
        report.get("suite_name"), kind=tr('当前程序仿真测试')
    )
    return tr("\n    <html><head><style>\n      body {{ color:#1e1e1e; font-family:'Microsoft YaHei UI','Segoe UI',sans-serif; font-size:13px; }}\n      h2 {{ color:#323130; font-size:15px; margin:18px 0 8px 0; }}\n      table {{ border-collapse:collapse; width:100%; }}\n      th {{ background:#f3f6fb; text-align:left; }}\n      th, td {{ border:1px solid #d0d7e5; padding:7px 8px; vertical-align:top; }}\n      .raw {{ border:1px solid #d0d7e5; background:#f8fafc; padding:9px 12px; }}\n      code {{ color:#004578; font-family:Consolas,monospace; overflow-wrap:anywhere; }}\n    </style></head><body>\n      <h2>逐项执行结果</h2>\n      <table><tr><th>结果</th><th>测试项目</th><th>阶段</th><th>检查结果</th></tr>{v0}</table>\n      <h2>执行器返回信息</h2><div class='raw'>{v1}</div>\n      <h2>运行证据</h2><div class='raw'>{v2}</div>\n      <h2>测试方案</h2><div class='raw'>{v3}</div>\n    </body></html>\n    ", v0=''.join(rows), v1=errors, v2=evidence or tr('无持久化记录'), v3=_escape(suite_name))


def _expectations_html(report: Mapping[str, Any]) -> str:
    problem_cases = [
        item
        for item in report.get("problem_cases", []) or []
        if isinstance(item, Mapping)
    ]
    blocks = []
    for case_index, case in enumerate(problem_cases, start=1):
        rows = []
        for item in case.get("failed_expectations", []) or []:
            if not isinstance(item, Mapping):
                continue
            rows.append(
                "<tr>"
                f"<td>{int(item.get('at_ms') or 0)} ms</td>"
                f"<td>{_escape(item.get('device_name'))}</td>"
                f"<td class='expected'>{_escape(item.get('expected_text'))}</td>"
                f"<td class='actual'>{_escape(item.get('actual_text'))}</td>"
                "</tr>"
            )
        if rows:
            blocks.append(
                tr("<div class='case-title'>{v0}</div><table><tr><th>时间</th><th>检查对象</th><th>测试方案期望</th><th>仿真实际结果</th></tr>", v0=_escape(_case_display_name(case, case_index)))
                + "".join(rows)
                + "</table>"
            )
    if not blocks:
        blocks.append(tr("<div class='empty'>本次结果没有可对照的失败断言。</div>"))
    return tr("\n    <html><head><style>\n      body {{ color:#1e1e1e; font-family:'Microsoft YaHei UI','Segoe UI',sans-serif; font-size:13px; }}\n      .intro {{ border-left:4px solid #0078d4; background:#edf5ff; padding:10px 12px; margin-bottom:15px; }}\n      .case-title {{ color:#323130; font-size:14px; font-weight:700; margin:13px 0 7px 0; }}\n      table {{ border-collapse:collapse; width:100%; }}\n      th {{ background:#f3f6fb; text-align:left; }}\n      th, td {{ border:1px solid #d0d7e5; padding:8px 9px; vertical-align:top; }}\n      td.expected {{ color:#107c10; font-weight:600; }}\n      td.actual {{ color:#a4262c; font-weight:600; }}\n      .guide {{ border:1px solid #d0d7e5; background:#f8fafc; padding:10px 12px; margin-top:16px; }}\n      .empty {{ color:#605e5c; padding:12px; }}\n    </style></head><body>\n      <div class='intro'>这里显示测试方案原本要求的状态，以及 GX Simulator2 实际观察到的状态。</div>\n      {v0}\n      <div class='guide'><b>如何判断：</b><br>\n      如果“测试方案期望”符合你的控制需求，说明 PLC 程序行为需要检查；\n      如果期望本身不符合需求，应先修改测试方案，不能据此修改 PLC 程序。</div>\n    </body></html>\n    ", v0=''.join(blocks))


def _passed_cases_html(report: Mapping[str, Any]) -> str:
    passed_cases = [
        item
        for item in report.get("passed_cases", []) or []
        if isinstance(item, Mapping)
    ]
    items = "".join(
        f"<li>{_escape(_case_display_name(case, index))}</li>"
        for index, case in enumerate(passed_cases, start=1)
    )
    return (
        "<html><body style=\"font-family:'Microsoft YaHei UI','Segoe UI';"
        "font-size:12px;color:#323130\"><ul>"
        + items
        + "</ul></body></html>"
    )


class _MetricCard(QFrame):
    def __init__(self, caption: str, value: int, color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("SimulatorMetricCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(1)
        value_label = QLabel(str(int(value or 0)))
        value_label.setStyleSheet(f"font-size:20px;font-weight:700;color:{color};")
        caption_label = QLabel(caption)
        caption_label.setStyleSheet("color:#605e5c;font-size:12px;")
        layout.addWidget(value_label)
        layout.addWidget(caption_label)


class SimulatorReportDialog(QDialog):
    """Fully populated modal report shown after every simulator run."""

    def __init__(self, report: Mapping[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.report = dict(report or {})
        self.requested_action = ""
        self.setWindowTitle(tr('仿真结果报告'))
        self.setModal(True)
        self.resize(960, 720)
        self.setMinimumSize(780, 560)
        self._build_ui()

    def _build_ui(self) -> None:
        status = str(self.report.get("status") or "error")
        foreground, background = _STATUS_COLORS.get(
            status, ("#a4262c", "#fde7e9")
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel(tr('仿真结果报告'))
        title.setObjectName("SimulatorReportTitle")
        test_count = int(self.report.get("test_count") or 0)
        subtitle = QLabel(
            tr('共 {v0} 项功能测试', v0=test_count)
            if test_count
            else tr('当前程序仿真测试')
        )
        subtitle.setObjectName("SimulatorReportSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        status_label = QLabel(str(self.report.get("status_label") or status))
        status_label.setStyleSheet(
            f"color:{foreground};background:{background};border:1px solid {foreground};"
            "border-radius:12px;padding:5px 12px;font-weight:600;"
        )
        header.addLayout(titles, 1)
        header.addWidget(status_label, 0)
        root.addLayout(header)

        counts = self.report.get("counts") or {}
        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        for caption, value, color in (
            (tr('通过'), counts.get("passed", 0), "#107c10"),
            (tr('失败'), counts.get("failed", 0), "#a4262c"),
            (tr('错误'), counts.get("error", 0), "#a4262c"),
            (tr('未执行'), self.report.get("not_executed_count", 0), "#8a4b08"),
        ):
            metrics.addWidget(_MetricCard(caption, int(value or 0), color, self))
        root.addLayout(metrics)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("SimulatorReportTabs")
        summary_page = QWidget(self.tabs)
        summary_layout = QVBoxLayout(summary_page)
        summary_layout.setContentsMargins(0, 7, 0, 0)
        summary_layout.setSpacing(7)
        browser = QTextBrowser(summary_page)
        browser.setObjectName("SimulatorReportBrowser")
        browser.setOpenExternalLinks(False)
        browser.setHtml(_summary_html(self.report))
        summary_layout.addWidget(browser, 1)

        passed_count = len(self.report.get("passed_cases", []) or [])
        self.passed_details = QTextBrowser(summary_page)
        self.passed_details.setObjectName("SimulatorPassedDetails")
        self.passed_details.setMaximumHeight(145)
        self.passed_details.setHtml(_passed_cases_html(self.report))
        self.passed_details.setVisible(False)
        self.passed_toggle = QToolButton(summary_page)
        self.passed_toggle.setObjectName("SimulatorPassedToggle")
        self.passed_toggle.setCheckable(True)
        self.passed_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.passed_toggle.setText(tr('其余 {v0} 项测试通过（点击展开）', v0=passed_count))
        self.passed_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.passed_toggle.setVisible(passed_count > 0)
        self.passed_toggle.toggled.connect(self._toggle_passed_cases)
        summary_layout.addWidget(self.passed_toggle)
        summary_layout.addWidget(self.passed_details)

        technical_browser = QTextBrowser(self.tabs)
        technical_browser.setObjectName("SimulatorTechnicalDetails")
        technical_browser.setHtml(_technical_html(self.report))
        expectation_browser = QTextBrowser(self.tabs)
        expectation_browser.setObjectName("SimulatorExpectationDetails")
        expectation_browser.setHtml(_expectations_html(self.report))
        self.tabs.addTab(summary_page, tr('结果摘要'))
        self.tabs.addTab(expectation_browser, tr('测试期望'))
        self.tabs.addTab(technical_browser, tr('技术详情'))
        root.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        self.action_hint = QLabel("")
        self.action_hint.setObjectName("SimulatorReportActionHint")
        actions.addWidget(self.action_hint, 1)
        copy_button = QPushButton(tr('复制报告'))
        copy_button.clicked.connect(self._copy_report)
        actions.addWidget(copy_button)
        if status == "failed":
            expectation_button = QPushButton(tr('检查测试期望'))
            expectation_button.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
            actions.addWidget(expectation_button)
        evidence_button = QPushButton(tr('打开证据目录'))
        evidence_text = str(self.report.get("evidence_path") or "").strip()
        evidence_path = Path(evidence_text) if evidence_text else None
        evidence_button.setEnabled(bool(evidence_path and evidence_path.is_file()))
        evidence_button.clicked.connect(self._open_evidence_directory)
        actions.addWidget(evidence_button)
        if status == "failed" and str(self.report.get("run_id") or "").strip():
            debug_button = QPushButton(tr('进入故障调试'))
            debug_button.setObjectName("SimulatorReportPrimaryButton")
            debug_button.clicked.connect(self._request_debug)
            actions.addWidget(debug_button)
        close_button = QPushButton(tr('关闭'))
        if status != "failed":
            close_button.setObjectName("SimulatorReportPrimaryButton")
        close_button.setDefault(True)
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        root.addLayout(actions)

        self.setStyleSheet(
            """
            QDialog { background:#f8fafc; color:#1e1e1e; }
            QLabel#SimulatorReportTitle { color:#1e1e1e; font-size:21px; font-weight:650; }
            QLabel#SimulatorReportSubtitle { color:#605e5c; font-size:12px; }
            QLabel#SimulatorReportActionHint { color:#0078d4; font-size:12px; }
            QFrame#SimulatorMetricCard { background:#ffffff; border:1px solid #d0d7e5; border-radius:3px; }
            QTabWidget#SimulatorReportTabs::pane { border:1px solid #c8d1df; background:#ffffff; }
            QTabWidget#SimulatorReportTabs QTabBar::tab { min-width:92px; min-height:29px; padding:0 12px; background:#f3f6fb; border:1px solid #c8d1df; border-bottom:none; }
            QTabWidget#SimulatorReportTabs QTabBar::tab:selected { color:#0078d4; background:#ffffff; border-top:2px solid #0078d4; }
            QTextBrowser#SimulatorReportBrowser, QTextBrowser#SimulatorExpectationDetails, QTextBrowser#SimulatorTechnicalDetails { background:#ffffff; border:none; padding:9px; selection-background-color:#cde8ff; }
            QTextBrowser#SimulatorPassedDetails { background:#f8fafc; border:1px solid #d0d7e5; }
            QToolButton#SimulatorPassedToggle { color:#107c10; background:transparent; border:none; padding:5px 2px; font-weight:600; }
            QToolButton#SimulatorPassedToggle:hover { color:#0b6a0b; background:#f3fbf2; }
            QPushButton { min-height:30px; padding:0 14px; color:#1e1e1e; background:#ffffff; border:1px solid #8a9bb5; border-radius:2px; }
            QPushButton:hover { background:#edf5ff; border-color:#0078d4; }
            QPushButton:disabled { color:#a19f9d; background:#f3f2f1; border-color:#d2d0ce; }
            QPushButton#SimulatorReportPrimaryButton { color:#ffffff; background:#0078d4; border-color:#0078d4; }
            QPushButton#SimulatorReportPrimaryButton:hover { background:#106ebe; }
            """
        )

    def _toggle_passed_cases(self, expanded: bool) -> None:
        self.passed_details.setVisible(bool(expanded))
        self.passed_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        count = len(self.report.get("passed_cases", []) or [])
        self.passed_toggle.setText(
            tr('其余 {v0} 项测试通过（点击{v1}）', v0=count, v1=tr('收起') if expanded else tr('展开'))
        )

    def _copy_report(self) -> None:
        QApplication.clipboard().setText(render_simulator_report_text(self.report))
        self.action_hint.setText(tr('报告已复制到剪贴板'))

    def _request_debug(self) -> None:
        self.requested_action = "debug"
        self.accept()

    def _open_evidence_directory(self) -> None:
        path_text = str(self.report.get("evidence_path") or "").strip()
        path = Path(path_text) if path_text else None
        if path and path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


__all__ = ["SimulatorReportDialog"]
