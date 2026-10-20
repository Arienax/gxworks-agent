"""Tool exposure and context requirements shared by every transport."""
from __future__ import annotations
import re


SAFE_TOOL_NAMES = (
    "get_current_project",
    "get_operation_status",
    "get_native_engineering_session",
    "pause_native_engineering_session",
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
    "execute_native_engineering",
    "get_fbd_catalog",
    "read_fbd_project",
    "read_fbd_libraries",
    "read_fbd_definition",
    "create_fbd_candidate",
    "read_st_project",
    "create_st_candidate",
    "read_sfc_project",
    "create_sfc_candidate",
    "list_gxworks2_sessions",
    "read_gxworks2_snapshot",
    "list_simulator_targets",
    "get_simulator_status",
    "read_simulator_devices",
    "connect_simulator",
    "prepare_simulator_scenario",
    "start_simulator_trace",
    "get_simulator_trace",
    "stop_simulator_trace",
    "read_native_device_assignments",
    "compare_native_compiled_code",
    "locate_native_code_steps",
)


READ_ONLY_TOOL_NAMES = frozenset({
    "get_current_project", "get_operation_status", "get_native_engineering_session", "get_current_program_info", "read_network",
    "search_plc_manual", "get_diagnostics", "validate_project",
    "read_native_device_assignments", "compare_native_compiled_code", "locate_native_code_steps",
    "compile_project", "validate_current_program", "get_fbd_catalog",
    "read_fbd_project",
    "read_fbd_libraries",
    "read_fbd_definition",
    "read_sfc_project",
    "read_st_project",
    "list_gxworks2_sessions", "read_gxworks2_snapshot",
    "list_simulator_targets", "get_simulator_status", "read_simulator_devices",
    "connect_simulator", "prepare_simulator_scenario", "start_simulator_trace",
    "get_simulator_trace", "stop_simulator_trace",
})


SOURCE_TOOL_NAMES = frozenset({"get_current_program_info", "read_network", "get_diagnostics",
                              "validate_current_program", "validate_project"})


DISCOVERY_TOOL_NAMES = frozenset({"get_current_project", "list_gxworks2_sessions",
                                "read_gxworks2_snapshot", "list_simulator_targets",
                                "get_simulator_status", "read_simulator_devices",
                                "connect_simulator", "prepare_simulator_scenario", "start_simulator_trace",
                                "get_simulator_trace", "stop_simulator_trace"})


NATIVE_INSPECTION_TOOL_NAMES = frozenset({"read_native_device_assignments", "compare_native_compiled_code", "locate_native_code_steps"})


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
