"""Immutable engineering snapshots and explicit context preconditions."""
from __future__ import annotations
import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from agent_runtime.tool_errors import ToolPreconditionError, PROJECT_ACTIONS, SOURCE_ACTIONS
from agent_runtime.tool_policy import (
    SOURCE_TOOL_NAMES,
)


@dataclass(frozen=True)
class ToolContext:
    """Read-only project snapshot used for one agent turn."""

    project: Mapping[str, Any]
    version: Optional[Mapping[str, Any]] = None
    ladder: Optional[Mapping[str, Any]] = None
    program_ir: Optional[Mapping[str, Any]] = None
    fbd_baseline: Optional[bytes] = None
    native_snapshot: Optional[bytes] = None
    st_text: Optional[str] = None
    sfc_graph: Optional[Mapping[str, Any]] = None
    inspection: Any = None
    analysis_source: Optional[Mapping[str, Any]] = None
    operation_status: Any = None
    native_session_status: Any = None
    native_session_pause: Any = None

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
    fbd_baseline: Optional[bytes] = None,
    native_snapshot: Optional[bytes] = None,
    st_text: Optional[str] = None,
    sfc_graph: Optional[Mapping[str, Any]] = None,
    inspection=None,
    analysis_source=None,
    operation_status=None,
    native_session_status=None,
    native_session_pause=None,
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
        fbd_baseline=bytes(fbd_baseline) if fbd_baseline is not None else None,
        native_snapshot=bytes(native_snapshot) if native_snapshot is not None else None,
        st_text=st_text,
        sfc_graph=copy.deepcopy(dict(sfc_graph)) if isinstance(sfc_graph, Mapping) else None,
        inspection=inspection,
        analysis_source=copy.deepcopy(analysis_source),
        operation_status=operation_status,
        native_session_status=native_session_status,
        native_session_pause=native_session_pause,
    )


def resolve_tool_context(definition, context, arguments):
    """Resolve only the context this operation needs, never infer a project."""
    from dataclasses import replace
    context = context or build_tool_context({})
    if arguments.get("source_ref"):
        if definition.name not in SOURCE_TOOL_NAMES or arguments.get("native"):
            raise ToolPreconditionError("PERMISSION_DENIED", "分析快照仅允许程序读取和静态检查。")
        if context.inspection is None:
            raise ToolPreconditionError("STALE_SOURCE_REF", "分析快照不属于当前会话。", next_actions=PROJECT_ACTIONS)
        context = context.inspection.source_context(arguments["source_ref"])
    requirement = definition.context_requirement
    if (definition.name == "validate_project" and arguments.get("native")) or (
            definition.name in {"create_fbd_candidate", "create_sfc_candidate"}
            and arguments.get("operation") in {"edit", "compile", "create_fb", "create_program", "import_library", "create_structure", "create_st_fb", "edit_st_fb", "edit_global_labels", "create_global_labels", "edit_pou_labels", "configure_project"}):
        requirement = "version"
    if definition.name == 'create_st_candidate' and arguments.get('operation') in {'create_st_program', 'edit_st_program'}:
        requirement = 'version'
    if requirement == "none":
        return context
    if requirement == "model":
        supplied = arguments.get("plc_model")
        actual = (context.version or {}).get("plc_model") or context.project.get("plc_model")
        if supplied and actual and supplied.upper() != actual.upper():
            raise ToolPreconditionError("MODEL_MISMATCH", "检索型号与当前工程不一致。")
        if not (supplied or actual):
            raise ToolPreconditionError("PLC_MODEL_REQUIRED", "手册检索需要明确 CPU 型号。",
                next_actions=({"tool": "search_plc_manual", "required_arguments": ["query", "plc_model"]},))
        if supplied and not actual:
            from plc.project_capabilities import normalize_project_selection
            selection = normalize_project_selection(supplied, "ladder", None, None)
            context = replace(context, project={"plc_model": selection["plc_model"]})
        return context
    if not context.project_id and context.analysis_source is None:
        raise ToolPreconditionError("NO_PROJECT_SELECTED", "未选中 Agent 工程；可先发现原生工程并读取分析快照。",
                                    next_actions=PROJECT_ACTIONS)
    if requirement == "version" and not context.version_id:
        raise ToolPreconditionError("NO_PROGRAM_SOURCE", "此操作需要已保存的工程版本。", next_actions=SOURCE_ACTIONS)
    if requirement == "program" and not any((context.program_ir, context.ladder, context.native_snapshot,
                                             context.fbd_baseline, context.st_text, context.analysis_source)):
        # Program-info queries report absence as state, rather than an error.
        if definition.name != "get_current_program_info":
            raise ToolPreconditionError("NO_PROGRAM_SOURCE", "当前工程没有可分析的程序来源。", next_actions=SOURCE_ACTIONS)
    if context.analysis_source and context.program_ir is None and definition.name not in {"get_current_program_info", "read_network"}:
        raise ToolPreconditionError("SOURCE_NOT_PARSEABLE", "快照保留原始指令，但不能进行完整静态检查。",
            next_actions=({"tool": "get_current_program_info", "arguments": {"source_ref": arguments.get("source_ref")}},),
            details={"analysis_source": {key: copy.deepcopy(value) for key, value in context.analysis_source.items()
                                         if key != "raw_networks"}})
    return context


def _require_program_ir(context: ToolContext) -> Mapping[str, Any]:
    if not isinstance(context.program_ir, Mapping):
        raise ValueError("当前没有可供 PLC Core 读取的程序 IR。")
    return context.program_ir


def _decision_receipt_id(context: ToolContext):
    # The same binding as _confirmed_spec: a historical version must never
    # acquire the current project's newer decision merely because it lacks one.
    if (context.version or {}).get("confirmed_spec_snapshot"):
        handoff = (context.version or {}).get("generation_handoff") or {}
        return handoff.get("decision_receipt_id") if isinstance(handoff, Mapping) else None
    return context.project.get("confirmed_decision_receipt_id")


def _confirmed_spec(context: ToolContext):
    return (context.version or {}).get("confirmed_spec_snapshot") or context.project.get(
        "confirmed_spec"
    )
