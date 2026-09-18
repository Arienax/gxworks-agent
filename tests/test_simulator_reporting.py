import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QTabWidget, QTextBrowser

from simulator.reporting import build_simulator_report, render_simulator_report_text
from ui.desktop.dialogs.simulator_report import SimulatorReportDialog


def _workflow(result, *, message="仿真测试已结束。", run_id="sim_fixture"):
    return {
        "status": result["status"],
        "message": message,
        "execution": {
            "result": result,
            "record": {"run_id": run_id, "suite_name": result.get("name", "")},
        },
    }


def test_report_exposes_failed_assertion_instead_of_only_summary_counts(tmp_path):
    evidence = tmp_path / "result.json"
    evidence.write_text("{}", encoding="utf-8")
    result = {
        "name": "three pump regression",
        "status": "failed",
        "counts": {"passed": 5, "failed": 1, "error": 0, "unavailable": 0},
        "test_count": 6,
        "attempted_count": 6,
        "executed_count": 6,
        "not_executed_count": 0,
        "results": [
            {
                "name": "backup and reset",
                "status": "failed",
                "setup_stage": "complete",
                "execution_started": True,
                "error": "",
                "assertions": [
                    {
                        "step_id": "restored",
                        "at_ms": 700,
                        "address": "Y1",
                        "passed": False,
                        "detail": "actual=1, eq 0, tolerance=0.0",
                    }
                ],
                "invariant_violations": [],
            }
        ],
    }

    suite = {
        "tests": [
            {
                "name": "backup and reset",
                "description": "主泵故障后切换备用泵，复位后恢复主泵运行",
                "steps": [
                    {"id": "reset_pulse", "at_ms": 600, "set": {"X11": 1}},
                    {
                        "id": "restored",
                        "at_ms": 700,
                        "set": {},
                        "expect": [{"address": "Y1", "operator": "eq", "value": 0}],
                    },
                ],
            }
        ]
    }
    program = {
        "devices": {
            "X11": {"comment": "故障复位按钮"},
            "Y1": {"comment": "2号泵接触器"},
        }
    }
    report = build_simulator_report(
        _workflow(result),
        evidence_path=evidence,
        suite=suite,
        program=program,
    )
    text = render_simulator_report_text(report)

    assert report["primary_reason"] == "2号泵接触器（Y1）实际仍处于接通状态，预期应断开"
    assert report["cases"][0]["display_name"] == "主泵故障后切换备用泵，复位后恢复主泵运行"
    assert report["cases"][0]["timeline"][0]["text"] == "按下故障复位按钮（X11）"
    assert report["cases"][0]["failed_expectations"][0]["expected_text"] == "断开"
    assert report["cases"][0]["failed_expectations"][0]["actual_text"] == "接通"
    assert report["counts"]["passed"] == 5
    assert "2号泵接触器（Y1）实际仍处于接通状态，预期应断开" in text
    assert "700 ms：状态检查失败" in text
    assert "restored" not in text
    assert "backup and reset" not in text
    assert str(evidence.resolve()) not in text
    assert "运行证据：已保存" in text


def test_report_exposes_environment_stage_and_exact_gateway_error():
    error = "Unknown gateway endpoint. [NOT_FOUND]"
    result = {
        "name": "fixture",
        "status": "unavailable",
        "counts": {"passed": 0, "failed": 0, "error": 0, "unavailable": 1},
        "test_count": 6,
        "attempted_count": 1,
        "executed_count": 0,
        "not_executed_count": 6,
        "error": error,
        "results": [
            {
                "name": "first",
                "status": "unavailable",
                "setup_stage": "cpu_reset",
                "execution_started": False,
                "environment_failure": True,
                "error": error,
                "assertions": [],
                "invariant_violations": [],
            }
        ],
    }

    report = build_simulator_report(_workflow(result, message="仿真环境尚未就绪。"))
    text = render_simulator_report_text(report)

    assert report["primary_reason"] == "Unknown gateway endpoint. [未找到]"
    assert report["cases"][0]["stage_label"] == "复位仿真 CPU"
    assert report["not_executed_count"] == 6
    assert "版本" in report["recommendations"][0] or "修复版" in report["recommendations"][0]
    assert "复位仿真 CPU" in text


def test_report_dialog_is_fully_populated_before_it_is_shown():
    app = QApplication.instance() or QApplication([])
    result = {
        "name": "fixture",
        "status": "error",
        "counts": {"passed": 0, "failed": 0, "error": 1, "unavailable": 0},
        "test_count": 0,
        "attempted_count": 0,
        "executed_count": 0,
        "not_executed_count": 0,
        "error": "MX Component connection failed",
        "results": [],
    }
    report = build_simulator_report(_workflow(result, message="仿真测试执行失败。"))

    dialog = SimulatorReportDialog(report)
    browser = dialog.findChild(QTextBrowser, "SimulatorReportBrowser")
    technical = dialog.findChild(QTextBrowser, "SimulatorTechnicalDetails")

    assert browser is not None
    assert "MX Component connection failed" in browser.toPlainText()
    assert technical is not None
    assert "执行器返回信息" in technical.toPlainText()
    dialog.close()
    app.processEvents()


def test_failed_report_offers_expectation_details_and_debug_action():
    app = QApplication.instance() or QApplication([])
    result = {
        "name": "fixture",
        "status": "failed",
        "counts": {"passed": 0, "failed": 1, "error": 0, "unavailable": 0},
        "test_count": 1,
        "attempted_count": 1,
        "executed_count": 1,
        "not_executed_count": 0,
        "results": [
            {
                "name": "fixture_case",
                "status": "failed",
                "setup_stage": "complete",
                "execution_started": True,
                "error": "",
                "assertions": [
                    {
                        "step_id": "verify",
                        "at_ms": 20,
                        "address": "Y0",
                        "passed": False,
                        "detail": "actual=1, eq 0, tolerance=0.0",
                    }
                ],
                "invariant_violations": [],
            }
        ],
    }
    report = build_simulator_report(_workflow(result, run_id="sim_failed"))
    dialog = SimulatorReportDialog(report)
    buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
    tabs = dialog.findChild(QTabWidget, "SimulatorReportTabs")

    assert "检查测试期望" in buttons
    assert "进入故障调试" in buttons
    buttons["检查测试期望"].click()
    assert tabs.currentIndex() == 1
    technical = dialog.findChild(QTextBrowser, "SimulatorTechnicalDetails")
    technical_text = technical.toPlainText()
    assert "fixture_case" not in technical_text
    assert "sim_failed" not in technical_text
    assert "verify" not in technical_text
    buttons["进入故障调试"].click()
    assert dialog.requested_action == "debug"
    dialog.close()
    app.processEvents()
