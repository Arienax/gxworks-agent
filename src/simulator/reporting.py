"""User-facing summaries for persisted Simulator2 regression evidence."""

from __future__ import annotations

from shared.i18n import tr

import ast
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from shared.display_names import (
    naturalize_display_text,
    naturalize_identifier,
    preferred_display_name,
)


STATUS_LABELS = {
    "passed": tr('全部通过'),
    "failed": tr('发现逻辑问题'),
    "error": tr('执行出错'),
    "unavailable": tr('仿真环境不可用'),
    "prepare_failed": tr('仿真环境准备失败'),
    "import_failed": tr('程序导入失败'),
}

CASE_STATUS_LABELS = {
    "passed": tr('通过'),
    "failed": tr('失败'),
    "error": tr('错误'),
    "unavailable": tr('环境不可用'),
}

STAGE_LABELS = {
    "connect": tr('连接 GX Simulator2'),
    "cpu_reset": tr('复位仿真 CPU'),
    "initial_write": tr('写入测试初始值'),
    "initial_sample": tr('读取初始状态'),
    "steps": tr('执行测试步骤'),
    "complete": tr('测试完成'),
    "preflight": tr('检查仿真环境'),
    "mx_component": tr('检查 MX Component'),
    "project": tr('检查 GX Works2 工程'),
    "simulator": tr('启动 GX Simulator2'),
}

_ASSERTION_DETAIL = re.compile(
    r"^actual=(.*?),\s*(eq|ne|gt|ge|lt|le|between)\s+(.*?),\s*tolerance=(.*)$",
    re.IGNORECASE,
)

_OPERATOR_LABELS = {
    "eq": tr('等于'),
    "ne": tr('不等于'),
    "gt": tr('大于'),
    "ge": tr('大于等于'),
    "lt": tr('小于'),
    "le": tr('小于等于'),
    "between": tr('位于范围'),
}


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _items(value: Any) -> List[Mapping[str, Any]]:
    return [item for item in (value or []) if isinstance(item, Mapping)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _assertion_parts(assertion: Mapping[str, Any]) -> Dict[str, str]:
    detail = _text(assertion.get("detail"))
    matched = _ASSERTION_DETAIL.match(detail)
    if not matched:
        return {"actual": "", "operator": "", "expected": "", "tolerance": ""}
    actual, operator, expected, tolerance = matched.groups()
    return {
        "actual": actual,
        "operator": operator.casefold(),
        "expected": expected,
        "tolerance": tolerance,
    }


def _assertion_issue(assertion: Mapping[str, Any]) -> str:
    address = _text(assertion.get("address")) or tr('未知软元件')
    at_ms = int(assertion.get("at_ms") or 0)
    step_id = naturalize_identifier(
        assertion.get("step_id"),
        kind=tr('步骤'),
    )
    detail = _text(assertion.get("detail"))
    parts = _assertion_parts(assertion)
    if parts["operator"]:
        comparison = _OPERATOR_LABELS.get(parts["operator"], parts["operator"])
        message = (
            tr('{v0} 实际为 {v1}，期望{v2} {v3}', v0=address, v1=parts['actual'], v2=comparison, v3=parts['expected'])
        )
        tolerance = parts["tolerance"]
        if tolerance not in {"0", "0.0", "0.00"}:
            message += tr('（容差 {v0}）', v0=tolerance)
    else:
        message = tr('{v0} 未达到期望', v0=address)
        if detail:
            message += f"：{detail}"
    wait_text = tr('，等待条件超时') if assertion.get("wait_for") else ""
    return f"{at_ms} ms · {step_id} · {message}{wait_text}"


def _literal(value: str) -> Any:
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value


def _device_labels(program: Mapping[str, Any]) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for address, value in _mapping(program.get("device_comments")).items():
        label = _text(value)
        if label:
            labels[_text(address).upper()] = label
    for address, metadata in _mapping(program.get("devices")).items():
        metadata = _mapping(metadata)
        label = _text(
            metadata.get("comment")
            or metadata.get("label")
            or metadata.get("description")
        )
        if label:
            labels[_text(address).upper()] = label
    return labels


def _device_name(address: Any, labels: Mapping[str, str]) -> str:
    normalized = _text(address).upper()
    label = _text(labels.get(normalized))
    return f"{label}（{normalized}）" if label else normalized or tr('未知软元件')


def _friendly_description(value: Any, labels: Mapping[str, str]) -> str:
    text = _text(value)
    if not text or not labels:
        return naturalize_display_text(text)

    def replace(match: re.Match) -> str:
        address = match.group(1).upper()
        return _device_name(address, labels) if labels.get(address) else address

    return naturalize_display_text(
        re.sub(
            r"(?<![A-Z0-9])([XYMDTCS]\d+)(?!\d)",
            replace,
            text,
            flags=re.IGNORECASE,
        )
    )


def _is_bit_device(address: str) -> bool:
    return bool(re.fullmatch(r"[XYMS]\d+", _text(address).upper()))


def _bit_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    return bool(numeric) if numeric in {0, 1} else None


def _friendly_assertion_issue(
    assertion: Mapping[str, Any], labels: Mapping[str, str]
) -> str:
    address = _text(assertion.get("address")).upper()
    device = _device_name(address, labels)
    parts = _assertion_parts(assertion)
    if parts["operator"] == "eq" and _is_bit_device(address):
        actual_on = _bit_value(_literal(parts["actual"]))
        expected_on = _bit_value(_literal(parts["expected"]))
        if actual_on is not None and expected_on is not None:
            actual_text = tr('接通') if actual_on else tr('断开')
            expected_text = tr('接通') if expected_on else tr('断开')
            still = tr('仍') if actual_on != expected_on else ""
            message = (
                tr('{v0}实际{v1}处于{v2}状态，预期应{v3}', v0=device, v1=still, v2=actual_text, v3=expected_text)
            )
        else:
            message = tr('{v0}未达到预期状态', v0=device)
    elif parts["operator"]:
        comparison = _OPERATOR_LABELS.get(parts["operator"], parts["operator"])
        message = (
            tr('{v0}实际值为 {v1}，预期{v2} {v3}', v0=device, v1=parts['actual'], v2=comparison, v3=parts['expected'])
        )
    else:
        message = tr('{v0}未达到预期状态', v0=device)
    if assertion.get("wait_for"):
        message += tr('，等待条件已超时')
    return message


def _friendly_set_action(
    address: str, value: Any, labels: Mapping[str, str]
) -> str:
    device = _device_name(address, labels)
    enabled = _bit_value(value)
    label = _text(labels.get(_text(address).upper()))
    if enabled is not None and "按钮" in label:
        return f"{tr('按下') if enabled else tr('松开')}{device}"
    if enabled is not None and "故障" in label:
        return f"{tr('触发') if enabled else tr('解除')}{device}"
    if enabled is not None and _is_bit_device(address):
        return tr('将{v0}{v1}', v0=device, v1=tr('接通') if enabled else tr('断开'))
    return tr('将{v0}设为 {v1}', v0=device, v1=value)


def _friendly_observation(
    assertion: Mapping[str, Any], labels: Mapping[str, str]
) -> str:
    address = _text(assertion.get("address")).upper()
    device = _device_name(address, labels)
    parts = _assertion_parts(assertion)
    actual = _literal(parts["actual"])
    enabled = _bit_value(actual) if _is_bit_device(address) else None
    if enabled is not None:
        return f"{device}{tr('已接通') if enabled else tr('已断开')}"
    if parts["actual"]:
        return tr('{v0}为 {v1}', v0=device, v1=parts['actual'])
    return tr('{v0}状态正常', v0=device)


def _failed_expectation(
    assertion: Mapping[str, Any], labels: Mapping[str, str]
) -> Dict[str, Any]:
    address = _text(assertion.get("address")).upper()
    parts = _assertion_parts(assertion)
    actual = _literal(parts["actual"])
    expected = _literal(parts["expected"])
    actual_on = _bit_value(actual) if _is_bit_device(address) else None
    expected_on = _bit_value(expected) if _is_bit_device(address) else None
    if parts["operator"] == "eq" and actual_on is not None and expected_on is not None:
        actual_text = tr('接通') if actual_on else tr('断开')
        expected_text = tr('接通') if expected_on else tr('断开')
    else:
        actual_text = parts["actual"] or tr('未知')
        comparison = _OPERATOR_LABELS.get(parts["operator"], parts["operator"])
        expected_text = (
            f"{comparison} {parts['expected']}"
            if comparison and parts["expected"]
            else (parts["expected"] or tr('未知'))
        )
    return {
        "at_ms": int(assertion.get("at_ms") or 0),
        "step_id": _text(assertion.get("step_id")),
        "step_display_name": naturalize_identifier(
            assertion.get("step_id"),
            kind=tr('步骤'),
        ),
        "address": address,
        "device_name": _device_name(address, labels),
        "actual": parts["actual"],
        "expected": parts["expected"],
        "actual_text": actual_text,
        "expected_text": expected_text,
    }


def _invariant_issue(violation: Mapping[str, Any]) -> str:
    at_ms = int(violation.get("at_ms") or 0)
    raw_name = _text(violation.get("name") or violation.get("type"))
    name = naturalize_identifier(raw_name, kind=tr('运行约束')) if raw_name else tr('运行约束')
    message = naturalize_display_text(violation.get("message")) or tr('运行状态违反了声明的约束')
    return f"{at_ms} ms · {name} · {message}"


def _case_timeline(
    case: Mapping[str, Any],
    specification: Mapping[str, Any],
    labels: Mapping[str, str],
) -> List[Dict[str, Any]]:
    assertions_by_step: Dict[str, List[Mapping[str, Any]]] = {}
    failed_assertions = []
    for assertion in _items(case.get("assertions")):
        step_id = _text(assertion.get("step_id"))
        assertions_by_step.setdefault(step_id, []).append(assertion)
        if assertion.get("passed") is False:
            failed_assertions.append(assertion)
    if failed_assertions:
        cutoff_ms = min(int(item.get("at_ms") or 0) for item in failed_assertions)
    else:
        cutoff_ms = int(case.get("duration_ms") or 0)
    steps = [
        step
        for step in _items(specification.get("steps"))
        if int(step.get("at_ms") or 0) <= cutoff_ms
    ][-6:]
    timeline: List[Dict[str, Any]] = []
    for step in steps:
        step_id = _text(step.get("id"))
        at_ms = int(step.get("at_ms") or 0)
        actions = [
            _friendly_set_action(_text(address).upper(), value, labels)
            for address, value in _mapping(step.get("set")).items()
        ]
        step_assertions = assertions_by_step.get(step_id, [])
        failed = [item for item in step_assertions if item.get("passed") is False]
        if failed:
            actions.append(
                tr('状态检查失败：')
                + "；".join(
                    _friendly_assertion_issue(item, labels) for item in failed
                )
            )
            status = "failed"
        elif step_assertions:
            observations = [
                _friendly_observation(item, labels) for item in step_assertions[:4]
            ]
            remainder = len(step_assertions) - len(observations)
            summary = "、".join(observations)
            if remainder > 0:
                summary += tr('等 {v0} 项', v0=len(step_assertions))
            actions.append(tr('状态检查通过：') + summary)
            status = "passed"
        else:
            status = "action"
        if not actions:
            continue
        timeline.append(
            {
                "at_ms": at_ms,
                "step_id": step_id,
                "step_display_name": naturalize_identifier(
                    step_id,
                    kind=tr('步骤'),
                ),
                "text": "；".join(actions),
                "status": status,
            }
        )
    return timeline


def _case_report(
    case: Mapping[str, Any],
    specification: Mapping[str, Any],
    labels: Mapping[str, str],
    index: int,
) -> Dict[str, Any]:
    status = _text(case.get("status")) or "error"
    stage = _text(case.get("setup_stage"))
    issues: List[str] = []
    friendly_issues: List[str] = []
    failed_expectations: List[Dict[str, Any]] = []
    exact_error = _text(case.get("error"))
    if exact_error:
        issues.append(exact_error)
        friendly_issues.append(naturalize_display_text(exact_error))
    for assertion in _items(case.get("assertions")):
        if assertion.get("passed") is False:
            issues.append(_assertion_issue(assertion))
            friendly_issues.append(_friendly_assertion_issue(assertion, labels))
            failed_expectations.append(_failed_expectation(assertion, labels))
    for violation in _items(case.get("invariant_violations")):
        issue = _invariant_issue(violation)
        issues.append(issue)
        friendly_issues.append(issue)
    if status not in {"passed", "failed"} and not issues:
        for event in reversed(_items(case.get("trace"))):
            if _text(event.get("event")) == "runner_error" and _text(event.get("error")):
                issues.append(_text(event.get("error")))
                friendly_issues.append(
                    naturalize_display_text(event.get("error"))
                )
                break
    if status != "passed" and not issues:
        missing = tr('测试未完成，但执行器没有返回更具体的错误信息。')
        issues.append(missing)
        friendly_issues.append(missing)
    technical_name = _text(case.get("name")) or tr('未命名测试')
    display_name = (
        _friendly_description(specification.get("description"), labels)
        or _friendly_description(case.get("description"), labels)
        or preferred_display_name(
            case,
            kind=tr('测试项目'),
            index=index,
            descriptive_keys=("display_name",),
        )
    )
    return {
        "name": technical_name,
        "display_name": display_name,
        "status": status,
        "status_label": CASE_STATUS_LABELS.get(
            status,
            naturalize_identifier(status, kind=tr('未知状态')),
        ),
        "stage": stage,
        "stage_label": STAGE_LABELS.get(
            stage,
            naturalize_identifier(stage, kind=tr('未知阶段')),
        ),
        "duration_ms": int(case.get("duration_ms") or 0),
        "execution_started": bool(case.get("execution_started")),
        "environment_failure": bool(case.get("environment_failure")),
        "issues": issues,
        "friendly_issues": friendly_issues,
        "summary": friendly_issues[0] if friendly_issues else tr('测试符合预期。'),
        "timeline": _case_timeline(case, specification, labels),
        "failed_expectations": failed_expectations,
    }


def _workflow_errors(workflow: Mapping[str, Any], result: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    for value in (result.get("error"), workflow.get("message")):
        message = _text(value)
        if message and message not in errors:
            errors.append(message)
    execution = _mapping(workflow.get("execution"))
    preparation = _mapping(execution.get("preparation"))
    for payload in (
        preparation,
        _mapping(workflow.get("stop")),
        _mapping(workflow.get("import")),
    ):
        message = _text(payload.get("message") or payload.get("error"))
        if message and message not in errors:
            errors.append(message)
    return errors


def _recommendations(status: str, primary_reason: str) -> List[str]:
    lowered = primary_reason.casefold()
    if status == "failed":
        return [
            tr('这是程序行为或测试期望不一致，不是仿真连接故障。'),
            tr('核对失败步骤对应的输入、输出和时序；确认期望无误后，可使用“故障调试”生成局部修改方案。'),
        ]
    if "not_found" in lowered or "未找到" in primary_reason or "版本过旧" in primary_reason:
        return [tr('关闭旧版软件后使用当前修复版重新测试；程序会自动隔离旧版网关。')]
    if "timed out" in lowered or "timeout" in lowered or "超时" in primary_reason:
        return [
            tr('确认 GX Works2 已打开目标工程，GX Simulator2 能进入 RUN。'),
            tr('若模拟器正在切换状态，请等待其稳定后重新测试。'),
        ]
    if "mx component" in lowered:
        return [tr('确认已安装与网关位数匹配的 MX Component，并重新启动软件。')]
    if status == "import_failed":
        return [tr('先在 GX Works2 中确认目标工程已打开，再检查程序和注释 CSV 的导入提示。')]
    if status in {"error", "unavailable", "prepare_failed"}:
        return [tr('根据上方失败阶段和原始错误修复仿真环境后，再运行同一测试方案。')]
    return []


def build_simulator_report(
    workflow: Mapping[str, Any],
    *,
    evidence_path: Optional[Any] = None,
    suite: Optional[Mapping[str, Any]] = None,
    program: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a complete, non-technical report from one workflow response."""

    workflow = _mapping(workflow)
    execution = _mapping(workflow.get("execution"))
    result = _mapping(execution.get("result"))
    record = _mapping(execution.get("record"))
    status = _text(workflow.get("status") or result.get("status")) or "error"
    result_status = _text(result.get("status"))
    if result_status in {"passed", "failed", "error", "unavailable"}:
        status = result_status
    suite = _mapping(suite)
    program = _mapping(program)
    labels = _device_labels(program)
    specifications = {
        _text(item.get("name")): item
        for item in _items(suite.get("tests"))
        if _text(item.get("name"))
    }
    cases = [
        _case_report(
            item,
            specifications.get(_text(item.get("name")), {}),
            labels,
            index,
        )
        for index, item in enumerate(_items(result.get("results")), start=1)
    ]
    workflow_errors = _workflow_errors(workflow, result)
    display_workflow_errors = [
        naturalize_display_text(item) for item in workflow_errors
    ]
    issue_candidates = [
        issue
        for case in cases
        if case["status"] != "passed"
        for issue in case["friendly_issues"]
    ]
    primary_reason = (
        issue_candidates[0]
        if issue_candidates
        else (
            display_workflow_errors[0]
            if display_workflow_errors
            else tr('仿真测试已完成。')
        )
    )
    counts = _mapping(result.get("counts"))
    test_count = int(result.get("test_count") or len(cases))
    executed_count = int(result.get("executed_count") or 0)
    attempted_count = int(result.get("attempted_count") or len(cases))
    not_executed_count = int(
        result.get("not_executed_count")
        if result.get("not_executed_count") is not None
        else max(0, test_count - executed_count)
    )
    path = Path(evidence_path).resolve() if evidence_path else None
    raw_suite_name = _text(result.get("name") or record.get("suite_name"))
    suite_display_name = _text(result.get("display_name")) or naturalize_identifier(
        raw_suite_name,
        kind=tr('测试方案'),
    )
    return {
        "status": status,
        "status_label": STATUS_LABELS.get(
            status,
            naturalize_identifier(status, kind=tr('未知状态')),
        ),
        "title": tr('仿真结果报告'),
        "suite_name": raw_suite_name,
        "suite_display_name": suite_display_name,
        "message": _text(workflow.get("message")),
        "primary_reason": primary_reason,
        "counts": {
            "passed": int(counts.get("passed") or 0),
            "failed": int(counts.get("failed") or 0),
            "error": int(counts.get("error") or 0),
            "unavailable": int(counts.get("unavailable") or 0),
        },
        "test_count": test_count,
        "attempted_count": attempted_count,
        "executed_count": executed_count,
        "not_executed_count": not_executed_count,
        "cases": cases,
        "passed_cases": [case for case in cases if case["status"] == "passed"],
        "problem_cases": [case for case in cases if case["status"] != "passed"],
        "workflow_errors": workflow_errors,
        "display_workflow_errors": display_workflow_errors,
        "recommendations": _recommendations(status, primary_reason),
        "run_id": _text(record.get("run_id")),
        "evidence_path": str(path) if path else "",
    }


def render_simulator_report_text(report: Mapping[str, Any]) -> str:
    """Render a copyable plain-text report from ``build_simulator_report``."""

    report = _mapping(report)
    counts = _mapping(report.get("counts"))
    lines = [
        tr('仿真结果报告'),
        tr('状态：{v0}', v0=_text(report.get('status_label'))),
    ]
    suite_display_name = _text(report.get("suite_display_name")) or naturalize_identifier(
        report.get("suite_name"), kind=tr('测试方案')
    )
    if suite_display_name:
        lines.append(
            tr('测试方案：') + suite_display_name
        )
    lines.extend(
        [
            tr('结论：{v0}', v0=_text(report.get('primary_reason'))),
            (
                tr('统计：通过 {v0}，失败 {v1}，错误 {v2}，未执行 {v3}', v0=int(counts.get('passed') or 0), v1=int(counts.get('failed') or 0), v2=int(counts.get('error') or 0), v3=int(report.get('not_executed_count') or 0))
            ),
        ]
    )
    cases = _items(report.get("cases"))
    if not cases:
        lines.extend(["", tr('问题定位：')])
        lines.append(tr('- 没有进入单项测试执行。'))
    problem_cases = [case for case in cases if case.get("status") != "passed"]
    passed_cases = [case for case in cases if case.get("status") == "passed"]
    if problem_cases:
        lines.extend(["", tr('问题定位：')])
    for case_index, case in enumerate(problem_cases, start=1):
        stage = _text(case.get("stage_label"))
        stage_text = (
            tr('，未完成阶段：{v0}', v0=stage)
            if stage and case.get("stage") != "complete"
            else ""
        )
        lines.append(
            f"- [{_text(case.get('status_label'))}] "
            f"{preferred_display_name(case, kind=tr('测试项目'), index=case_index, descriptive_keys=('display_name', 'description', 'title', 'label'))}"
            f"{stage_text}"
        )
        for issue in case.get("friendly_issues") or case.get("issues") or []:
            lines.append(f"  - {_text(issue)}")
        timeline = _items(case.get("timeline"))
        if timeline:
            lines.append(tr('  操作过程：'))
            for item in timeline:
                lines.append(
                    f"  - {int(item.get('at_ms') or 0)} ms：{_text(item.get('text'))}"
                )
    if passed_cases:
        lines.extend(["", tr('其余 {v0} 项测试通过。', v0=len(passed_cases))])
    recommendations = [_text(item) for item in report.get("recommendations") or [] if _text(item)]
    if recommendations:
        lines.extend(["", tr('建议处理：')])
        lines.extend(f"- {item}" for item in recommendations)
    if _text(report.get("evidence_path")):
        lines.extend(["", tr('运行证据：已保存，可在结果窗口中打开。')])
    if cases:
        lines.extend(["", tr('执行详情：')])
        for case_index, case in enumerate(cases, start=1):
            lines.append(
                f"- [{_text(case.get('status_label'))}] "
                f"{preferred_display_name(case, kind=tr('测试项目'), index=case_index, descriptive_keys=('display_name', 'description', 'title', 'label'))}"
            )
            for issue in case.get("friendly_issues") or []:
                lines.append(f"  - {_text(issue)}")
    return "\n".join(lines).strip() + "\n"


__all__ = ["build_simulator_report", "render_simulator_report_text"]
