"""Deterministic, allow-listed tools exposed to the PLC chat agent.

The language model never receives filesystem, mouse, keyboard, PLC write, or
device-force primitives.  Every tool operates on an immutable snapshot of the
currently selected project/version.  Candidate patches and GX synchronization
only create confirmation requests; the Qt main thread owns every commit point.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


SAFE_TOOL_NAMES = (
    "get_current_project",
    "get_generation_context",
    "create_program_candidate",
    "get_current_program_info",
    "read_network",
    "search_plc_manual",
    "get_diagnostics",
    "validate_project",
    "compile_project",
    "patch_program",
    "validate_current_program",
    "import_current_program_to_gxworks2",
)

FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "mouse_click",
        "keyboard_input",
        "delete_file",
        "write_plc",
        "force_device",
    }
)

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolContext:
    """Read-only project snapshot used for one agent turn."""

    project: Mapping[str, Any]
    version: Optional[Mapping[str, Any]] = None
    ladder: Optional[Mapping[str, Any]] = None
    program_ir: Optional[Mapping[str, Any]] = None

    @property
    def project_id(self) -> str:
        return str(self.project.get("id") or "")

    @property
    def version_id(self) -> str:
        return str((self.version or {}).get("id") or "")

    @property
    def plc_model(self) -> str:
        return str(
            (self.version or {}).get("plc_model")
            or self.project.get("plc_model")
            or "FX3U"
        ).upper()


def build_tool_context(
    project: Mapping[str, Any],
    *,
    version: Optional[Mapping[str, Any]] = None,
    ladder: Optional[Mapping[str, Any]] = None,
    program_ir: Optional[Mapping[str, Any]] = None,
) -> ToolContext:
    """Copy mutable UI/session state before handing it to a worker thread."""

    if not isinstance(project, Mapping):
        raise TypeError("project must be an object")
    return ToolContext(
        project=copy.deepcopy(dict(project)),
        version=copy.deepcopy(dict(version)) if isinstance(version, Mapping) else None,
        ladder=copy.deepcopy(dict(ladder)) if isinstance(ladder, Mapping) else None,
        program_ir=(
            copy.deepcopy(dict(program_ir))
            if isinstance(program_ir, Mapping)
            else None
        ),
    )


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: Callable[[ToolContext, Mapping[str, Any]], Mapping[str, Any]]
    confirmation_required: bool = False

    def api_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": copy.deepcopy(dict(self.parameters)),
            },
        }


@dataclass
class ToolRegistry:
    """Name-to-handler registry with a small JSON-schema validation subset."""

    _tools: Dict[str, ToolDefinition] = field(default_factory=dict)

    def register(self, definition: ToolDefinition) -> None:
        name = str(definition.name or "")
        if not _TOOL_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid tool name: {name!r}")
        if name in FORBIDDEN_TOOL_NAMES:
            raise ValueError(f"forbidden tool cannot be registered: {name}")
        if name in self._tools:
            raise ValueError(f"duplicate tool: {name}")
        schema = dict(definition.parameters or {})
        if schema.get("type") != "object":
            raise ValueError(f"tool {name} parameters must be an object schema")
        self._tools[name] = definition

    @property
    def names(self) -> Sequence[str]:
        return tuple(self._tools)

    def schemas(self) -> List[Dict[str, Any]]:
        return [definition.api_schema() for definition in self._tools.values()]

    @staticmethod
    def _decode_arguments(arguments: Any) -> Dict[str, Any]:
        if arguments in (None, ""):
            return {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError) as error:
                raise ValueError("arguments must be valid JSON") from error
        if not isinstance(arguments, Mapping):
            raise ValueError("arguments must be a JSON object")
        return dict(arguments)

    @staticmethod
    def _validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        missing = sorted(required.difference(arguments))
        if missing:
            raise ValueError("missing required argument(s): " + ", ".join(missing))
        if schema.get("additionalProperties") is False:
            extras = sorted(set(arguments).difference(properties))
            if extras:
                raise ValueError("unknown argument(s): " + ", ".join(extras))

        for name, value in arguments.items():
            rule = properties.get(name)
            if not isinstance(rule, Mapping):
                continue
            expected = rule.get("type")
            valid = True
            if expected == "string":
                valid = isinstance(value, str)
            elif expected == "integer":
                valid = isinstance(value, int) and not isinstance(value, bool)
            elif expected == "number":
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            elif expected == "boolean":
                valid = isinstance(value, bool)
            elif expected == "array":
                valid = isinstance(value, list)
            elif expected == "object":
                valid = isinstance(value, Mapping)
            if not valid:
                raise ValueError(f"argument {name!r} must be {expected}")
            if isinstance(value, str):
                if "minLength" in rule and len(value) < int(rule["minLength"]):
                    raise ValueError(f"argument {name!r} is too short")
                if "maxLength" in rule and len(value) > int(rule["maxLength"]):
                    raise ValueError(f"argument {name!r} is too long")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "minimum" in rule and value < rule["minimum"]:
                    raise ValueError(f"argument {name!r} is below minimum")
                if "maximum" in rule and value > rule["maximum"]:
                    raise ValueError(f"argument {name!r} is above maximum")
            if "enum" in rule and value not in rule["enum"]:
                raise ValueError(f"argument {name!r} is not an allowed value")

    def call(self, name: str, arguments: Any, context: ToolContext) -> Dict[str, Any]:
        definition = self._tools.get(str(name or ""))
        if definition is None:
            return {
                "ok": False,
                "tool": str(name or ""),
                "error": {
                    "code": "UNKNOWN_TOOL",
                    "message": "该工具未在白名单中。",
                },
            }
        try:
            decoded = self._decode_arguments(arguments)
            self._validate_arguments(definition.parameters, decoded)
        except ValueError as error:
            return {
                "ok": False,
                "tool": definition.name,
                "error": {
                    "code": "INVALID_ARGUMENTS",
                    "message": str(error),
                },
            }
        try:
            data = dict(definition.handler(context, decoded) or {})
        except Exception as error:  # tool errors are data, not agent-loop crashes
            return {
                "ok": False,
                "tool": definition.name,
                "error": {
                    "code": "TOOL_FAILED",
                    "message": str(error)[:1000],
                },
            }
        envelope = {"ok": True, "tool": definition.name, "data": data}
        if definition.confirmation_required:
            envelope["status"] = "confirmation_required"
        return envelope


_EMPTY_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _get_current_project(context: ToolContext, _arguments: Mapping[str, Any]) -> Dict[str, Any]:
    project = context.project
    versions = [item for item in project.get("versions", []) or [] if isinstance(item, Mapping)]
    return {
        "project_id": context.project_id,
        "name": str(project.get("name") or "未命名项目"),
        "plc_model": context.plc_model,
        "target_mode": str(project.get("target_mode") or "ladder"),
        "workflow_mode": str(project.get("workflow_mode") or "generate"),
        "selected_version_id": context.version_id or None,
        "active_version_id": project.get("active_version_id"),
        "version_count": len(versions),
        "has_confirmed_spec": isinstance(project.get("confirmed_spec"), Mapping),
        "has_pending_review": bool(project.get("pending_review")),
    }


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


def _get_generation_context(
    context: ToolContext, arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    from plc.generation_contract import generation_output_contract
    from application.generation_context import (
        _build_knowledge_context, build_generation_instructions, generation_user_input,
        public_generation_ladder, public_generation_specification, public_generation_value,
    )
    from shared.context_policy import context_policy_scope

    confirmed_spec = _confirmed_spec(context)
    from application.confirmed_generation_context import (
        CONFIRMED_GENERATION_REQUEST, project_confirmed_specification,
    )
    public_spec = public_generation_specification(confirmed_spec)
    source = context.ladder
    if isinstance(context.program_ir, Mapping):
        from plc.ir import ir_to_ladder
        source = ir_to_ladder(context.program_ir)
    current_ladder = public_generation_ladder(source)
    user_requirement = public_generation_value(str(arguments.get("user_requirement") or ""))
    target_mode = str(context.project.get("target_mode") or "ladder")
    is_edit_mode = target_mode == "ladder" and current_ladder is not None
    if target_mode == "ladder" and isinstance(confirmed_spec, Mapping) and confirmed_spec:
        public_spec = project_confirmed_specification(confirmed_spec)
        if not is_edit_mode:
            user_requirement = CONFIRMED_GENERATION_REQUEST
    model_request = generation_user_input(
        user_requirement, is_edit_mode=is_edit_mode, target_mode=target_mode,
    )
    generation_handoff = {}
    def capture_context(value):
        generation_handoff.update(value)
    with context_policy_scope():
        instructions = build_generation_instructions(
            model_request,
            plc_model=context.plc_model,
            target_mode=target_mode,
            is_edit_mode=is_edit_mode,
            confirmed_context=public_spec,
            current_version_json=current_ladder,
            # Clean retrieved text before assembly; generic path matching must
            # never rewrite application-owned schema patterns in the prompt.
            knowledge_builder=_build_knowledge_context,
            on_context=capture_context,
        )
    return {
        "project_id": context.project_id,
        "plc_model": context.plc_model,
        "target_mode": target_mode,
        "workflow_mode": str(context.project.get("workflow_mode") or "generate"),
        "has_confirmed_spec": isinstance(confirmed_spec, Mapping),
        "confirmed_spec": public_spec,
        "output_contract": generation_output_contract(allow_partial=is_edit_mode, plc_model=context.plc_model),
        "current_version_id": context.version_id or None,
        "generation_instructions": instructions,
        "generation_handoff": public_generation_value(generation_handoff),
        "generation_request": model_request,
    }


def _create_program_candidate(
    context: ToolContext, arguments: Mapping[str, Any], *, generation_handoff=None,
) -> Dict[str, Any]:
    from plc.core import PLCCore
    from plc.ir import canonical_sha256

    if str(context.project.get("target_mode") or "ladder") != "ladder":
        raise ValueError("只有 ladder 目标模式可以创建新程序候选。")
    core = PLCCore()
    confirmed_spec = copy.deepcopy(_confirmed_spec(context))
    candidate = core.create_program_candidate(
        arguments["ladder"],
        plc_model=context.plc_model,
        program_name=arguments.get("program_name", (context.program_ir or {}).get("program_name", "MAIN")),
        confirmed_spec=confirmed_spec,
        previous_program=context.program_ir,
    )
    compiled = core.compile_project(candidate["candidate_ir"], validation_profile=candidate["validation_profile"])
    if not isinstance(generation_handoff, dict):
        from application.confirmed_generation_context import project_confirmed_specification
        from plc.specification.provenance import handoff_snapshot
        generation_handoff = handoff_snapshot(
            project_confirmed_specification(confirmed_spec), stage="external_generate",
            evidence={"stage": "external_generate", "status": "not_recorded", "records": [],
                      "reason": "external_context_not_correlated"},
        )
    generation_handoff = copy.deepcopy(generation_handoff)
    generation_handoff["confirmed_spec_sha256"] = (
        canonical_sha256(confirmed_spec) if confirmed_spec is not None else None)
    generation_handoff["external_model_use"] = "not_observed"
    action = {
        "type": "accept_generated_program",
        "project_id": context.project_id,
        "project_name": str(context.project.get("name") or "未命名项目"),
        "program_name": candidate["candidate_ir"]["program_name"],
        "candidate_id": candidate["candidate_id"],
        "revision": candidate["revision"],
        "candidate_ir_sha256": candidate["candidate_ir_sha256"],
        "ladder_sha256": candidate["ladder_sha256"],
        "confirmed_spec_hash": (
            canonical_sha256(confirmed_spec) if confirmed_spec is not None else None
        ),
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
        "summary": copy.deepcopy(candidate["summary"]),
        "artifact_hashes": copy.deepcopy(compiled.get("hashes") or {}),
        "_candidate_ir": copy.deepcopy(candidate["candidate_ir"]),
        "_confirmed_spec": confirmed_spec,
        "_validation_profile": candidate["validation_profile"],
        "normalization": copy.deepcopy(candidate["normalization"]),
        "_generation_handoff": generation_handoff,
    }
    if context.program_ir is not None:
        action.update(base_version_id=context.version_id, base_ir_sha256=canonical_sha256(context.program_ir))
    return {
        "requires_confirmation": True,
        "message": "程序候选已通过结构检查和临时编译，等待工程确认；行为与原生验证尚未执行。",
        "candidate_id": candidate["candidate_id"],
        "revision": candidate["revision"],
        "summary": copy.deepcopy(candidate["summary"]),
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
        "validation_profile": candidate["validation_profile"],
        "normalization": copy.deepcopy(candidate["normalization"]),
        "generation_handoff": copy.deepcopy(generation_handoff),
        "verification": {
            "structural_checks_passed": True,
            "deterministic_checks_passed": True,
            "behavior_verified": False,
            "native_verified": False,
            "scope": "API structural acceptance, IR consistency and temporary artifact compilation only. Static findings are diagnostics. "
                     "Input sequences, timing and requirement coverage still need independent verification.",
        },
        "pending_action": action,
    }


def _get_current_program_info(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    version = context.version
    if not isinstance(version, Mapping):
        return {"available": False, "message": "当前没有选中的已生成版本。"}

    artifacts = version.get("artifacts") or {}
    artifact_flags = {
        name: bool(value)
        for name, value in artifacts.items()
        if name in {"json", "ir", "program_csv", "comment_csv", "svg", "st", "st_from_ir"}
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


def _search_plc_manual(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from knowledge.retriever import retrieve_knowledge

    query = str(arguments.get("query") or "").strip()
    top_k = int(arguments.get("top_k", 5))
    rows = retrieve_knowledge(
        query,
        plc_model=context.plc_model,
        task_type="analysis",
        top_k=top_k,
        char_budget=6500,
    )
    results = []
    for row in rows[:top_k]:
        if not isinstance(row, Mapping):
            continue
        results.append(
            {
                "id": str(row.get("id") or ""),
                "manual_id": str(row.get("manual_id") or ""),
                "source": str(row.get("source") or ""),
                "page": row.get("page"),
                "pdf_page": row.get("pdf_page"),
                "section": str(row.get("section") or ""),
                "chunk_type": str(row.get("chunk_type") or ""),
                "instruction_opcode": str(row.get("instruction_opcode") or ""),
                "text": str(row.get("text") or "")[:1800],
            }
        )
    return {
        "query": query,
        "plc_model": context.plc_model,
        "count": len(results),
        "results": results,
    }


def _validate_current_program(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
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
    return {
        "available": True,
        "valid": counts["error"] == 0,
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


def _require_program_ir(context: ToolContext) -> Mapping[str, Any]:
    if not isinstance(context.program_ir, Mapping):
        raise ValueError("当前没有可供 PLC Core 读取的程序 IR。")
    return context.program_ir


def _confirmed_spec(context: ToolContext):
    return (context.version or {}).get("confirmed_spec_snapshot") or context.project.get(
        "confirmed_spec"
    )


def _read_network(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from plc.core import PLCCore

    network = PLCCore().read_network(
        _require_program_ir(context),
        str(arguments.get("network_id") or ""),
    )
    return {"network": network}


def _get_diagnostics(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    from plc.core import PLCCore

    return dict(PLCCore().get_diagnostics(_require_program_ir(context)))


def _validate_project(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
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
    from plc.core import PLCCore

    compiled = PLCCore().compile_project(_require_program_ir(context))
    return {
        "compiled": True,
        "artifacts": sorted(compiled.get("artifacts") or {}),
        "hashes": copy.deepcopy(compiled.get("hashes") or {}),
    }


def _patch_program(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
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
    }
    return {
        "requires_confirmation": True,
        "message": "候选补丁已通过确定性校验和临时编译，等待用户查看差异。",
        "candidate_id": candidate["candidate_id"],
        "diff": copy.deepcopy(candidate["diff"]),
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
        "pending_action": action,
    }


def _request_gxworks2_import(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    version = context.version
    if not isinstance(version, Mapping):
        raise ValueError("当前没有选中的已生成版本。")
    if str(version.get("target_mode") or "") != "ladder":
        raise ValueError("只有梯形图版本可导入 GX Works2。")
    artifacts = version.get("artifacts") or {}
    missing = [
        label
        for key, label in (
            ("program_csv", "程序 CSV"),
            ("comment_csv", "软元件注释 CSV"),
        )
        if not isinstance(artifacts, Mapping) or not artifacts.get(key)
    ]
    if missing:
        raise ValueError("当前版本缺少" + "、".join(missing) + "。")
    return {
        "requires_confirmation": True,
        "message": "需要用户确认后，才能调用现有的一键导入服务。",
        "pending_action": {
            "type": "import_current_program_to_gxworks2",
            "project_id": context.project_id,
            "project_name": str(context.project.get("name") or "未命名项目"),
            "version_id": context.version_id,
            "revision": version.get("revision"),
            "program_name": str(version.get("program_name") or "MAIN"),
            "plc_model": context.plc_model,
        },
    }


def build_default_tool_registry() -> ToolRegistry:
    from application.generation_receipts import GenerationReceiptCache
    from plc.ir import canonical_sha256

    receipts = GenerationReceiptCache()

    def receipt_binding(context):
        return canonical_sha256({
            "project_id": context.project_id, "version_id": context.version_id,
            "plc_model": context.plc_model, "target_mode": context.project.get("target_mode"),
            "confirmed_spec": _confirmed_spec(context),
            "program": context.program_ir if context.program_ir is not None else context.ladder,
        })

    def generation_context(context, arguments):
        result = _get_generation_context(context, arguments)
        if result.get("generation_handoff"):
            result["generation_context_id"] = receipts.remember(
                receipt_binding(context), result["generation_handoff"],
            )
        return result

    def program_candidate(context, arguments):
        receipt_id = arguments.get("generation_context_id")
        handoff = receipts.resolve(receipt_id, receipt_binding(context)) if receipt_id else None
        if handoff is not None:
            handoff["external_context_id"] = receipt_id
        return _create_program_candidate(context, arguments, generation_handoff=handoff)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "get_current_project",
            "读取当前 PLC AI 项目的摘要、所选版本和工作流状态；不读取文件路径或凭据。",
            _EMPTY_OBJECT_SCHEMA,
            _get_current_project,
        )
    )
    registry.register(
        ToolDefinition(
            "get_generation_context",
            "读取与内置 API 共用的生成提示、当前程序、确认规格、PLC 型号和本地手册上下文；可传本次需求改善检索。无版本时也可用，不调用模型，不返回路径或模型配置。",
            {
                "type": "object",
                "properties": {
                    "user_requirement": {
                        "type": "string", "maxLength": 24000,
                        "description": "本次生成或修改需求；省略时仍返回当前工程上下文。",
                    },
                },
                "additionalProperties": False,
            },
            generation_context,
        )
    )
    registry.register(
        ToolDefinition(
            "create_program_candidate",
            "提交完整或局部 ladder JSON，复用 API 的兼容整理、结构检查和临时编译，返回待确认候选；不再次调用模型或导入 GX Works2。",
            {
                "type": "object",
                "properties": {
                    "program_name": {
                        "type": "string", "default": "MAIN",
                        "minLength": 1, "maxLength": 64,
                    },
                    "ladder": {
                        "type": "object",
                        "description": "Full or partial ladder JSON from get_generation_context; the shared API parser validates and normalizes compatible encodings.",
                    },
                    "generation_context_id": {
                        "type": "string", "maxLength": 128,
                        "description": "Copy the ID returned by get_generation_context to preserve source lineage. Optional; absence or expiry is recorded as a trace gap, not a validation failure.",
                    },
                },
                "required": ["ladder"],
                "additionalProperties": False,
            },
            program_candidate,
            confirmation_required=True,
        )
    )
    registry.register(
        ToolDefinition(
            "get_current_program_info",
            "读取当前所选程序版本的 IR 摘要、Network 读写关系、设备和生成产物状态。",
            _EMPTY_OBJECT_SCHEMA,
            _get_current_program_info,
        )
    )
    registry.register(
        ToolDefinition(
            "read_network",
            "按稳定 Network ID 读取当前程序中的一个完整网络。",
            {
                "type": "object",
                "properties": {
                    "network_id": {
                        "type": "string",
                        "description": "例如 N0001。",
                        "minLength": 2,
                        "maxLength": 32,
                    }
                },
                "required": ["network_id"],
                "additionalProperties": False,
            },
            _read_network,
        )
    )
    registry.register(
        ToolDefinition(
            "search_plc_manual",
            "在当前 PLC 型号的本地官方手册与调试案例知识库中检索事实依据。",
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的 PLC 指令、软元件、错误码或问题。",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，1 到 8。",
                        "minimum": 1,
                        "maximum": 8,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            _search_plc_manual,
        )
    )
    registry.register(
        ToolDefinition(
            "get_diagnostics",
            "读取当前 PLC IR 已有的确定性诊断、计数和已执行规则。",
            _EMPTY_OBJECT_SCHEMA,
            _get_diagnostics,
        )
    )
    registry.register(
        ToolDefinition(
            "validate_project",
            "通过 PLC Core 对当前程序执行完整 IR 与梯形图校验。",
            _EMPTY_OBJECT_SCHEMA,
            _validate_project,
        )
    )
    registry.register(
        ToolDefinition(
            "compile_project",
            "从当前 PLC IR 临时生成并校验 JSON、SVG、ST、程序 CSV 和注释 CSV；不会写入项目版本或操作 GX Works2。",
            _EMPTY_OBJECT_SCHEMA,
            _compile_project,
        )
    )
    registry.register(
        ToolDefinition(
            "patch_program",
            "在当前 IR 副本上增删改 Network，完成校验与临时编译后生成待用户确认的候选补丁。不会直接保存或同步 GX Works2。",
            {
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "object",
                        "description": "基于当前 revision/sha256 的 Network 级候选补丁。",
                        "properties": {
                            "base_revision": {"type": "integer"},
                            "base_ir_sha256": {"type": "string"},
                            "target_revision": {"type": "integer"},
                            "operations": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "operation": {
                                            "type": "string",
                                            "enum": [
                                                "add_network",
                                                "modify_network",
                                                "replace_network",
                                                "delete_network",
                                            ],
                                        },
                                        "network": {"type": "string"},
                                        "after": {"type": "string"},
                                        "ladder": {
                                            "type": "object",
                                            "description": "新增或替换 Network 的完整 ladder rung 对象。",
                                        },
                                    },
                                    "required": ["operation", "network"],
                                },
                            },
                            "device_comments": {
                                "type": "object",
                                "description": "软元件地址到注释；null 表示删除注释。",
                            },
                        },
                        "required": ["operations"],
                    }
                },
                "required": ["patch"],
                "additionalProperties": False,
            },
            _patch_program,
            confirmation_required=True,
        )
    )
    registry.register(
        ToolDefinition(
            "validate_current_program",
            "对当前所选梯形图运行确定性的本地结构校验与评审规则，不调用 AI 修改程序。",
            _EMPTY_OBJECT_SCHEMA,
            _validate_current_program,
        )
    )
    registry.register(
        ToolDefinition(
            "import_current_program_to_gxworks2",
            "请求把当前所选梯形图版本导入已打开的 GX Works2 工程。该工具只生成确认请求；用户明确确认后才会由既有高层导入服务执行。",
            _EMPTY_OBJECT_SCHEMA,
            _request_gxworks2_import,
            confirmation_required=True,
        )
    )
    if tuple(registry.names) != SAFE_TOOL_NAMES:
        raise RuntimeError("default PLC tool registry does not match its allow-list")
    return registry


__all__ = [
    "FORBIDDEN_TOOL_NAMES",
    "SAFE_TOOL_NAMES",
    "ToolContext",
    "ToolDefinition",
    "ToolRegistry",
    "build_default_tool_registry",
    "build_tool_context",
]
