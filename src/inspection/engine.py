"""Unified local/deep inspection orchestration for ladder versions."""

import json
import re
from typing import Any, Callable, Dict, Mapping, Optional

from inspection.models import (
    hash_ladder_json,
    merge_inspection_reports,
    normalize_inspection_report,
    normalize_plc_model,
)
from plc.validation import PLCJsonValidationError, validate_ladder_full
from inspection.rules import findings_to_dicts, review_ladder


_DEVICE_RE = re.compile(
    r"(?<![A-Z0-9_])(?:SM|SD|X|Y|M|D|T|C|S)\d+(?![A-Z0-9_])",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"^(\$[^:]*):\s*(.*)$", re.DOTALL)
_RUNG_INDEX_RE = re.compile(r"\$\.rungs\[(\d+)\]")


def _report_type(value):
    normalized = str(value or "program_review").strip().lower().replace("-", "_")
    if normalized in {"debug", "fault_debug"}:
        return "fault_debug"
    return "program_review"


def _request_dict(request):
    if isinstance(request, Mapping):
        return dict(request)
    text = str(request or "").strip()
    return {"text": text} if text else {}


def _debug_symptom(request):
    if not isinstance(request, Mapping):
        return str(request or "").strip()
    for key in ("symptom", "fault_symptom", "故障现象", "text"):
        value = str(request.get(key) or "").strip()
        if value:
            return value
    return ""


def _path_context(error_text, ladder):
    match = _PATH_RE.match(str(error_text))
    path = match.group(1) if match else "$"
    message = match.group(2) if match else str(error_text)
    rung_ids = []
    rung_match = _RUNG_INDEX_RE.search(path)
    if rung_match and isinstance(ladder, dict):
        index = int(rung_match.group(1))
        rungs = ladder.get("rungs", [])
        if isinstance(rungs, list) and index < len(rungs):
            rung_id = rungs[index].get("rung_id") if isinstance(rungs[index], dict) else None
            if isinstance(rung_id, int):
                rung_ids.append(rung_id)
    addresses = [item.upper() for item in _DEVICE_RE.findall(message)]
    return path, message, rung_ids, addresses


def _hard_validation_finding(error, ladder):
    path, message, rung_ids, addresses = _path_context(error, ladder)
    return {
        "source": "local",
        "severity": "error",
        "category": "hard_validation",
        "title": "梯形图硬校验失败",
        "message": message,
        "evidence": [str(error)],
        "rung_ids": rung_ids,
        "json_paths": [path],
        "addresses": addresses,
        "suggestion": "先修正结构、地址或指令兼容错误，再进行版本修复。",
        "fixable": False,
        "confidence": "high",
    }


def _debug_request_finding():
    return {
        "source": "local",
        "severity": "error",
        "category": "debug_request",
        "title": "缺少故障现象",
        "message": "故障调试必须提供故障现象。",
        "evidence": ["调试请求中的故障现象为空"],
        "suggestion": "填写实际现象后重试；期望行为、发生条件和观测值可作为补充。",
        "fixable": False,
        "confidence": "high",
    }


def _observations(request):
    if not isinstance(request, Mapping):
        return []
    values = request.get("observations")
    if values is None:
        values = request.get("observed_values", request.get("观测地址/值/时刻"))
    if isinstance(values, Mapping):
        values = [values]
    elif not isinstance(values, list):
        values = [values] if values else []
    result = []
    for item in values:
        if isinstance(item, Mapping):
            address = str(item.get("address") or item.get("device") or "").strip().upper()
            observed = item.get("value", item.get("observed", ""))
            timestamp = item.get(
                "when", item.get("time", item.get("timestamp", ""))
            )
            result.append(
                {
                    "address": address,
                    "observed": "value=%s%s" % (
                        observed,
                        (", time=%s" % timestamp) if timestamp not in {None, ""} else "",
                    ),
                }
            )
        else:
            text = str(item or "").strip()
            match = _DEVICE_RE.search(text)
            if match:
                result.append({"address": match.group(0).upper(), "observed": text})
    return [item for item in result if item["address"]]


def _online_checks(request):
    checks = []
    for index, item in enumerate(_observations(request)):
        checks.append(
            {
                "check_id": "observed_%04d" % (index + 1),
                "address": item["address"],
                "instruction": "核对该手工观测值与报告绑定版本、PLC 型号和采样时刻一致。",
                "expected": "",
                "observed": item["observed"],
                "status": "confirmed",
            }
        )
    return checks


def _summary(findings, status="local_only"):
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        severity = finding.get("severity")
        if severity in counts:
            counts[severity] += 1
    prefix = "本地评审完成" if status == "local_only" else "评审完成"
    return "%s：错误 %d，警告 %d，提示 %d。" % (
        prefix,
        counts["error"],
        counts["warning"],
        counts["info"],
    )


_STATIC_TO_LEGACY_CATEGORY = {
    "MULTIPLE_WRITER": "output_ownership",
    "LATCH_WITHOUT_RESET": "set_reset_ownership",
    "TIMER_CANNOT_COMPLETE": "timer_path",
    "UNREACHABLE_STATE": "state_transition",
    "DEAD_END_STATE": "state_transition",
}


def _static_findings(ladder, confirmed_spec, plc_model):
    """Return P4/P5 findings, suppressing equivalent legacy report rows."""

    from plc.ir import build_plc_ir

    program = build_plc_ir(
        ladder,
        plc_model=plc_model,
        confirmed_spec=confirmed_spec,
    )
    return program, [
        dict(item, source="local")
        for item in (program.get("analysis") or {}).get("findings", [])
        if isinstance(item, Mapping)
    ]


def _finding_signature(finding):
    addresses = finding.get("addresses")
    if addresses is None:
        addresses = [finding.get("address")] if finding.get("address") else []
    return (
        str(finding.get("category") or ""),
        tuple(sorted(str(item).upper() for item in addresses or [] if item)),
    )


def run_local_inspection(
    ladder,
    report_type="program_review",
    request=None,
    confirmed_spec=None,
    plc_model="FX3U",
    base_version_id=None,
    trigger="manual",
    depth="basic",
):
    """Run hard validation and all local advisory rules.

    No AI or network call is made.  The report is always bound to the supplied
    ladder hash and selected version id.
    """

    model = normalize_plc_model(plc_model)
    normalized_type = _report_type(report_type)
    request_payload = _request_dict(request)
    defaults = {
        "report_type": normalized_type,
        "trigger": trigger,
        "depth": depth,
        "base_version_id": base_version_id,
        "plc_model": model,
        "status": "local_only",
        "request": request_payload,
    }

    if isinstance(ladder, dict) and "st_code" in ladder and "rungs" not in ladder:
        return normalize_inspection_report(
            {
                "status": "unsupported",
                "summary": "当前版本为 ST；首期仅支持梯形图评审与故障调试。",
                "findings": [],
                "request": request_payload,
            },
            base_json=ladder,
            defaults=defaults,
        )

    findings = []
    hard_validation_passed = True
    try:
        validate_ladder_full(
            ladder,
            plc_model=model,
            confirmed_spec=confirmed_spec,
        )
    except (PLCJsonValidationError, TypeError, ValueError) as error:
        hard_validation_passed = False
        findings.append(_hard_validation_finding(error, ladder))

    # Advisory rules assume a structurally valid ladder.  Continuing after a
    # hard failure turns one root problem into many secondary warnings and can
    # make a generated program look far worse than the evidence supports.
    if hard_validation_passed:
        legacy_findings = findings_to_dicts(
            review_ladder(
                ladder,
                confirmed_spec=confirmed_spec,
                plc_model=model,
                request=request_payload,
            )
        )
        _program, deterministic_findings = _static_findings(
            ladder, confirmed_spec, model
        )
        static_signatures = {
            (
                _STATIC_TO_LEGACY_CATEGORY.get(str(item.get("code") or "")),
                tuple(sorted(item.get("addresses") or [])),
            )
            for item in deterministic_findings
            if str(item.get("code") or "") in _STATIC_TO_LEGACY_CATEGORY
        }
        findings.extend(
            item
            for item in legacy_findings
            if _finding_signature(item) not in static_signatures
        )
        findings.extend(deterministic_findings)
    if normalized_type == "fault_debug" and not _debug_symptom(request):
        findings.append(_debug_request_finding())

    payload = {
        "status": "local_only",
        "summary": _summary(findings),
        "findings": findings,
        "online_checks": _online_checks(request_payload),
        "request": request_payload,
    }
    return normalize_inspection_report(payload, base_json=ladder, defaults=defaults)


def run_inspection(
    ladder,
    report_type="program_review",
    request=None,
    confirmed_spec=None,
    plc_model="FX3U",
    base_version_id=None,
    trigger="manual",
    depth="deep",
    ai_runner=None,
):
    """Run local rules first, then optionally merge a deep AI report.

    ``ai_runner`` is dependency-injected to keep this module independent from a
    particular API client.  If no runner is configured the result is explicitly
    ``local_only``.  Once an attempted deep call fails, the local evidence is
    retained and the result is marked ``partial``.
    """

    local = run_local_inspection(
        ladder,
        report_type=report_type,
        request=request,
        confirmed_spec=confirmed_spec,
        plc_model=plc_model,
        base_version_id=base_version_id,
        trigger=trigger,
        depth="basic",
    )
    if local["status"] == "unsupported" or depth != "deep" or ai_runner is None:
        return local

    try:
        ai_payload = ai_runner(
            ladder=ladder,
            local_report=local,
            report_type=local["report_type"],
            request=_request_dict(request),
            confirmed_spec=confirmed_spec,
            plc_model=local["plc_model"],
            base_version_id=base_version_id,
        )
        if not isinstance(ai_payload, Mapping):
            raise ValueError("AI inspection returned no report object")
        claimed_hash = str(ai_payload.get("base_json_hash") or "").strip()
        if claimed_hash and claimed_hash != local["base_json_hash"]:
            raise ValueError("AI inspection returned a report for a different JSON hash")
        claimed_version = str(ai_payload.get("base_version_id") or "").strip()
        if claimed_version and base_version_id is not None and claimed_version != str(base_version_id):
            raise ValueError("AI inspection returned a report for a different version")
        ai_report = normalize_inspection_report(
            dict(ai_payload, status=ai_payload.get("status", "complete"), depth="deep"),
            base_json=ladder,
            defaults={
                "report_type": local["report_type"],
                "trigger": trigger,
                "depth": "deep",
                "base_version_id": base_version_id,
                "plc_model": local["plc_model"],
                "request": _request_dict(request),
                "origin": "ai",
            },
        )
        return merge_inspection_reports(local, ai_report)
    except Exception as error:
        fallback = dict(local)
        fallback["status"] = "partial"
        fallback["depth"] = "deep"
        fallback["summary"] = local["summary"] + " AI 深查失败，已保留本地结果：%s" % error
        fallback["execution"] = {
            "status": "partial",
            "local": {"status": "complete", "error": ""},
            "ai": {"status": "failed", "error": str(error)},
        }
        return normalize_inspection_report(fallback)


__all__ = [
    "hash_ladder_json",
    "merge_inspection_reports",
    "normalize_inspection_report",
    "run_inspection",
    "run_local_inspection",
]
