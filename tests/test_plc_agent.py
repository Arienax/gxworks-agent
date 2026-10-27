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
    READ_ONLY_TOOL_NAMES,
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


def test_tool_execution_receipt_is_recorded_only_after_runtime_returns():
    from model_runtime.usage import usage_scope
    records = []
    class Runtime(_FakeRuntime):
        def invoke(self, call, context):
            assert records == []
            return super().invoke(call, context)
    provider = _FakeProvider([[ToolCallEnd(ToolCall("call", "read_network", "{}"))], [TextDelta("已完成")]])
    with usage_scope(lambda kind, data: records.append((kind,data))):
        run_tool_agent("读取当前网络", context=object(), provider=provider, runtime=Runtime())
    assert records == [("tool_execution", {"name":"read_network", "success":True})]


@pytest.mark.parametrize("name", ["create_program_candidate", "patch_program", "create_fbd_candidate",
                                 "import_current_program_to_gxworks2", "future_write_tool"])
def test_questions_hide_and_reject_engineering_tools_before_invocation(name):
    class Runtime(_FakeRuntime):
        def list_tools(self, context=None):
            return [{"type": "function", "function": {"name": tool, "parameters": {"type": "object"}}}
                    for tool in ("get_current_project", name)]
        def invoke(self, call, context):
            pytest.fail("Read-only assistant invoked a forbidden handler")
    runtime = Runtime()
    provider = _FakeProvider([[ToolCallEnd(ToolCall("blocked", name, "{}"))], [TextDelta("请切换到修改程序。")]])
    result = run_tool_agent("修改当前程序", context=_context(), runtime=runtime, provider=provider, read_only=True)
    assert [tool["function"]["name"] for tool in provider.requests[0].tools] == ["get_current_project"]
    assert not provider.requests[0].tool_response_contracts
    assert result.audit[0]["error_code"] == "READ_ONLY_TOOL"
    assert result.pending_actions == []
    assert provider.requests[1].messages[-1].is_error


def test_question_default_tools_are_explicitly_read_only_and_read_calls_still_work():
    provider = _FakeProvider([[ToolCallEnd(ToolCall("read", "get_current_project", "{}"))], [TextDelta("当前工程已读取。")]])
    result = run_tool_agent("当前工程？", context=_context(), provider=provider, read_only=True)
    assert {tool["function"]["name"] for tool in provider.requests[0].tools} == READ_ONLY_TOOL_NAMES
    assert result.audit[0]["ok"]
    assert not result.pending_actions


@pytest.mark.parametrize('arguments', [{'native': True}, '{"native":true}', {'program': 'MAIN.Program.pou'}])
def test_question_cannot_expand_ir_validation_into_native_execution(arguments):
    from agent_runtime.runtime import ReadOnlyToolRuntime, build_default_tool_runtime
    source = build_default_tool_runtime()
    runtime = ReadOnlyToolRuntime(source)
    schema = next(row['function'] for row in runtime.list_tools() if row['function']['name'] == 'validate_project')
    assert set(schema['parameters']['properties']) == {'source_ref'}
    assert 'native' in next(row['function'] for row in source.list_tools()
                           if row['function']['name'] == 'validate_project')['parameters']['properties']
    result = runtime.invoke(ToolCall('native-check', 'validate_project', arguments), _context())
    assert result.is_error and result.data['error']['code'] == 'READ_ONLY_TOOL'


def test_question_custom_read_tool_cannot_return_an_engineering_proposal():
    class Runtime(_FakeRuntime):
        def invoke(self, call, context):
            envelope = {"ok": True, "status": "confirmation_required", "data": {"pending_action": {
                "type": "accept_generated_program", "project_id": "project_test"}}}
            return ToolResult(call.id, call.name, json.dumps(envelope), envelope)
    provider = _FakeProvider([[ToolCallEnd(ToolCall("read", "get_current_project", "{}"))], [TextDelta("操作已阻止。")]])
    result = run_tool_agent("当前工程？", context=_context(), runtime=Runtime(), provider=provider, read_only=True)
    assert result.audit[0]["error_code"] == "READ_ONLY_TOOL"
    assert not result.pending_actions


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


def _read_complete(registry, name, arguments, context):
    from application.read_delivery import assemble_read_pages
    pages = [registry.call(name, arguments, context)]
    while not pages[-1]['data']['delivery']['complete']:
        followup = pages[-1]['data']['delivery']['next']
        pages.append(registry.call(followup['tool'], followup['arguments'], context))
    return {**pages[0], 'data': assemble_read_pages(pages)}


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
    result = _read_complete(build_default_tool_registry(),
        "search_plc_manual", {"query": "T0 怎么用", "top_k": 3}, _context()
    )

    assert result["ok"] is True
    assert captured == {
        "query": "T0 怎么用",
        "plc_model": "FX3U",
        "task_type": "analysis",
        "top_k": 3,
        "language": None,
        "aspects": None,
    }
    assert 'T0 timer semantics' in json.dumps(result["data"]["results"][0]['knowledge'])
    assert 'id' not in result['data']['results'][0]
    debug = _read_complete(build_default_tool_registry(), 'search_plc_manual', {'query': 'T0 怎么用', 'debug': True}, _context())
    assert debug['data']['results'][0]['section'] == 'Timer'
    assert debug['data']['results'][0]['id'] == 'chunk-1'


def test_search_default_projects_facts_and_explicit_debug_preserves_original(monkeypatch):
    import copy
    packet = {'content_origin': 'authored_fact', 'kind': 'instruction', 'target': 'SFTL',
        'technical_status': 'source_verified', 'source_evidence': [{'id': 'PRIVATE_SOURCE_SENTINEL'}],
        'value': {'facts': [{'id': 'PRIVATE_FACT_SENTINEL', 'dimension': 'memory.limit',
            'status': 'source_verified', 'depends_on': ['PRIVATE_DEPENDENCY_SENTINEL'],
            'value': {'maximum': 1024, 'shift_count': 'N2', 'source': 'S',
                'validation_results': ['PRIVATE_CHECK_SENTINEL'], 'native_compile': 'PRIVATE_TEST_SENTINEL'}}]}}
    row = {'id': 'PRIVATE_RECORD_SENTINEL', 'manual_id': 'PRIVATE_MANUAL_SENTINEL',
        'text': json.dumps(packet), 'fact_kind': 'instruction', 'fact_target': 'SFTL'}
    before = copy.deepcopy(row)
    monkeypatch.setattr('knowledge.retriever.retrieve_fact_aware_knowledge', lambda *a, **k: [row])
    registry = build_default_tool_registry()
    ordinary = _read_complete(registry, 'search_plc_manual', {'query': 'SFTL'}, _context())
    wire = json.dumps(ordinary)
    assert 'SENTINEL' not in wire
    # Provenance stays out of the default view; the fact's own applicability
    # status stays in, so a caller can tell a verified fact from a candidate.
    assert 'technical_status' not in wire and 'distribution_status' not in wire
    assert '1024' in wire and 'N2' in wire
    technical = ordinary['data']['results'][0]['knowledge'][0]
    assert technical['value']['facts'][0]['value']['source'] == 'S'
    assert technical['value']['facts'][0]['knowledge_status'] == 'source_verified'
    debug = _read_complete(registry, 'search_plc_manual', {'query': 'SFTL', 'debug': True}, _context())
    assert debug['data']['results'][0]['id'] == 'PRIVATE_RECORD_SENTINEL'
    assert json.loads(debug['data']['results'][0]['text']) == packet
    assert row == before


@pytest.mark.parametrize('name,debug', [('get_generation_context', False), ('create_program_candidate', False),
    ('read_network', False), ('get_diagnostics', True), ('validate_current_program', True)])
def test_tool_model_view_excludes_attached_audits_and_keeps_debug_records(name, debug):
    from agent_runtime.runtime import InProcessToolRuntime, public_tool_result_data
    raw = {'network_id': 'N0001', 'generation_context_id': 'context-token',
        'source': 'S', 'generation_handoff': {'source_id': 'PRIVATE_HANDOFF_SENTINEL'},
        'test_results': ['PRIVATE_TEST_SENTINEL'], 'validation': {'message': 'PRIVATE_CHECK_SENTINEL', 'native_source_save_required': True},
        'source_id': 'PRIVATE_SOURCE_SENTINEL', 'status': 'needs_input'}
    registry = ToolRegistry()
    registry.register(ToolDefinition(name, 'test', {'type': 'object', 'properties': {}}, lambda *a: raw))
    result = InProcessToolRuntime(registry).invoke(ToolCall('call', name, {}), _context())
    delivered = public_tool_result_data(result)
    assert json.loads(result.content) == delivered
    assert ('PRIVATE_TEST_SENTINEL' in result.content) == debug
    assert ('PRIVATE_SOURCE_SENTINEL' in result.content) == debug
    if name == 'get_generation_context':
        from application.read_delivery import assemble_read_pages
        delivered = {**delivered, 'data': assemble_read_pages([delivered])}
        assert raw['validation']['message'] == 'PRIVATE_CHECK_SENTINEL'
    else:
        assert result.data['data']['validation'] == raw['validation']
    assert delivered['data']['network_id'] == 'N0001'
    assert delivered['data']['generation_context_id'] == 'context-token'
    assert delivered['data']['source'] == 'S' and delivered['data']['status'] == 'needs_input'
    if not debug:
        assert delivered['data']['native_source_save_required'] is True


@pytest.mark.parametrize("model", ["FX1S", "FX2N", "Q03UDE", "L02S"])
def test_validate_tool_reports_missing_cpu_policy_without_false_pass(model, monkeypatch):
    # Exercise a genuinely missing policy; these CPUs now have source-backed profiles.
    from plc import validation
    monkeypatch.setattr(validation, "SUPPORTED_PLC_MODELS", validation.SUPPORTED_PLC_MODELS - {model})
    original = _context()
    context = build_tool_context(
        {"id": "new-cpu", "plc_model": model},
        version={"id": "v0001", "plc_model": model}, ladder=original.ladder,
    )
    result = build_default_tool_registry().call("validate_current_program", {}, context)
    assert result["ok"] is True
    data = result["data"]
    assert data["plc_model"] == model
    assert data["available"] is False and data["valid"] is False
    assert data["status"] == "unverified"
    assert data["counts"]["error"] == 0 and data["findings"] == []
    assert model in data["summary"] and "未验证" in data["summary"]


@pytest.mark.parametrize("source, target, status", [("M0", "M1", "passed"), ("X0", "Y0", "passed"), ("X8", "Y0", "failed")])
def test_validate_tool_keeps_fx3uc_address_policy_scoped(source, target, status):
    ladder = _ladder()
    ladder["device_comments"] = {source: "启动", target: "运行"}
    branch = ladder["rungs"][0]["branches"][0]
    branch["inputs"][0]["address"] = source
    branch["outputs"][0]["address"] = target
    context = build_tool_context(
        {"id": "source-subset", "plc_model": "FX3UC"},
        version={"id": "v0001", "plc_model": "FX3UC"}, ladder=ladder,
    )
    result = build_default_tool_registry().call("validate_current_program", {}, context)
    assert result["ok"] is True
    assert result["data"]["available"] is True
    assert result["data"]["status"] == status
    assert result["data"]["valid"] is (status == "passed")
    if status == "failed":
        assert result["data"]["counts"]["error"] == 1
        assert "FX3UC" in result["data"]["findings"][0]["message"]


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


@pytest.mark.parametrize('fail_continuation', [False, True])
def test_web_model_receives_complete_paged_knowledge_without_extra_model_rounds(fail_continuation):
    from application.read_delivery import ReadDeliveryStore, assemble_read_pages
    from agent_runtime.runtime import InProcessToolRuntime
    facts = [{'id': str(i), 'dimension': 'effects.manual', 'value': {'detail': 'a' * 900},
              'scope': {'models': ['FX3U']}, 'knowledge_status': 'source_verified'} for i in range(25)]
    registry = ToolRegistry()
    registry._read_delivery = ReadDeliveryStore(max_tokens=1000, max_bytes=4000)
    registry.register(ToolDefinition('search_plc_manual', 'read',
        {'type': 'object', 'properties': {'query': {'type': 'string'}}},
        lambda *_: {'knowledge': [{'facts': facts}]}))
    base = InProcessToolRuntime(registry)
    class Runtime:
        def list_tools(self, context=None):
            return base.list_tools(context)
        def invoke(self, call, context):
            if fail_continuation and isinstance(call.arguments, dict) and call.arguments.get('cursor'):
                error = {'ok': False, 'error': {'code': 'STALE_READ_CURSOR'}}
                return ToolResult(call.id, call.name, json.dumps(error), error, True)
            return base.invoke(call, context)
    provider = _FakeProvider([[ToolCallEnd(ToolCall('read', 'search_plc_manual', {'query': 'ANDP'}))],
                              [TextDelta('已读取')]])
    result = run_tool_agent('查询 ANDP', context=_context(), runtime=Runtime(), provider=provider,
                            read_only=True, max_rounds=2)
    assert result.rounds == len(provider.requests) == 2
    message = provider.requests[1].messages[-1]
    body = json.loads(message.content)
    if fail_continuation:
        assert message.is_error and body['error']['code'] == 'STALE_READ_CURSOR'
        assert 'knowledge' not in body
    else:
        assert body['data']['delivery']['complete'] is True
        assert body['data']['delivery']['pages_received'] > 8
        assert 'limits' not in body['data']['delivery']
        body['data']['delivery']['page'] = 0
        assert assemble_read_pages([body])['knowledge'][0]['facts'] == facts


def test_web_resume_does_not_claim_earlier_pages_were_delivered():
    from agent_runtime.agent import _complete_read_sequence
    envelope = {'ok': True, 'data': {'document': {'$ref': '#/objects/earlier'}, 'objects': {},
        'delivery': {'version': 1, 'document_id': 'read-one', 'page': 3, 'complete': True,
                     'scope': 'read_sequence'}}}
    class Runtime:
        def invoke(self, call, context):
            return ToolResult(call.id, call.name, json.dumps(envelope), envelope)
    result = _complete_read_sequence(Runtime(), ToolCall('read', 'search_plc_manual', {'cursor': 'read-one:3'}), None)
    assert result.data['data']['delivery']['scope'] == 'read_sequence'
    assert 'pages_received' not in result.data['data']['delivery']


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

@pytest.mark.parametrize("value,valid", [
    ({"count": 2, "mode": 1}, True),
    ({"count": True, "mode": 1}, False),
    ({"count": 2, "mode": True}, False),
    ({"count": float("inf"), "mode": 1}, False),
    ({"count": 2, "mode": 1, "vendor_layout": 12}, False),
    ({"count": 2}, False),
])
def test_all_registered_tools_validate_nested_arguments_before_the_handler(value, valid):
    calls = []
    schema = {"type": "object", "required": ["rows"], "additionalProperties": False,
        "properties": {"rows": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["count", "mode"], "additionalProperties": False,
            "properties": {"count": {"type": "number", "minimum": 0, "maximum": 5},
                           "mode": {"enum": [0, 1]}}}}}}
    registry = ToolRegistry()
    registry.register(ToolDefinition("nested_arguments", "Test shared argument boundary", schema,
                                    lambda context, arguments: calls.append(arguments) or {"valid": True}))
    result = registry.call("nested_arguments", {"rows": [value]}, build_tool_context({"id": "project"}))
    assert result["ok"] is valid
    assert len(calls) == int(valid)
    if not valid:
        assert result["error"]["code"] == "INVALID_ARGUMENTS"
