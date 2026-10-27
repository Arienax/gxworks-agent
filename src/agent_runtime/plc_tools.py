# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Public tool catalog and compatibility imports; implementations have dedicated owners."""
from __future__ import annotations
from agent_runtime.tool_errors import ToolPreconditionError
from agent_runtime.tool_policy import (
    SAFE_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    SOURCE_TOOL_NAMES,
    DISCOVERY_TOOL_NAMES,
    NATIVE_INSPECTION_TOOL_NAMES,
    FORBIDDEN_TOOL_NAMES,
    _TOOL_NAME_RE,
)
from agent_runtime.tool_context import (
    ToolContext,
    build_tool_context,
    resolve_tool_context,
    _require_program_ir,
    _decision_receipt_id,
    _confirmed_spec,
)
from agent_runtime.tool_registry import (
    ToolDefinition,
    ToolRegistry,
)
from agent_runtime.engineering_schema import (
    _engineering_object,
    _engineering_text,
    _engineering_declarations,
    _engineering_configuration,
    _engineering_graph_actions,
    _engineering_authoring_commands,
    _operation_schema,
    _native_commands,
    _engineering_native_schema,
    _engineering_st_schema,
    _engineering_fbd_schema,
    _validate_engineering_input,
)
from agent_runtime.program_tools import (
    _get_operation_status,
    _get_native_engineering_session,
    _pause_native_engineering_session,
    _get_current_project,
    _device_summary,
    _get_current_program_info,
    _validate_current_program,
    _read_network,
    _get_diagnostics,
    _validate_project,
    _compile_project,
    _patch_program,
)
from agent_runtime.generation_tools import (
    _get_generation_context,
    _create_program_candidate,
)
from agent_runtime.manual_tools import (
    _search_plc_manual,
)
from agent_runtime.native_tools import (
    _request_native_engineering,
    _request_gxworks2_import,
    _get_fbd_catalog,
    _read_fbd_project,
    _read_fbd_libraries,
    _read_fbd_definition,
    _create_fbd_candidate,
    _read_sfc_project,
    _create_sfc_candidate,
    _read_st_project,
    _create_st_candidate,
)


def build_default_tool_registry() -> ToolRegistry:
    from agent_runtime.generation_tools import GenerationToolHandlers
    from agent_runtime.tool_catalog import default_tool_definitions
    from agent_runtime.inspection_tools import register_inspection_tools

    generation = GenerationToolHandlers(
        _get_generation_context, _create_program_candidate, _create_fbd_candidate, _patch_program)
    registry = ToolRegistry()
    for definition in default_tool_definitions(generation):
        registry.register(definition)
    register_inspection_tools(registry)
    if tuple(registry.names) != SAFE_TOOL_NAMES:
        raise RuntimeError("default PLC tool registry does not match its allow-list")
    return registry


__all__ = [
    "FORBIDDEN_TOOL_NAMES",
    "READ_ONLY_TOOL_NAMES",
    "SAFE_TOOL_NAMES",
    "ToolContext",
    "ToolDefinition",
    "ToolRegistry",
    "build_default_tool_registry",
    "build_tool_context",
]
