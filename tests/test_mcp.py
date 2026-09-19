"""Optional MCP tests: deterministic fixtures, no GUI, vendor software or network."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("mcp", reason="Install requirements-mcp.txt for MCP integration tests")

import anyio
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from integrations.mcp.context_provider import (
    ContextUnavailableError,
    SessionToolContextProvider,
    StaticToolContextProvider,
)
from integrations.mcp.server import create_server
from integrations.mcp.tool_adapter import MCPToolAdapter, to_mcp_result
from agent_runtime.messages import ToolCall, ToolResult
from agent_runtime.plc_tools import (
    FORBIDDEN_TOOL_NAMES,
    SAFE_TOOL_NAMES,
    ToolDefinition,
    ToolRegistry,
    build_tool_context,
)
from plc.core import PLCCore
from plc.ir import build_plc_ir, canonical_sha256
from storage.session import SessionStore
from agent_runtime.runtime import InProcessToolRuntime, build_default_tool_runtime


ROOT = Path(__file__).resolve().parents[1]


def _generation_ladder():
    return {
        "device_comments": {"X0": "Start", "X1": "Stop", "Y0": "Motor"},
        "rungs": [{
            "rung_id": 1, "header_element": None, "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [
                    {"type": "parallel_block", "branches": [
                        [{"type": "NO", "address": "X0"}],
                        [{"type": "NO", "address": "Y0"}],
                    ]},
                    {"type": "NC", "address": "X1"},
                ],
                "outputs": [{"type": "COIL", "address": "Y0"}],
            }],
        }],
    }


@pytest.fixture
def empty_project(tmp_path):
    store = SessionStore(base_dir=tmp_path / "empty-workspace", legacy_dir=tmp_path)
    project = store.create_project("Natural-language generation", plc_model="FX3U")
    store.set_confirmed_spec(project["id"], {
        "summary": "X0 Start, X1 Stop, Y0 Motor with seal-in",
        "io_table": [
            {"address": "X0", "kind": "X", "label": "Start"},
            {"address": "X1", "kind": "X", "label": "Stop"},
            {"address": "Y0", "kind": "Y", "label": "Motor"},
        ],
        "selected_approach": {"name": "Seal-in", "generation_contract": {"required_structures": ["self_hold"]}},
    })
    return store, project["id"]


@pytest.fixture
def saved_project(tmp_path):
    ladder = {
        "device_comments": {"X0": "启动", "Y0": "运行"},
        "rungs": [{
            "rung_id": 1, "debug_note": "启动输出", "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0", "label": ""}],
                "outputs": [{"type": "COIL", "address": "Y0", "label": ""}],
            }],
        }],
    }
    program = build_plc_ir(ladder, plc_model="FX3U", revision=1)
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("MCP 测试项目")
    version_id, directory = store.prepare_version(project["id"])
    compiled = PLCCore().compile_project(program, directory)
    version = store.complete_version(project["id"], version_id, {
        **store._ir_metadata(program), "target_mode": "ladder", "plc_model": "FX3U",
        "artifacts": dict(compiled["artifacts"]),
    })
    return store, project["id"], version, program


def _provider(saved_project, *, pin=False):
    store, project_id, version, _ = saved_project
    return SessionToolContextProvider(
        store.base_dir, project_id, version["id"] if pin else None
    )


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_discovery_and_schemas_come_from_the_runtime(saved_project):
    runtime = build_default_tool_runtime()
    adapter = MCPToolAdapter(runtime, _provider(saved_project))
    tools = adapter.list_tools()
    assert tuple(tool.name for tool in tools) == SAFE_TOOL_NAMES
    assert not {tool.name for tool in tools} & FORBIDDEN_TOOL_NAMES
    for tool, source in zip(tools, runtime.list_tools()):
        assert tool.description == source["function"]["description"]
        assert tool.input_schema == source["function"]["parameters"]
        Draft202012Validator.check_schema(tool.input_schema)
        assert tool.model_dump(by_alias=True)["inputSchema"] == tool.input_schema
    tools[0].input_schema["properties"]["injected"] = {"type": "string"}
    assert "injected" not in adapter.list_tools()[0].input_schema["properties"]


def test_mcp_client_discovers_and_calls_the_shared_runtime(saved_project):
    runtime = build_default_tool_runtime()
    calls = []
    original = runtime.invoke

    def invoke(call, context):
        calls.append((call, context))
        return original(call, context)

    runtime.invoke = invoke

    async def exercise():
        async with Client(create_server(_provider(saved_project), runtime)) as client:
            discovered = await client.list_tools()
            assert {tool.name for tool in discovered.tools} == set(SAFE_TOOL_NAMES)
            for name, arguments in [
                ("get_current_project", {}),
                ("get_current_program_info", {}),
                ("read_network", {"network_id": "N0001"}),
                ("get_diagnostics", {}),
                ("validate_project", {}),
                ("validate_current_program", {}),
                ("compile_project", {}),
            ]:
                result = await client.call_tool(name, arguments)
                assert result.is_error is False
                assert result.structured_content["ok"] is True
                assert json.loads(result.content[0].text) == result.structured_content
                assert result.meta["gxworks"]["project_id"] == saved_project[1]
                assert result.meta["gxworks"]["call_id"]
            assert calls[2][0].arguments == {"network_id": "N0001"}
            assert isinstance(calls[2][0], ToolCall)
            assert calls[2][1].program_ir == saved_project[3]

    anyio.run(exercise)


@pytest.mark.parametrize("name,arguments", [
    ("read_network", {}),
    ("read_network", {"network_id": 1}),
    ("get_current_project", {"auto_approve": True}),
    ("search_plc_manual", {"query": "X0", "top_k": True}),
    ("search_plc_manual", {"query": "X0", "top_k": 9}),
    ("patch_program", {"patch": []}),
])
def test_invalid_arguments_survive_the_mcp_boundary(saved_project, name, arguments):
    async def exercise():
        async with Client(create_server(_provider(saved_project))) as client:
            result = await client.call_tool(name, arguments)
            assert result.is_error is True
            assert result.structured_content["error"]["code"] == "INVALID_ARGUMENTS"

    anyio.run(exercise)


@pytest.mark.parametrize("arguments", [[], "{", 3, True])
def test_adapter_delegates_invalid_argument_decoding_to_registry(saved_project, arguments):
    result = MCPToolAdapter(build_default_tool_runtime(), _provider(saved_project)).call_tool(
        "read_network", arguments, "invalid"
    )
    assert result.is_error
    assert result.structured_content["error"]["code"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("name", [*sorted(FORBIDDEN_TOOL_NAMES), "arbitrary_shell", "approve_action"])
def test_forbidden_and_unknown_calls_never_invoke_runtime(saved_project, name):
    class UntrustedRuntime:
        def list_tools(self, context=None):
            return [{"function": {"name": name, "description": "unsafe", "parameters": {"type": "object"}}}]

        def invoke(self, call, context):
            pytest.fail("A tool outside SAFE_TOOL_NAMES was invoked")

    adapter = MCPToolAdapter(UntrustedRuntime(), _provider(saved_project))
    assert adapter.list_tools() == []
    assert adapter.call_tool(name, {}, "blocked").structured_content["error"]["code"] == "UNKNOWN_TOOL"

    async def exercise():
        async with Client(create_server(_provider(saved_project))) as client:
            result = await client.call_tool(name, {})
            assert result.is_error
            assert result.structured_content["error"]["code"] == "UNKNOWN_TOOL"

    anyio.run(exercise)


def test_runtime_tool_error_translation(saved_project):
    result = MCPToolAdapter(build_default_tool_runtime(), _provider(saved_project)).call_tool(
        "read_network", {"network_id": "N9999"}, "missing-network"
    )
    assert result.is_error
    assert result.structured_content["error"]["code"] == "TOOL_FAILED"
    assert "N9999" in result.structured_content["error"]["message"]
    assert result.meta["gxworks"]["call_id"] == "missing-network"


def test_public_projection_does_not_leak_or_truncate_structured_data():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        "get_current_project", "test", {"type": "object"},
        lambda *_: {
            "_private": "hidden-top", "long_text": "测" * 19000,
            "nested": [{"visible": 1, "_secret": "hidden-nested"}],
            "tuple": ({"_secret": "hidden-tuple", "visible": 2},),
            "audit": {"revision": 4, "hash": "public-hash"},
        },
    ))
    original = InProcessToolRuntime(registry).invoke(
        ToolCall("long", "get_current_project", {}), build_tool_context({"id": "test"})
    )
    assert len(original.content) > 19000
    assert len(json.loads(original.content)["data"]["long_text"]) == 19000
    assert "hidden-" not in original.content
    result = to_mcp_result(original)
    wire = result.model_dump_json(by_alias=True)
    assert "hidden-" not in wire
    assert "_private" not in wire
    assert json.loads(result.content[0].text) == result.structured_content
    assert len(result.structured_content["data"]["long_text"]) == 19000
    assert result.structured_content["data"]["audit"]["revision"] == 4
    assert original.data["data"]["_private"] == "hidden-top"


def test_text_only_tool_errors_are_preserved():
    result = to_mcp_result(ToolResult("text", "get_current_project", "backend unavailable", is_error=True))
    assert result.is_error
    assert result.structured_content is None
    assert result.content[0].text == "backend unavailable"


def test_confirmations_stay_pending_and_never_write_the_workspace(saved_project):
    store, project_id, version, program = saved_project
    before = _files(store.base_dir)
    rung = copy.deepcopy(program["networks"][0]["ladder"])
    rung["branches"][0]["inputs"].append({"type": "NC", "address": "X1", "label": ""})
    patch = {"operations": [{"operation": "modify_network", "network": "N0001", "ladder": rung}]}

    async def exercise():
        async with Client(create_server(_provider(saved_project))) as client:
            for name, arguments in [
                ("patch_program", {"patch": patch}),
                ("import_current_program_to_gxworks2", {}),
            ]:
                result = await client.call_tool(name, arguments)
                public = result.structured_content
                assert not result.is_error
                assert public["status"] == "confirmation_required"
                assert public["data"]["requires_confirmation"] is True
                pending = public["data"]["pending_action"]
                assert pending["project_id"] == project_id
                assert "_candidate_ir" not in result.model_dump_json()
                assert "_confirmed_spec" not in result.model_dump_json()
                if name == "patch_program":
                    assert pending["base_version_id"] == version["id"]
                    assert pending["candidate_ir_sha256"]
                    assert pending["artifact_hashes"]
                    assert pending["diff"]["modified"] == ["N0001"]
                else:
                    assert pending["version_id"] == version["id"]

    anyio.run(exercise)
    assert _files(store.base_dir) == before


def test_provider_loads_copies_and_follows_active_or_pinned_versions(saved_project):
    store, project_id, version, _ = saved_project
    provider = _provider(saved_project)
    pinned = _provider(saved_project, pin=True)
    context = provider.get_context()
    context.project["name"] = "mutated copy"
    assert provider.get_context().project["name"] == "MCP 测试项目"
    static = StaticToolContextProvider(context)
    context.project["name"] = "changed again"
    assert static.get_context().project["name"] == "mutated copy"
    snapshot = static.get_context()
    snapshot.project["name"] = "changed returned value"
    assert static.get_context().project["name"] == "mutated copy"
    second, _ = store.prepare_version(project_id)
    store.complete_version(project_id, second, {"target_mode": "st", "artifacts": {}})
    assert provider.get_context().version_id == second
    assert pinned.get_context().version_id == version["id"]


def test_legacy_context_does_not_persist_migration_but_desktop_still_does(saved_project):
    store, project_id, version, _ = saved_project
    ir_file = store.version_dir(project_id, version["id"]) / version["artifacts"]["ir"]
    ir_file.unlink()
    project = store.get_project(project_id)
    project["versions"][0]["artifacts"].pop("ir")
    store.save_project(project)
    before = _files(store.base_dir)
    context = _provider(saved_project).get_context()
    assert context.program_ir["revision"] == 1
    assert context.ladder["rungs"][0]["rung_id"] == 1
    assert _files(store.base_dir) == before
    store.load_program_ir(project_id, version["id"])
    assert ir_file.exists()
    assert store.get_version(project_id, version["id"])["artifacts"]["ir"]


def test_missing_context_and_path_escape_fail_closed(saved_project, tmp_path):
    store, project_id, version, _ = saved_project
    with pytest.raises(ContextUnavailableError):
        SessionToolContextProvider(tmp_path / "does-not-exist", project_id)
    assert not (tmp_path / "does-not-exist").exists()
    for invalid in ("../elsewhere", "C:\\elsewhere", "..", "a/b"):
        with pytest.raises(ContextUnavailableError):
            SessionToolContextProvider(store.base_dir, invalid)
    with pytest.raises(ContextUnavailableError):
        SessionToolContextProvider(store.base_dir, project_id, "v9999").get_context()
    project = store.get_project(project_id)
    project["versions"][0]["artifacts"]["ir"] = "../../outside.json"
    store.save_project(project)
    with pytest.raises(ContextUnavailableError, match="escapes"):
        _provider(saved_project).get_context()
    result = MCPToolAdapter(build_default_tool_runtime(), _provider(saved_project)).call_tool(
        "get_current_project", {}, "bad-context"
    )
    assert result.is_error
    assert result.structured_content["error"]["code"] == "CONTEXT_UNAVAILABLE"
    assert str(store.base_dir) not in result.content[0].text


def test_project_without_version_has_a_valid_context(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace")
    project = store.create_project("Empty project")
    provider = SessionToolContextProvider(store.base_dir, project["id"])
    context = provider.get_context()
    assert context.version is None and context.program_ir is None
    result = MCPToolAdapter(build_default_tool_runtime(), provider).call_tool("get_current_project", {}, "empty")
    assert not result.is_error


def test_generation_tool_schemas_describe_only_model_owned_input(empty_project):
    from plc.generation_contract import ladder_v1_schema

    store, project_id = empty_project
    adapter = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(store.base_dir, project_id))
    schemas = {tool.name: tool.input_schema for tool in adapter.list_tools()}
    context_schema = schemas["get_generation_context"]
    assert set(context_schema["properties"]) == {"user_requirement"}
    assert not context_schema.get("required")
    assert context_schema["additionalProperties"] is False
    schema = schemas["create_program_candidate"]
    assert schema["required"] == ["ladder"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"program_name", "ladder", "generation_context_id"}
    assert schema["properties"]["generation_context_id"]["type"] == "string"
    assert "generation_context_id" not in schema["required"]
    assert schema["properties"]["program_name"]["default"] == "MAIN"
    assert schema["properties"]["ladder"]["type"] == "object"
    assert "oneOf" not in schema["properties"]["ladder"]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate({"ladder": _generation_ladder()})
    for bad in [
        {"ladder": _generation_ladder(), "revision": 8},
        {"ladder": {"device_comments": {}, "rungs": [], "networks": []}},
        {"ladder": {"device_comments": {}, "rungs": "text"}},
    ]:
        # Transport owns top-level tool arguments; the shared API parser owns
        # ladder compatibility and structural errors after normalization.
        rejected = adapter.call_tool("create_program_candidate", bad, "invalid-generation")
        assert rejected.is_error


@pytest.mark.parametrize("output", [
    {"type": "COIL", "address": "Y0"},
    {"type": "PLS", "address": "M0"},
    {"type": "PLF", "address": "M1"},
    {"type": "TIMER", "address": "T0", "value": "K10"},
    {"type": "COUNTER", "address": "C0", "value": "K3"},
    {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K1", "D10"]},
])
def test_generation_schema_and_core_accept_the_documented_element_types(output):
    from plc.generation_contract import ladder_v1_schema

    ladder = _generation_ladder()
    rung = ladder["rungs"][0]
    rung["header_element"] = {"type": "BLOCK_INPUT", "expression": "= D0 K1", "label": None}
    rung["shared_inputs"] = [{"type": "NO", "address": "X3"}]
    rung["branches"][0]["inputs"].extend([
        {"type": "P", "address": "X4"}, {"type": "F", "address": "X5"},
        {"type": "COMPARE", "expression": "> D2 K10"},
    ])
    rung["branches"][0]["outputs"] = [output]
    Draft202012Validator(ladder_v1_schema()).validate(ladder)
    result = PLCCore().create_program_candidate(ladder, plc_model="FX3U")
    assert result["diagnostics"]["valid"] is True


@pytest.mark.parametrize("invalid", [
    {"type": "APP_INSTR", "opcode": "please run the motor", "operands": []},
    {"type": "BLOCK_OUTPUT", "expression": "UNVERIFIED Y0"},
    {"type": "COIL", "address": "Y0", "label": "L" * 65},
])
def test_generation_schema_and_core_reject_output_escape_hatches(invalid):
    from plc.generation_contract import ladder_v1_schema

    ladder = _generation_ladder()
    ladder["rungs"][0]["branches"][0]["outputs"] = [invalid]
    assert not Draft202012Validator(ladder_v1_schema()).is_valid(ladder)
    with pytest.raises(ValueError):
        PLCCore().create_program_candidate(ladder, plc_model="FX3U")


def test_versionless_generation_context_and_candidate_over_mcp(empty_project):
    store, project_id = empty_project
    before = _files(store.base_dir)
    spec = store.get_project(project_id)["confirmed_spec"]
    provider = SessionToolContextProvider(store.base_dir, project_id)

    async def exercise():
        async with Client(create_server(provider)) as client:
            context_result = await client.call_tool("get_generation_context", {})
            assert not context_result.is_error
            context = context_result.structured_content["data"]
            assert context["project_id"] == project_id
            assert context["plc_model"] == "FX3U"
            assert context["target_mode"] == "ladder"
            assert context["workflow_mode"] == "generate"
            assert context["has_confirmed_spec"] is True
            assert context["confirmed_spec"]["io_table"] == spec["io_table"]
            assert context["confirmed_spec"]["selected_approach"]["generation_contract"] == spec["selected_approach"]["generation_contract"]
            assert context["output_contract"]["format"] == "ladder_v1"
            Draft202012Validator(context["output_contract"]["schema"]).validate(_generation_ladder())
            result = await client.call_tool("create_program_candidate", {"ladder": _generation_ladder()})
            assert not result.is_error
            assert result.structured_content["status"] == "confirmation_required"
            assert result.structured_content["data"]["requires_confirmation"] is True
            verification = result.structured_content["data"]["verification"]
            assert verification["deterministic_checks_passed"] is True
            assert verification["behavior_verified"] is False
            assert verification["native_verified"] is False
            pending = result.structured_content["data"]["pending_action"]
            assert pending["type"] == "accept_generated_program"
            assert pending["project_id"] == project_id
            assert pending["program_name"] == "MAIN"
            assert pending["revision"] == 1
            assert pending["candidate_id"].startswith("candidate_")
            assert len(pending["candidate_ir_sha256"]) == 64
            assert pending["ladder_sha256"] == canonical_sha256(_generation_ladder())
            assert pending["confirmed_spec_hash"] == canonical_sha256(spec)
            assert pending["diagnostics"]["valid"] is True
            assert len(pending["artifact_hashes"]) == 6
            assert pending["summary"]["network_count"] == 1
            assert pending["summary"]["device_count"] == 3
            assert "_candidate_ir" not in result.model_dump_json()
            assert "_confirmed_spec" not in result.model_dump_json()
            assert json.loads(result.content[0].text) == result.structured_content
            assert result.meta["gxworks"]["version_id"] == ""

    anyio.run(exercise)
    assert _files(store.base_dir) == before
    project = store.get_project(project_id)
    assert project["versions"] == []
    assert project["active_version_id"] is None


def test_generation_context_has_no_private_state_or_credentials():
    secret = "never-expose-this-private-state"
    spec = {
        "summary": "confirmed motor requirement", "user_notes": "stop has priority",
        "workspace": secret, "api_key": secret, "provider_configuration": {"url": secret},
        "selected_approach": {"name": "Seal-in", "path": secret, "generation_contract": {
            "required_structures": ["self_hold"], "_secret": secret, "credentials": secret,
        }},
        "io_table": [{"address": "Y0", "label": "Motor", "path": secret}],
        "parameters": [{"name": "delay", "value": "K10", "widget": object(), "api_key": secret}],
        "hardware_profile": {"cpu_full_model": "FX3U-32MT/ES-A", "modules": ["FX3U-4AD-ADP"], "path": secret},
        "hardware_context": {"analog_module": {"input_module": "FX3U-4AD-ADP", "input_channel": 1, "provider": secret}},
        "logic_contracts": {"mutex": [["Y0", "Y1"]], "terminal_states": {"D0": [0, 9], "workspace": secret}},
        "execution_semantics": [{"semantic": "LEVEL", "devices": ["X0"], "_state": secret}],
        "approaches": [{"name": secret}], "pending_review": {"draft": secret},
    }
    context = build_tool_context({
        "id": "projection", "plc_model": "FX3U", "confirmed_spec": spec,
        "workspace": secret, "provider": secret, "api_key": secret,
        "messages": [{"content": secret}], "widget": object(),
    })
    result = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context)).call_tool(
        "get_generation_context", {}, "projection",
    )
    assert not result.is_error
    wire = result.model_dump_json()
    assert secret not in wire
    for private_name in ("api_key", "workspace", "provider_configuration", "widget", "pending_review"):
        assert private_name not in wire
    public = result.structured_content["data"]["confirmed_spec"]
    assert public["summary"] == spec["summary"]
    assert public["selected_approach"]["generation_contract"] == {"required_structures": ["self_hold"]}
    assert public["hardware_profile"]["modules"] == ["FX3U-4AD-ADP"]
    assert public["hardware_context"]["analog_module"] == {"input_module": "FX3U-4AD-ADP", "input_channel": 1}
    assert public["parameters"] == [{"name": "delay", "value": "K10"}]
    assert public["logic_contracts"] == {"mutex": [["Y0", "Y1"]], "terminal_states": {"D0": [0, 9]}}
    assert context.project["confirmed_spec"]["api_key"] == secret


def test_generation_uses_the_context_model_and_version_spec_snapshot():
    spec = {"summary": "Selected version spec", "selected_approach": {
        "name": "no motor", "generation_contract": {"forbidden_devices": ["Y0"]},
    }}
    context = build_tool_context(
        {"id": "authority", "plc_model": "FX3U", "confirmed_spec": {"summary": "current project spec"}},
        version={"id": "v0001", "plc_model": "FX5U", "confirmed_spec_snapshot": spec},
    )
    runtime = build_default_tool_runtime()
    projected = runtime.invoke(ToolCall("context", "get_generation_context", {}), context).data["data"]
    assert projected["plc_model"] == "FX5U"
    assert projected["confirmed_spec"]["summary"] == spec["summary"]
    failed = runtime.invoke(ToolCall("candidate", "create_program_candidate", {"ladder": _generation_ladder()}), context)
    assert not failed.is_error
    pending = failed.data["data"]["pending_action"]
    assert pending["_candidate_ir"]["plc"]["cpu"] == "FX5U"
    assert pending["_confirmed_spec"] == spec
    assert pending["_validation_profile"] == "generation_structural"
    no_spec = build_tool_context({"id": "empty", "plc_model": "FX5U"})
    projected = runtime.invoke(ToolCall("context", "get_generation_context", {}), no_spec).data["data"]
    assert projected["has_confirmed_spec"] is False
    assert projected["confirmed_spec"] is None
    generated = runtime.invoke(ToolCall("candidate", "create_program_candidate", {"ladder": _generation_ladder()}), no_spec)
    assert not generated.is_error
    assert generated.data["data"]["pending_action"]["_candidate_ir"]["plc"]["cpu"] == "FX5U"


@pytest.mark.parametrize("field", [
    "project_id", "plc_model", "revision", "confirmed_spec", "candidate_id",
    "candidate_ir_sha256", "ladder_sha256", "auto_approve",
])
def test_generation_rejects_client_overrides_of_server_fields(empty_project, field):
    store, project_id = empty_project
    result = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(store.base_dir, project_id)).call_tool(
        "create_program_candidate", {"ladder": _generation_ladder(), field: "override"}, "override",
    )
    assert result.is_error
    assert result.structured_content["error"]["code"] == "INVALID_ARGUMENTS"


def test_invalid_generated_ladder_is_an_mcp_tool_error_and_can_be_corrected(empty_project):
    store, project_id = empty_project
    adapter = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(store.base_dir, project_id))
    before = _files(store.base_dir)
    invalid = _generation_ladder()
    invalid["rungs"][0]["branches"][0]["outputs"] = [{"type": "APP_INSTR", "opcode": "UNVERIFIED", "operands": []}]
    result = adapter.call_tool("create_program_candidate", {"ladder": invalid}, "invalid")
    assert result.is_error
    assert result.structured_content["error"]["code"] == "TOOL_FAILED"
    assert "unsupported APP_INSTR" in result.structured_content["error"]["message"]
    assert "status" not in result.structured_content
    corrected = adapter.call_tool("create_program_candidate", {"ladder": _generation_ladder()}, "corrected")
    assert not corrected.is_error
    assert corrected.structured_content["status"] == "confirmation_required"
    assert _files(store.base_dir) == before


def test_context_rejects_concurrent_metadata_changes(saved_project, monkeypatch):
    provider = _provider(saved_project)
    original = provider._store.get_project
    calls = []

    def changing_project(project_id):
        project = original(project_id)
        calls.append(project)
        if len(calls) > 1:
            project["name"] = "changed during read"
        return project

    monkeypatch.setattr(provider._store, "get_project", changing_project)
    with pytest.raises(ContextUnavailableError, match="changed"):
        provider.get_context()


def test_unexpected_runtime_exception_is_a_sanitized_tool_error(saved_project, monkeypatch):
    runtime = build_default_tool_runtime()

    def fail(*_):
        raise RuntimeError("private failure details")

    monkeypatch.setattr(runtime, "invoke", fail)
    result = MCPToolAdapter(runtime, _provider(saved_project)).call_tool("get_current_project", {}, "error")
    assert result.is_error
    assert result.structured_content["error"]["code"] == "TOOL_FAILED"
    assert "private failure details" not in result.content[0].text


@pytest.mark.parametrize("without_version", [False, True])
def test_stdio_real_process_without_qt_and_with_noisy_runtime(saved_project, tmp_path, without_version):
    wrapper = tmp_path / "headless_server.py"
    wrapper.write_text('''import importlib.abc
import os
import sys

class BlockDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"PyQt6", "PyQt5", "qt_compat", "main", "api", "model_provider", "openai", "pywinauto", "win32com", "config", "config_manager", "credential_store"}:
            raise AssertionError("Unexpected desktop/provider dependency: " + fullname)

sys.meta_path.insert(0, BlockDesktop())
from integrations.mcp import server
from integrations.mcp.__main__ import main
original = server.build_default_tool_runtime

def noisy_runtime():
    runtime = original()
    invoke = runtime.invoke
    def noisy(call, context):
        print("python stdout diagnostic", flush=True)
        os.write(1, b"native stdout diagnostic\\n")
        return invoke(call, context)
    runtime.invoke = noisy
    return runtime

server.build_default_tool_runtime = noisy_runtime
raise SystemExit(main())
''', encoding="utf-8")
    store, project_id, _, _ = saved_project
    if without_version:
        project_id = store.create_project("headless initial generation")["id"]
    before = _files(store.base_dir)
    env = {key: value for key, value in os.environ.items() if not any(
        marker in key.upper() for marker in ("API_KEY", "DEEPSEEK", "OPENAI", "CREDENTIAL")
    )}
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(wrapper), "--stdio", "--workspace", str(store.base_dir), "--project", project_id],
        env={**env, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        cwd=tmp_path,
    )

    async def exercise():
        with anyio.fail_after(25):
            with (tmp_path / "stderr.txt").open("w", encoding="utf-8") as errors:
                async with stdio_client(parameters, errlog=errors) as streams:
                    async with ClientSession(*streams, read_timeout_seconds=10) as client:
                        initialized = await client.initialize()
                        assert initialized.server_info.name == "gxworks-agent"
                        assert "confirmation_required" in initialized.instructions
                        assert "do not start an automatic repeated-submission loop" in initialized.instructions
                        assert initialized.capabilities.tools is not None
                        assert {tool.name for tool in (await client.list_tools()).tools} == set(SAFE_TOOL_NAMES)
                        calls = [] if without_version else [
                            ("read_network", {"network_id": "N0001"}),
                            ("compile_project", {}),
                            ("import_current_program_to_gxworks2", {}),
                        ]
                        calls.extend([
                            ("get_generation_context", {}),
                            ("create_program_candidate", {"ladder": _generation_ladder()}),
                        ])
                        for name, arguments in calls:
                            result = await client.call_tool(name, arguments)
                            assert not result.is_error
                            assert result.structured_content["ok"]
                            if name == "create_program_candidate":
                                assert result.structured_content["status"] == "confirmation_required"
                                assert "_candidate_ir" not in result.model_dump_json()
                                assert "_confirmed_spec" not in result.model_dump_json()

    anyio.run(exercise)
    diagnostics = (tmp_path / "stderr.txt").read_text(encoding="utf-8")
    assert "python stdout diagnostic" in diagnostics
    assert "native stdout diagnostic" in diagnostics
    assert _files(store.base_dir) == before
    if without_version:
        assert store.get_project(project_id)["versions"] == []
        assert store.get_project(project_id)["active_version_id"] is None


def test_cli_help_and_setup_errors_use_only_stderr(tmp_path):
    from mcp_test_support import isolated_mcp_command, isolated_mcp_environment

    env = isolated_mcp_environment(PYTHONPATH=str(ROOT / "src"), PLC_AI_WORKSPACE_DIR="")
    for args, expected_code in [
        (["--help"], 0),
        (["--stdio", "--project", "absent"], 2),
        (["--stdio", "--workspace", str(tmp_path / "missing"), "--project", "absent"], 2),
    ]:
        result = subprocess.run(
            isolated_mcp_command(*args),
            env=env, cwd=tmp_path, capture_output=True, timeout=15,
        )
        assert result.returncode == expected_code
        assert result.stdout == b""
        assert result.stderr


def test_scriptable_stdio_smoke():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mcp_smoke.py")],
        cwd=ROOT, capture_output=True, encoding="utf-8", timeout=35,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["tool_count"] == len(SAFE_TOOL_NAMES)
    assert summary["called_tool"] == "read_network"
    assert summary["generation"] == {
        "called_tools": ["get_generation_context", "create_program_candidate"],
        "status": "confirmation_required", "revision": 1,
        "version_count": 0, "active_version_id": None, "workspace_unchanged": True,
    }
