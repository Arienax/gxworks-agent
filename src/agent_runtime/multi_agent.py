"""Version-bound, deterministic orchestration for PLC specialist agents.

The specialists in this module are deliberately *advisory*.  They receive an
immutable, bounded snapshot and return JSON candidates.  They cannot call one
another, use GUI/device primitives, import a program, run a simulator, or
approve their own changes.  Deterministic validators remain the authority for
PLC IR, inspection findings, patches, imports and regression tests.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
import uuid
from shared.i18n import language_scoped

from inspection.models import hash_ladder_json, normalize_inspection_report
from plc.ir import canonical_sha256, ir_to_ladder, validate_plc_ir


MULTI_AGENT_SCHEMA_VERSION = 1
MAX_SPECIALIST_STAGES = 4
MAX_CONTEXT_BYTES = 180_000

REVIEWER = "reviewer"
TIMING_PLANNER = "timing_planner"
DEBUG_AGENT = "debug_agent"
PATCH_AGENT = "patch_agent"

_ALLOWED_ROLES = frozenset(
    {REVIEWER, TIMING_PLANNER, DEBUG_AGENT, PATCH_AGENT}
)
_ROUTES = {
    "program_review": (REVIEWER, TIMING_PLANNER),
    "fault_debug": (DEBUG_AGENT, PATCH_AGENT),
}


class MultiAgentError(RuntimeError):
    """A specialist handoff or output crossed its deterministic boundary."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _bounded_copy(value: Any, label: str) -> Any:
    copied = copy.deepcopy(value)
    size = len(_canonical_bytes(copied))
    if size > MAX_CONTEXT_BYTES:
        raise MultiAgentError(
            f"{label} 上下文为 {size} 字节，超过受控多代理上限 "
            f"{MAX_CONTEXT_BYTES} 字节。"
        )
    return copied


def build_version_binding(
    program: Mapping[str, Any], *, project_id: str, version_id: str
) -> Dict[str, Any]:
    """Return the immutable identity every specialist handoff must preserve."""

    validate_plc_ir(program, validate_ladder=False)
    project = str(project_id or "").strip()
    version = str(version_id or "").strip()
    if not project or not version:
        raise MultiAgentError("多代理任务必须绑定项目和程序版本。")
    return {
        "project_id": project,
        "version_id": version,
        "revision": int(program.get("revision") or 0),
        "ir_sha256": canonical_sha256(program),
    }


def _validate_binding(claim: Any, expected: Mapping[str, Any], role: str) -> None:
    if not isinstance(claim, Mapping):
        raise MultiAgentError(f"{role} 未返回版本绑定。")
    mismatches = [key for key, value in expected.items() if claim.get(key) != value]
    if mismatches:
        raise MultiAgentError(
            f"{role} 返回了其他程序版本：" + ", ".join(mismatches)
        )


def _network_context(program: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for item in (program.get("networks", []) or [])[:180]:
        if not isinstance(item, Mapping):
            continue
        instructions = list(item.get("instructions") or [])
        rows.append(
            {
                "id": str(item.get("id") or ""),
                "order": int(item.get("order") or 0),
                "comment": str(item.get("comment") or "")[:500],
                "instruction_count": len(instructions),
                "instructions": copy.deepcopy(instructions[:120]),
                "instructions_truncated": len(instructions) > 120,
                "reads": list(item.get("reads") or [])[:120],
                "writes": list(item.get("writes") or [])[:120],
            }
        )
    return rows


def build_review_context(
    program: Mapping[str, Any],
    *,
    project_id: str,
    version_id: str,
    request: Any,
    local_report: Mapping[str, Any],
    confirmed_spec: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one read-only context shared by Reviewer and Timing Planner."""

    binding = build_version_binding(
        program, project_id=project_id, version_id=version_id
    )
    analysis = program.get("analysis") or {}
    networks = list(program.get("networks") or [])
    devices = sorted(str(item) for item in (program.get("devices") or {}))
    local_findings = list(local_report.get("findings") or [])
    static_findings = list(analysis.get("findings") or [])
    context = {
        "schema_version": MULTI_AGENT_SCHEMA_VERSION,
        "binding": binding,
        "plc": copy.deepcopy(program.get("plc") or {}),
        "program_name": str(program.get("program_name") or "MAIN"),
        "request": copy.deepcopy(request),
        "confirmed_spec": copy.deepcopy(dict(confirmed_spec or {})),
        "networks": _network_context(program),
        "network_scope": {
            "total": len(networks),
            "included": min(len(networks), 180),
            "truncated": len(networks) > 180,
        },
        "devices": devices[:600],
        "device_scope": {
            "total": len(devices),
            "included": min(len(devices), 600),
            "truncated": len(devices) > 600,
        },
        "logic": copy.deepcopy(program.get("logic") or {}),
        "timing": copy.deepcopy(program.get("timing") or {}),
        "deterministic_analysis": {
            "counts": copy.deepcopy(analysis.get("counts") or {}),
            "findings": copy.deepcopy(static_findings[:160]),
            "findings_truncated": len(static_findings) > 160,
            "rules_checked": list(analysis.get("rules_checked") or []),
        },
        "local_report": {
            "report_type": str(local_report.get("report_type") or "program_review"),
            "summary": str(local_report.get("summary") or "")[:3000],
            "counts": copy.deepcopy(local_report.get("counts") or {}),
            "findings": copy.deepcopy(local_findings[:160]),
            "findings_truncated": len(local_findings) > 160,
        },
        "authority": {
            "advisory_only": True,
            "may_execute_tools": False,
            "may_modify_program": False,
            "may_import": False,
            "may_run_simulator": False,
            "deterministic_validator_is_authoritative": True,
        },
    }
    return _bounded_copy(context, "评审")


def _reviewer_candidate(
    candidate: Any,
    *,
    role: str,
    context: Mapping[str, Any],
    ladder: Mapping[str, Any],
    report_type: str,
    request: Any,
    base_version_id: str,
    plc_model: str,
) -> Dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise MultiAgentError(f"{role} 必须返回 JSON 对象。")
    _validate_binding(candidate.get("binding"), context["binding"], role)
    payload = copy.deepcopy(dict(candidate))
    payload.pop("binding", None)
    raw_findings = payload.get("findings")
    if raw_findings is None:
        raw_findings = []
    if not isinstance(raw_findings, list):
        raise MultiAgentError(f"{role}.findings 必须是数组。")
    valid_rungs = {
        int(item.get("rung_id"))
        for item in ladder.get("rungs", []) or []
        if isinstance(item, Mapping) and isinstance(item.get("rung_id"), int)
    }
    valid_devices = {
        str(item).upper() for item in context.get("devices", []) or []
    }
    normalized_findings = []
    for index, raw in enumerate(raw_findings[:120]):
        if not isinstance(raw, Mapping):
            raise MultiAgentError(f"{role}.findings[{index}] 必须是对象。")
        item = copy.deepcopy(dict(raw))
        evidence = item.get("evidence")
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list):
            evidence = [evidence]
        cited_rungs = set()
        cited_devices = set()
        cited_paths = set()
        for entry in evidence:
            if not isinstance(entry, Mapping):
                continue
            values = entry.get("rung_ids", entry.get("rung_id"))
            if not isinstance(values, (list, tuple, set)):
                values = [values]
            for value in values:
                if isinstance(value, bool):
                    continue
                try:
                    cited_rungs.add(int(value))
                except (TypeError, ValueError):
                    continue
            values = entry.get("addresses", entry.get("address"))
            if not isinstance(values, (list, tuple, set)):
                values = [values]
            cited_devices.update(
                str(value).strip().upper() for value in values if str(value or "").strip()
            )
            values = entry.get("json_paths", entry.get("json_path"))
            if not isinstance(values, (list, tuple, set)):
                values = [values]
            cited_paths.update(
                str(value).strip() for value in values if str(value or "").strip()
            )
        if not evidence:
            cited_rungs.update(
                int(value)
                for value in item.get("rung_ids", []) or []
                if isinstance(value, int) and not isinstance(value, bool)
            )
            cited_devices.update(
                str(value).strip().upper()
                for value in item.get("addresses", []) or []
                if str(value or "").strip()
            )
            cited_paths.update(
                str(value).strip()
                for value in item.get("json_paths", []) or []
                if str(value or "").strip()
            )
        invalid_rungs = cited_rungs.difference(valid_rungs)
        invalid_devices = cited_devices.difference(valid_devices)
        if invalid_rungs or invalid_devices:
            detail = []
            if invalid_rungs:
                detail.append("rung " + ", ".join(map(str, sorted(invalid_rungs))))
            if invalid_devices:
                detail.append("device " + ", ".join(sorted(invalid_devices)))
            raise MultiAgentError(
                f"{role} 引用了当前程序之外的证据：" + "; ".join(detail)
            )
        invalid_paths = [
            value
            for value in cited_paths
            if value != "$"
            and not (
                value.startswith("$.rungs[")
                and any(
                    value == f"$.rungs[{position}]"
                    or value.startswith(f"$.rungs[{position}].")
                    for position in range(len(ladder.get("rungs", []) or []))
                )
            )
        ]
        if invalid_paths:
            raise MultiAgentError(
                f"{role} 引用了当前程序之外的 JSON 路径：{invalid_paths[0]}"
            )
        normalized_findings.append(item)
    payload["findings"] = normalized_findings
    report = normalize_inspection_report(
        payload,
        base_json=ladder,
        defaults={
            "report_type": report_type,
            "request": request,
            "base_version_id": base_version_id,
            "plc_model": plc_model,
            "trigger": "manual",
            "depth": "deep",
            "origin": "ai",
            "status": "complete",
        },
    )
    # A normalized fix instruction remains advice: the specialist has no
    # execution primitive.  Existing UI confirmation and the normal generation
    # boundary remain responsible for any later program change.
    return report


def _stage_record(
    role: str,
    *,
    sequence: int,
    input_payload: Mapping[str, Any],
    output_payload: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "sequence": sequence,
        "role": role,
        "status": "accepted",
        "input_sha256": _sha(input_payload),
        "output_sha256": _sha(output_payload),
    }


class DeterministicMultiAgentSupervisor:
    """Execute only fixed, finite specialist DAGs with no recursive delegation."""

    def __init__(self, runner: Callable[[str, Mapping[str, Any]], Mapping[str, Any]]):
        if not callable(runner):
            raise TypeError("runner must be callable")
        self.runner = runner

    def _call(
        self,
        role: str,
        payload: Mapping[str, Any],
        *,
        sequence: int,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if role not in _ALLOWED_ROLES:
            raise MultiAgentError(f"未授权的代理角色：{role}")
        if sequence < 1 or sequence > MAX_SPECIALIST_STAGES:
            raise MultiAgentError("多代理阶段超过固定上限。")
        frozen = _bounded_copy(payload, f"{role} 输入")
        result = self.runner(role, frozen)
        if not isinstance(result, Mapping):
            raise MultiAgentError(f"{role} 未返回 JSON 对象。")
        accepted = _bounded_copy(dict(result), f"{role} 输出")
        return accepted, _stage_record(
            role,
            sequence=sequence,
            input_payload=frozen,
            output_payload=accepted,
        )

    @language_scoped
    def review_program(
        self,
        program: Mapping[str, Any],
        *,
        project_id: str,
        version_id: str,
        request: Any,
        local_report: Mapping[str, Any],
        confirmed_spec: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run Reviewer then Timing Planner and return normalized advice."""

        context = build_review_context(
            program,
            project_id=project_id,
            version_id=version_id,
            request=request,
            local_report=local_report,
            confirmed_spec=confirmed_spec,
        )
        ladder = ir_to_ladder(program)
        report_type = str(local_report.get("report_type") or "program_review")
        plc_model = str((program.get("plc") or {}).get("cpu") or "FX3U").upper()
        stages: List[Dict[str, Any]] = []
        reports: List[Dict[str, Any]] = []
        prior_summary = ""
        for sequence, role in enumerate(_ROUTES["program_review"], start=1):
            role_payload = {
                "schema_version": MULTI_AGENT_SCHEMA_VERSION,
                "role": role,
                "context": context,
                "upstream": (
                    {"role": REVIEWER, "summary": prior_summary}
                    if role == TIMING_PLANNER
                    else None
                ),
            }
            raw, stage = self._call(role, role_payload, sequence=sequence)
            normalized = _reviewer_candidate(
                raw,
                role=role,
                context=context,
                ladder=ladder,
                report_type=report_type,
                request=request,
                base_version_id=version_id,
                plc_model=plc_model,
            )
            stage["normalized_output_sha256"] = _sha(normalized)
            stages.append(stage)
            reports.append(normalized)
            prior_summary = str(normalized.get("summary") or "")[:1000]

        run = {
            "schema_version": MULTI_AGENT_SCHEMA_VERSION,
            "run_id": "agents_" + uuid.uuid4().hex[:16],
            "workflow": "program_review",
            "binding": copy.deepcopy(context["binding"]),
            "route": list(_ROUTES["program_review"]),
            "status": "accepted",
            "created_at": _utc_now(),
            "context_sha256": _sha(context),
            "stages": stages,
            "authority": copy.deepcopy(context["authority"]),
        }
        return {"reports": reports, "audit": run}

    @language_scoped
    def prepare_debug_plan(
        self,
        *,
        evidence: Mapping[str, Any],
        plan_builder: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    ) -> Dict[str, Any]:
        """Run evidence diagnosis → local patch, then invoke deterministic builder."""

        if not isinstance(evidence, Mapping) or not isinstance(
            evidence.get("binding"), Mapping
        ):
            raise MultiAgentError("调试多代理需要版本绑定的失败证据。")
        if not callable(plan_builder):
            raise TypeError("plan_builder must be callable")
        frozen_evidence = _bounded_copy(dict(evidence), "调试证据")
        stages: List[Dict[str, Any]] = []

        diagnosis_payload = {
            "schema_version": MULTI_AGENT_SCHEMA_VERSION,
            "role": DEBUG_AGENT,
            "binding": copy.deepcopy(frozen_evidence["binding"]),
            "evidence": frozen_evidence,
        }
        diagnosis, stage = self._call(DEBUG_AGENT, diagnosis_payload, sequence=1)
        from application.debug_loop import normalize_debug_diagnosis

        diagnosis = normalize_debug_diagnosis(diagnosis, frozen_evidence)
        stage["normalized_output_sha256"] = _sha(diagnosis)
        stages.append(stage)

        patch_payload = {
            "schema_version": MULTI_AGENT_SCHEMA_VERSION,
            "role": PATCH_AGENT,
            "binding": copy.deepcopy(frozen_evidence["binding"]),
            "evidence": frozen_evidence,
            "diagnosis": diagnosis,
        }
        patch, stage = self._call(PATCH_AGENT, patch_payload, sequence=2)
        stages.append(stage)

        # The deterministic plan builder validates diagnosis citations, patch
        # scope, PLC IR, static errors and exact version identity.
        plan = dict(plan_builder(diagnosis, patch))
        run = {
            "schema_version": MULTI_AGENT_SCHEMA_VERSION,
            "run_id": "agents_" + uuid.uuid4().hex[:16],
            "workflow": "fault_debug",
            "binding": copy.deepcopy(frozen_evidence["binding"]),
            "route": list(_ROUTES["fault_debug"]),
            "status": "accepted",
            "created_at": _utc_now(),
            "context_sha256": _sha(frozen_evidence),
            "stages": stages,
            "authority": {
                "advisory_only": True,
                "may_execute_tools": False,
                "may_modify_program": False,
                "may_import": False,
                "may_run_simulator": False,
                "deterministic_plan_builder_is_authoritative": True,
            },
        }
        plan["multi_agent"] = run
        return plan


__all__ = [
    "DEBUG_AGENT",
    "MAX_SPECIALIST_STAGES",
    "MULTI_AGENT_SCHEMA_VERSION",
    "MultiAgentError",
    "PATCH_AGENT",
    "REVIEWER",
    "TIMING_PLANNER",
    "DeterministicMultiAgentSupervisor",
    "build_review_context",
    "build_version_binding",
]
