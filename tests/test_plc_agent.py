import json

import pytest

import agent_runtime.agent as plc_agent
from model_runtime.provider import (
    AssistantMessage,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    ToolResult,
)
from agent_runtime.agent import run_tool_agent, should_route_to_tool_agent
from agent_runtime.plc_tools import (
    FORBIDDEN_TOOL_NAMES,
    SAFE_TOOL_NAMES,
    ToolDefinition,
    ToolRegistry,
    build_default_tool_registry,
    build_tool_context,
)
from plc.ir import build_plc_ir


class _FakeProvider:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        yield from self.rounds.pop(0)


class _FakeRuntime:
    def __init__(self):
        self.calls = []

    def list_tools(self, context=None):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_network",
                    "description": "read",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def invoke(self, tool_call, context):
        self.calls.append((tool_call, context))
        envelope = {
            "ok": True,
            "tool": tool_call.name,
            "data": {"network": "N0001"},
        }
        return ToolResult(
            tool_call.id,
            tool_call.name,
            json.dumps(envelope, ensure_ascii=False),
            envelope,
        )


def test_default_tool_request_enables_streaming_without_json_response_mode():
    provider = _FakeProvider([[TextDelta("完成")]])

    run_tool_agent("当前版本？", context=_context(), provider=provider)

    request = provider.requests[0]
    assert request.stream is True
    assert request.options["response_format"] is None
    assert request.options["tool_choice"] == "auto"
    assert [item["function"]["name"] for item in request.tools] == list(
        SAFE_TOOL_NAMES
    )


def test_agent_uses_only_injected_provider_and_tool_runtime_for_full_loop():
    provider = _FakeProvider(
        [
            [ToolCallEnd(ToolCall("call_1", "read_network", "{}"))],
            [TextDelta("已读取 N0001。")],
        ]
    )
    runtime = _FakeRuntime()
    context = object()

    result = run_tool_agent(
        "读取当前网络",
        context=context,
        provider=provider,
        runtime=runtime,
    )

    assert result.content == "已读取 N0001。"
    assert runtime.calls == [(ToolCall("call_1", "read_network", "{}"), context)]
    assert isinstance(provider.requests[1].messages[-1], ToolResult)


def _ladder():
    return {
        "device_comments": {"X0": "启动", "Y0": "运行"},
        "rungs": [
            {
                "rung_id": 10,
                "debug_note": "启动输出",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0", "label": ""}],
                        "outputs": [{"type": "COIL", "address": "Y0", "label": ""}],
                    }
                ],
            }
        ],
    }


def _context(*, with_version=True):
    ladder = _ladder() if with_version else None
    program_ir = build_plc_ir(ladder, plc_model="FX3U", revision=3) if ladder else None
    version = (
        {
            "id": "v0003",
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "program_name": "MAIN",
            "revision": 3,
            "ir_schema_version": 1,
            "ir_sha256": "a" * 64,
            "ladder_sha256": "b" * 64,
            "artifacts": {
                "json": "ladder.json",
                "ir": "program.ir.json",
                "program_csv": "program.csv",
                "comment_csv": "comments.csv",
                "svg": "ladder.svg",
                "st_from_ir": "program_from_ir.st",
            },
            "confirmed_spec_snapshot": None,
        }
        if with_version
        else None
    )
    project = {
        "id": "project123",
        "name": "电机控制",
        "plc_model": "FX3U",
        "target_mode": "ladder",
        "workflow_mode": "generate",
        "active_version_id": "v0003" if with_version else None,
        "versions": [version] if version else [],
        "messages": [],
    }
    return build_tool_context(
        project,
        version=version,
        ladder=ladder,
        program_ir=program_ir,
    )


def test_default_registry_exposes_only_allowlisted_high_level_tools():
    registry = build_default_tool_registry()

    assert tuple(registry.names) == SAFE_TOOL_NAMES
    assert not set(registry.names).intersection(FORBIDDEN_TOOL_NAMES)
    assert [item["function"]["name"] for item in registry.schemas()] == list(
        SAFE_TOOL_NAMES
    )


def test_registry_rejects_forbidden_duplicate_unknown_and_malformed_calls():
    registry = ToolRegistry()
    calls = []
    definition = ToolDefinition(
        "safe_read",
        "safe",
        {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        },
        lambda _context, arguments: calls.append(arguments) or {"value": 1},
    )
    registry.register(definition)
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(definition)
    with pytest.raises(ValueError, match="forbidden"):
        registry.register(
            ToolDefinition(
                "mouse_click",
                "unsafe",
                {"type": "object", "properties": {}},
                lambda *_: {},
            )
        )

    unknown = registry.call("write_plc", "{}", _context())
    malformed = registry.call("safe_read", "{", _context())
    extra = registry.call(
        "safe_read", json.dumps({"query": "X0", "path": "secret"}), _context()
    )
    valid = registry.call("safe_read", {"query": "X0"}, _context())

    assert unknown["error"]["code"] == "UNKNOWN_TOOL"
    assert malformed["error"]["code"] == "INVALID_ARGUMENTS"
    assert extra["error"]["code"] == "INVALID_ARGUMENTS"
    assert calls == [{"query": "X0"}]
    assert valid == {"ok": True, "tool": "safe_read", "data": {"value": 1}}


def test_project_and_program_tools_return_bounded_sanitized_snapshots():
    context = _context()
    registry = build_default_tool_registry()

    project = registry.call("get_current_project", {}, context)["data"]
    program = registry.call("get_current_program_info", {}, context)["data"]

    assert project["project_id"] == "project123"
    assert project["selected_version_id"] == "v0003"
    assert "messages" not in project
    assert "api_key" not in json.dumps(project)
    assert program["version_id"] == "v0003"
    assert program["network_count"] == 1
    assert program["networks"][0]["reads"] == ["X0"]
    assert program["networks"][0]["writes"] == ["Y0"]
    assert program["devices"]["by_type"] == {"X": 1, "Y": 1}


def test_search_manual_tool_uses_selected_plc_and_caps_results(monkeypatch):
    captured = {}

    def fake_retrieve(query, **kwargs):
        captured.update({"query": query, **kwargs})
        return [
            {
                "id": "chunk-1",
                "manual_id": "fx3_manual",
                "source": "FX3U Manual",
                "page": "10",
                "pdf_page": 12,
                "section": "Timer",
                "chunk_type": "instruction",
                "instruction_opcode": "OUT_T",
                "text": "T0 timer semantics",
            }
        ]

    monkeypatch.setattr('knowledge.retriever.retrieve_fact_aware_knowledge', fake_retrieve)
    result = build_default_tool_registry().call(
        "search_plc_manual", {"query": "T0 怎么用", "top_k": 3}, _context()
    )

    assert result["ok"] is True
    assert captured == {
        "query": "T0 怎么用",
        "plc_model": "FX3U",
        "task_type": "analysis",
        "top_k": 3,
        "char_budget": 6500,
    }
    assert result["data"]["results"][0]["section"] == "Timer"


def test_validate_tool_runs_local_checks_and_import_tool_only_requests_confirmation():
    registry = build_default_tool_registry()
    context = _context()

    validation = registry.call("validate_current_program", {}, context)
    import_request = registry.call(
        "import_current_program_to_gxworks2", {}, context
    )

    assert validation["ok"] is True
    assert validation["data"]["available"] is True
    assert validation["data"]["version_id"] == "v0003"
    assert import_request["ok"] is True
    assert import_request["status"] == "confirmation_required"
    assert import_request["data"]["requires_confirmation"] is True
    assert import_request["data"]["pending_action"] == {
        "type": "import_current_program_to_gxworks2",
        "project_id": "project123",
        "project_name": "电机控制",
        "version_id": "v0003",
        "revision": 3,
        "program_name": "MAIN",
        "plc_model": "FX3U",
    }


def test_tool_agent_executes_real_multiround_calls_and_preserves_reasoning():
    provider = _FakeProvider(
        [
            [
                ReasoningDelta("需要先读取当前程序。"),
                ToolCallEnd(ToolCall("call_1", "get_current_program_info", "{}")),
            ],
            [TextDelta("当前是 v0003，共 1 个 Network。")],
        ]
    )

    result = run_tool_agent(
        "当前程序信息是什么？",
        context=_context(),
        provider=provider,
    )

    assert result.content == "当前是 v0003，共 1 个 Network。"
    assert result.rounds == 2
    assert result.audit == [
        {
            "round": 1,
            "tool": "get_current_program_info",
            "ok": True,
            "status": None,
            "error_code": None,
        }
    ]
    second_messages = provider.requests[1].messages
    assistant = second_messages[-2]
    tool_message = second_messages[-1]
    assert isinstance(assistant, AssistantMessage)
    assert assistant.reasoning == "需要先读取当前程序。"
    assert assistant.tool_calls[0].name == "get_current_program_info"
    assert isinstance(tool_message, ToolResult)
    assert json.loads(tool_message.content)["ok"] is True


def test_tool_agent_streams_canonical_reasoning_and_content():
    provider = _FakeProvider(
        [
            [
                ReasoningDelta("需要先读取"),
                ReasoningDelta("当前程序。"),
                ToolCallEnd(ToolCall("call_1", "get_current_program_info", "{}")),
            ],
            [TextDelta("当前是 "), TextDelta("v0003。")],
        ]
    )
    reasoning = []
    content = []
    progress = []

    result = run_tool_agent(
        "当前程序信息是什么？",
        context=_context(),
        provider=provider,
        on_reasoning_chunk=reasoning.append,
        on_content_chunk=content.append,
        on_progress=progress.append,
    )

    assert result.content == "当前是 v0003。"
    assert reasoning == ["需要先读取", "当前程序。"]
    assert content == ["当前是 ", "v0003。"]
    assert any("已确认工具：get_current_program_info" in item for item in progress)
    assert any("工具执行完成：get_current_program_info" in item for item in progress)
    second_messages = provider.requests[1].messages
    assert second_messages[-2].tool_calls == (
        ToolCall("call_1", "get_current_program_info", "{}"),
    )


def test_agent_import_call_returns_pending_action_without_executing_import():
    provider = _FakeProvider(
        [
            [
                ToolCallEnd(
                    ToolCall("call_i", "import_current_program_to_gxworks2", "{}")
                )
            ],
            [TextDelta("已准备导入，请在界面确认。")],
        ]
    )

    result = run_tool_agent(
        "把当前程序导入 GX Works2",
        context=_context(),
        provider=provider,
    )

    assert result.content == "已准备导入，请在界面确认。"
    assert len(result.pending_actions) == 1
    assert result.pending_actions[0]["version_id"] == "v0003"
    assert result.audit[0]["status"] == "confirmation_required"


def test_tool_agent_is_bounded():
    provider = _FakeProvider(
        [
            [ToolCallEnd(ToolCall("a", "get_current_project", "{}"))],
            [ToolCallEnd(ToolCall("b", "get_current_project", "{}"))],
        ]
    )

    with pytest.raises(RuntimeError, match="超过上限"):
        run_tool_agent(
            "当前项目？",
            context=_context(),
            provider=provider,
            max_rounds=2,
        )


@pytest.mark.parametrize(
    "text",
    [
        "把刚才生成的程序导进去",
        "请一键导入 GX Works2",
        "查一下 FX3U 手册里的 PLSY",
        "校验当前程序",
        "当前版本信息是什么？",
        "修改当前程序，把 X3 加入互锁",
    ],
)
def test_explicit_operational_requests_route_to_tool_agent(text):
    assert should_route_to_tool_agent(text)


@pytest.mark.parametrize(
    "text",
    [
        "生成一个带校验步骤的输送带程序",
        "做一个电机启动停止梯形图",
        "程序里增加手册推荐的急停逻辑",
    ],
)
def test_normal_generation_and_edit_requests_keep_existing_flow(text):
    assert not should_route_to_tool_agent(text)


def test_implicit_edit_routes_only_when_a_current_ladder_exists():
    request = "把 X3 急停加进去"

    assert not should_route_to_tool_agent(request)
    assert should_route_to_tool_agent(request, has_current_program=True)
    assert not should_route_to_tool_agent(
        "重新生成一个带 X3 急停的程序", has_current_program=True
    )
