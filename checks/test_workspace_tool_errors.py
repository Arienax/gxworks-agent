"""Workspace failures keep their stable classification across agent transports."""
from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from agent_runtime.messages import ToolCall  # noqa: E402
from agent_runtime.plc_tools import build_default_tool_registry  # noqa: E402
from agent_runtime.runtime import InProcessToolRuntime, public_tool_result_data  # noqa: E402
from agent_runtime.tool_context import build_tool_context  # noqa: E402
from agent_runtime.tool_registry import ToolDefinition, ToolRegistry  # noqa: E402
from gxworks2.workspace_adapter import WorkspaceSaveError, WorkspaceValidationError  # noqa: E402


def _saved_context(**kwargs):
    return build_tool_context(
        {"id": "project_one", "plc_model": "FX3U", "target_mode": "fbd"},
        version={"id": "version_one", "target_mode": "fbd", "program_name": "FBD_MATRIX.Program.pou"},
        **kwargs,
    )


def _failure_runtime(error):
    def fail(_context, _arguments):
        raise error

    registry = ToolRegistry()
    registry.register(ToolDefinition("validate_project", "Native validation", {
        "type": "object", "properties": {"native": {"type": "boolean"}},
        "additionalProperties": False,
    }, fail))
    return InProcessToolRuntime(registry)


def test_workspace_failures_retain_codes_details_and_actions():
    for error_type, code in (
            (WorkspaceValidationError, "WORKSPACE_PROTOCOL_MISMATCH"),
            (WorkspaceValidationError, "WORKSPACE_ADAPTER_UNAVAILABLE"),
            (WorkspaceSaveError, "WORKSPACE_ADAPTER_BUILD_FAILED")):
        for message in ("运行组件尚不可用。", "The runtime component is unavailable."):
            details = {"expected_protocol": 3, "actual_protocol": 2, "gx_compile": "not_run"}
            actions = [{"tool": "validate_project", "arguments": {"native": True}}]
            error = error_type(message, {"calls": ["internal observation"]},
                code=code, details=details, next_actions=actions, retryable=True)
            result = _failure_runtime(error).invoke(
                ToolCall("validation_call", "validate_project", {"native": True}), _saved_context())
            expected = {"ok": False, "tool": "validate_project", "error": {
                "code": code, "message": message, "details": details,
                "next_actions": actions, "retryable": True,
            }}
            assert result.is_error is True
            assert result.data == expected
            assert json.loads(result.content) == expected
            assert public_tool_result_data(result) == expected


def test_error_classification_does_not_depend_on_message_text():
    message = "原生工作区协议版本不匹配 / workspace protocol mismatch"
    error = WorkspaceValidationError(message)
    result = _failure_runtime(error).invoke(
        ToolCall("generic_workspace", "validate_project", {"native": True}), _saved_context())
    assert result.data["error"]["code"] == "WORKSPACE_ADAPTER_FAILED"
    assert "gx_compile" not in result.data["error"].get("details", {})
    assert "gx_compile" not in result.data["error"]
    other = _failure_runtime(ValueError(message)).invoke(
        ToolCall("generic_error", "validate_project", {"native": True}), _saved_context())
    assert other.data["error"]["code"] == "TOOL_FAILED"


def test_native_validation_uses_saved_bytes_without_starting_the_ide():
    from gxw.project_templates import blank_fbd_baseline

    raw = blank_fbd_baseline("FX3U")
    error = WorkspaceValidationError("Component contract differs.",
        code="WORKSPACE_PROTOCOL_MISMATCH",
        details={"expected_protocol": 3, "actual_protocol": 2, "gx_compile": "not_run"},
        retryable=True)
    runtime = InProcessToolRuntime(build_default_tool_registry())
    with patch("application.execution.DesktopResourceLock", return_value=nullcontext()), \
            patch("gxworks2.workspace_adapter.NativeWorkspaceValidation.validate", side_effect=error) as validate, \
            patch("gxworks2.finder.GXWorks2Finder.start",
                  side_effect=AssertionError("headless validation must not start the IDE")):
        result = runtime.invoke(ToolCall("headless_validation", "validate_project", {"native": True}),
                                _saved_context(native_snapshot=raw))
    validate.assert_called_once_with(raw, timeout=None)
    assert result.is_error is True
    assert result.data["error"] == error.payload()
    assert public_tool_result_data(result)["error"] == error.payload()


def test_native_validation_still_requires_a_saved_version():
    runtime = InProcessToolRuntime(build_default_tool_registry())
    context = build_tool_context({"id": "project_one", "plc_model": "FX3U"})
    with patch("gxworks2.workspace_adapter.NativeWorkspaceValidation.validate") as validate:
        result = runtime.invoke(ToolCall("missing_version", "validate_project", {"native": True}), context)
        validate.assert_not_called()
    assert result.is_error is True
    assert result.data["error"]["code"] == "NO_PROGRAM_SOURCE"


CASES = (test_workspace_failures_retain_codes_details_and_actions,
         test_error_classification_does_not_depend_on_message_text,
         test_native_validation_uses_saved_bytes_without_starting_the_ide,
         test_native_validation_still_requires_a_saved_version)


if __name__ == "__main__":
    for case in CASES:
        case()
        print("ok", case.__name__)
