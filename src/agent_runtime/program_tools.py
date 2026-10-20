"""Project inspection, deterministic validation and patch intents."""
from __future__ import annotations
import copy
import re
from typing import Any, Dict, Mapping
from agent_runtime.tool_errors import ToolPreconditionError, PROJECT_ACTIONS, SOURCE_ACTIONS
from agent_runtime.tool_context import (
    ToolContext,
    _require_program_ir,
    _confirmed_spec,
)
from agent_runtime.native_tools import (
    _read_sfc_project,
)


def _get_operation_status(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    if not context.project_id:
        raise ToolPreconditionError('NO_PROJECT_SELECTED', '请先明确选择需要查询执行回执的工程。',
                                    next_actions=PROJECT_ACTIONS)
    if sum(key in arguments for key in ('proposal_id', 'execution_job_id')) != 1:
        raise ToolPreconditionError('INVALID_ARGUMENTS', '请提供 proposal_id 或 execution_job_id 中的一项。')
    if not callable(context.operation_status):
        raise ToolPreconditionError('OPERATION_STATUS_UNAVAILABLE', '当前接入方式没有绑定应用执行回执查询。')
    return context.operation_status(context.project_id, **arguments)


def _get_native_engineering_session(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    if not context.project_id:
        raise ToolPreconditionError('NO_PROJECT_SELECTED', 'Select the intended engineering project first.',
                                    next_actions=PROJECT_ACTIONS)
    if not callable(context.native_session_status):
        return {'mode': 'background', 'status': 'not_started', 'project_id': context.project_id}
    return context.native_session_status(context.project_id, arguments.get('session_id'))


def _pause_native_engineering_session(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    if not context.project_id or not callable(context.native_session_pause):
        raise ValueError('No native engineering session is available in this selected project')
    return context.native_session_pause(context.project_id, arguments['session_id'])


def _get_current_project(context: ToolContext, _arguments: Mapping[str, Any]) -> Dict[str, Any]:
    project = context.project
    if not context.project_id:
        return {"selection_status": "none", "project": None, "next_actions": list(PROJECT_ACTIONS)}
    versions = [item for item in project.get("versions", []) or [] if isinstance(item, Mapping)]
    result = {
        "project_id": context.project_id,
        "name": str(project.get("name") or "未命名项目"),
        "plc_model": context.plc_model,
        "target_mode": str((context.version or {}).get('target_mode') or project.get("target_mode") or "ladder"),
        "workflow_mode": str(project.get("workflow_mode") or "generate"),
        "selected_version_id": context.version_id or None,
        "active_version_id": project.get("active_version_id"),
        "version_count": len(versions),
        "has_confirmed_spec": isinstance(project.get("confirmed_spec"), Mapping),
        "has_pending_review": bool(project.get("pending_review")),
    }
    return {**result, "selection_status": "selected", "project": dict(result), "next_actions": []}


def _device_summary(program_ir: Mapping[str, Any]) -> Dict[str, Any]:
    devices = program_ir.get("devices") or {}
    if not isinstance(devices, Mapping):
        return {"count": 0, "by_type": {}, "addresses": []}
    by_type: Dict[str, int] = {}
    for address, data in devices.items():
        kind = ""
        if isinstance(data, Mapping):
            kind = str(data.get("kind") or data.get("type") or "").upper()
        if not kind:
            match = re.match(r"^[A-Z]+", str(address).upper())
            kind = match.group(0) if match else "OTHER"
        by_type[kind] = by_type.get(kind, 0) + 1
    addresses = [str(value) for value in devices.keys()]
    return {
        "count": len(addresses),
        "by_type": dict(sorted(by_type.items())),
        "addresses": addresses[:120],
        "truncated": len(addresses) > 120,
    }


def _get_current_program_info(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if context.analysis_source is not None:
        source = context.analysis_source
        raw = source["raw_networks"]
        return {"available": True, "version_id": None, "editable": False,
                "source_ref": source["source_ref"], "plc_model": source["plc_model"],
                "program_name": source["program_name"], "coverage": source["coverage"],
                "network_count": len(raw), "networks_truncated": len(raw) > 100,
                "networks": [{key: value for key, value in row.items() if key != "raw_instructions"}
                             | {"instruction_count": len(row["raw_instructions"])} for row in raw[:100]],
                "devices": _device_summary(context.program_ir) if context.program_ir else {},
                "artifacts": {}, "runtime_verified": False}
    version = context.version
    if not isinstance(version, Mapping):
        return {"available": False, "reason": "NO_PROGRAM_SOURCE", "next_actions": list(SOURCE_ACTIONS),
                "message": "当前没有选中的已生成版本。"}

    artifacts = version.get("artifacts") or {}
    artifact_flags = {
        name: bool(value)
        for name, value in artifacts.items()
        if name in {"json", "ir", "program_csv", "comment_csv", "svg", "st", "st_from_ir", "fbd", "gxw", "write_report"}
    } if isinstance(artifacts, Mapping) else {}
    result: Dict[str, Any] = {
        "available": True,
        "version_id": context.version_id,
        "target_mode": str(version.get("target_mode") or context.project.get("target_mode") or "ladder"),
        "plc_model": context.plc_model,
        "program_name": str(version.get("program_name") or "MAIN"),
        "revision": version.get("revision"),
        "ir_schema_version": version.get("ir_schema_version"),
        "ir_sha256": version.get("ir_sha256"),
        "ladder_sha256": version.get("ladder_sha256"),
        "artifacts": artifact_flags,
    }
    program_ir = context.program_ir
    if isinstance(program_ir, Mapping):
        networks = [
            {
                "id": str(network.get("id") or ""),
                "comment": str(network.get("comment") or "")[:200],
                "instruction_count": len(network.get("instructions") or []),
                "reads": list(network.get("reads") or [])[:40],
                "writes": list(network.get("writes") or [])[:40],
            }
            for network in (program_ir.get("networks") or [])[:100]
            if isinstance(network, Mapping)
        ]
        result.update(
            {
                "network_count": len(program_ir.get("networks") or []),
                "networks": networks,
                "networks_truncated": len(program_ir.get("networks") or []) > 100,
                "devices": _device_summary(program_ir),
                "timing": copy.deepcopy(program_ir.get("timing") or {}),
                "static_analysis": {
                    "counts": copy.deepcopy(
                        (program_ir.get("analysis") or {}).get("counts") or {}
                    ),
                    "rules_checked": list(
                        (program_ir.get("analysis") or {}).get("rules_checked") or []
                    ),
                    "dependency_node_count": len(
                        (program_ir.get("analysis") or {})
                        .get("dependency_graph", {})
                        .get("nodes", [])
                    ),
                    "dependency_edge_count": len(
                        (program_ir.get("analysis") or {})
                        .get("dependency_graph", {})
                        .get("device_edges", [])
                    ),
                    "findings": copy.deepcopy(
                        ((program_ir.get("analysis") or {}).get("findings") or [])[:30]
                    ),
                },
            }
        )
    return result


def _validate_current_program(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if context.st_text is not None:
        from plc.st_validation import inspect_st_program
        st_context, sources = None, None
        if context.native_snapshot is not None:
            from gxw.st_program import project_context_snapshot
            from gxw.callable_sources import ProjectCallableSources
            selected = (context.version or {}).get('program_name')
            st_context = project_context_snapshot(context.native_snapshot, selected)
            if selected:
                sources = ProjectCallableSources.from_project(context.native_snapshot, selected)
        return {**inspect_st_program(context.st_text, plc_model=context.plc_model,
                    hardware_context=(_confirmed_spec(context) or {}).get('hardware_profile'),
                    confirmed_spec=_confirmed_spec(context), project_context=st_context, callable_sources=sources),
                "version_id": context.version_id}
    if not isinstance(context.ladder, Mapping):
        return {
            "available": False,
            "valid": False,
            "message": "当前没有可校验的梯形图版本。",
        }

    from inspection.engine import run_local_inspection

    report = run_local_inspection(
        context.ladder,
        report_type="program_review",
        request={"review_focus": "Agent 请求校验当前程序"},
        confirmed_spec=(context.version or {}).get("confirmed_spec_snapshot")
        or context.project.get("confirmed_spec"),
        plc_model=context.plc_model,
        base_version_id=context.version_id or None,
        trigger="agent_tool",
        depth="basic",
    )
    findings = []
    for finding in (report.get("findings") or [])[:30]:
        if not isinstance(finding, Mapping):
            continue
        findings.append(
            {
                key: copy.deepcopy(finding.get(key))
                for key in (
                    "finding_id",
                    "id",
                    "code",
                    "severity",
                    "title",
                    "message",
                    "suggestion",
                    "rung_ids",
                    "devices",
                )
                if finding.get(key) not in (None, "", [])
            }
        )
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in report.get("findings") or []:
        severity = str((finding or {}).get("severity") or "").lower()
        if severity in counts:
            counts[severity] += 1
    from plc.validation import SUPPORTED_PLC_MODELS
    profile_available = context.plc_model in SUPPORTED_PLC_MODELS
    conditions_unverified = any(
        item.get('category') in {'device_applicability_unverified', 'instruction_constraints_unverified'} or
        item.get('constraint_status') == 'unverified' for item in report.get('findings', []))
    available = report.get("status") != "unsupported"
    status = ('failed' if counts['error'] else 'unverified'
              if not profile_available or conditions_unverified or not available else 'passed')
    return {
        "available": available,
        "valid": status == 'passed',
        "status": status,
        "version_id": context.version_id,
        "plc_model": context.plc_model,
        "summary": str(report.get("summary") or ""),
        "counts": counts,
        "finding_count": len(report.get("findings") or []),
        "findings": findings,
        "findings_truncated": len(report.get("findings") or []) > len(findings),
        "base_json_hash": report.get("base_json_hash")
        or (report.get("base") or {}).get("json_sha256"),
    }


def _read_network(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from plc.core import PLCCore
    if context.analysis_source is not None:
        raw = next((row for row in context.analysis_source["raw_networks"]
                    if row["id"] == arguments["network_id"]), None)
        if raw is None:
            raise ToolPreconditionError("NETWORK_NOT_FOUND", "快照中没有此 Network。",
                next_actions=({"tool": "get_current_program_info", "arguments": {"source_ref": arguments["source_ref"]}},))
        if context.program_ir is None:
            return {"network": copy.deepcopy(raw), "coverage": "raw_instructions_only"}
        network = PLCCore().read_network(context.program_ir, arguments["network_id"])
        return {"network": network, "raw_instructions": copy.deepcopy(raw["raw_instructions"]),
                "coverage": copy.deepcopy(context.analysis_source["coverage"])}
    network = PLCCore().read_network(
        _require_program_ir(context),
        str(arguments.get("network_id") or ""),
    )
    return {"network": network}


def _get_diagnostics(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if context.st_text is not None:
        return _validate_current_program(context, {})
    from plc.core import PLCCore

    return {**dict(PLCCore().validate_project(_require_program_ir(context), _confirmed_spec(context))),
            "evidence_kind": "static_program_check", "runtime_verified": False}


def _validate_project(
    context: ToolContext, arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if arguments.get('native') is True:
        from application.native_validation import validate_native_snapshot
        snapshot = context.native_snapshot if context.native_snapshot is not None else context.fbd_baseline
        if snapshot is None or not context.version_id:
            raise ValueError('原生检查需要绑定当前版本已保存的 GXW 快照；仅有源码或 CSV 不足以启动原生检查。')
        return validate_native_snapshot(snapshot,
            project_id=context.project_id, version_id=context.version_id,
            program=arguments.get('program') or (context.version or {}).get('program_name'),
            expected_cpu=context.plc_model if (context.version or {}).get('target_mode') in {'ladder','st','sfc'} else None,
            target_mode=(context.version or {}).get('target_mode'), st_text=context.st_text,
            program_ir=context.program_ir, sfc_graph=context.sfc_graph)
    if 'program' in arguments:
        raise ValueError('program 仅用于 native=true 的原生检查上下文。')
    if (context.version or {}).get('target_mode') == 'sfc':
        snapshot = _read_sfc_project(context, {})
        return {'status': 'source_preserved', 'gx_compile': 'not_run',
                'issues': snapshot['model']['issues'], 'position_resolution': 'not-established'}
    if context.st_text is not None:
        return _validate_current_program(context, {})
    from plc.core import PLCCore

    return dict(
        PLCCore().validate_project(
            _require_program_ir(context),
            _confirmed_spec(context),
        )
    )


def _compile_project(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if (context.version or {}).get('target_mode') == 'sfc':
        return {'compiled': False, 'status': 'not_run',
                'message': 'SFC 子程序使用原生 validate_project(native=true) 编译与检查。'}
    if context.st_text is not None:
        return {"compiled": False, "status": "unverified", "artifacts": ["st"],
                "message": "ST 源码已保存；本地静态检查不能替代 GX Works2 编译。",
                "validation": _validate_current_program(context, {})}
    from plc.core import PLCCore
    from plc.validation import ValidationProfileUnavailableError

    core = PLCCore()
    program = _require_program_ir(context)
    try:
        compiled = core.compile_project(program)
    except ValidationProfileUnavailableError:
        return {"compiled": False, "status": "unverified", "artifacts": [],
                "message": "当前型号缺少本地校验规则，未执行本地编译；需要独立的原生工程检查。",
                "validation": core.validate_project(program, _confirmed_spec(context))}
    return {
        "compiled": True,
        "artifacts": sorted(compiled.get("artifacts") or {}),
        "hashes": copy.deepcopy(compiled.get("hashes") or {}),
    }


def _patch_program(context: ToolContext, arguments: Mapping[str, Any], *, generation_handoff=None) -> Dict[str, Any]:
    from plc.core import PLCCore
    from plc.ir import canonical_sha256

    program = _require_program_ir(context)
    if not isinstance(context.version, Mapping):
        raise ValueError("当前没有绑定候选补丁的基础版本。")
    patch = copy.deepcopy(arguments.get("patch"))
    if not isinstance(patch, Mapping):
        raise ValueError("patch 必须是 JSON 对象。")
    patch = dict(patch)
    patch.setdefault("base_revision", program.get("revision"))
    patch.setdefault("base_ir_sha256", canonical_sha256(program))
    patch.setdefault("target_revision", int(program.get("revision") or 0) + 1)
    core = PLCCore()
    confirmed_spec = copy.deepcopy(_confirmed_spec(context))
    candidate = dict(core.patch_program(program, patch, confirmed_spec))
    compiled = core.compile_project(candidate["candidate_ir"])
    action = {
        "type": "accept_candidate_patch",
        "project_id": context.project_id,
        "project_name": str(context.project.get("name") or "未命名项目"),
        "base_version_id": context.version_id,
        "candidate_id": candidate["candidate_id"],
        "base_revision": candidate["base_revision"],
        "base_ir_sha256": candidate["base_ir_sha256"],
        "target_revision": candidate["target_revision"],
        "candidate_ir_sha256": candidate["candidate_ir_sha256"],
        "confirmed_spec_hash": (
            canonical_sha256(confirmed_spec) if confirmed_spec is not None else None
        ),
        "diff": copy.deepcopy(candidate["diff"]),
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
        "artifact_hashes": copy.deepcopy(compiled.get("hashes") or {}),
        "_candidate_ir": copy.deepcopy(candidate["candidate_ir"]),
        "_confirmed_spec": confirmed_spec,
        "maintainability_review": copy.deepcopy(candidate.get("maintainability_review")),
        "_generation_handoff": copy.deepcopy(generation_handoff),
    }
    return {
        "requires_confirmation": True,
        "message": "候选补丁已通过确定性校验和临时编译，等待用户查看差异。",
        "maintainability_review": copy.deepcopy(candidate.get("maintainability_review")),
        "generation_handoff": copy.deepcopy(generation_handoff),
        "candidate_id": candidate["candidate_id"],
        "diff": copy.deepcopy(candidate["diff"]),
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
        "pending_action": action,
    }
