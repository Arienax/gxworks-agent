"""API/MCP context parity without model calls or private workspace state."""

from tools.offline_inputs import private_path
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import application.model_api as api
import knowledge.retriever as knowledge_retriever
from agent_runtime.plc_tools import build_default_tool_registry as _build_registry, build_tool_context
from application.generation_context import generation_user_input, public_generation_ladder
from application.confirmed_generation_context import CONFIRMED_GENERATION_REQUEST


def build_default_tool_registry():
    # Semantic parity assertions consume the full read sequence. Transport size,
    # cursor rejection and exact wire bodies are independently tested in MCP.
    from application.read_delivery import assemble_read_pages
    registry = _build_registry()
    class CompleteReads:
        def call(self, name, arguments, context):
            result = registry.call(name, arguments, context)
            if not result.get('ok') or 'delivery' not in result.get('data', {}):
                return result
            pages = [result]
            while pages[-1]['data']['delivery']['next']:
                nxt = pages[-1]['data']['delivery']['next']
                page = registry.call(nxt['tool'], nxt['arguments'], context)
                assert page['ok'], page
                pages.append(page)
            return {**result, 'data': assemble_read_pages(pages)}
    return CompleteReads()


def test_generation_receipt_binding_tracks_the_selected_version_mode():
    from dataclasses import replace
    from agent_runtime.generation_tools import GenerationToolHandlers
    context = build_tool_context({'id': 'p1', 'plc_model': 'Q03UDV', 'target_mode': 'ladder'},
                                 version={'id': 'v1', 'target_mode': 'ladder'})
    before = GenerationToolHandlers._binding(context)
    changed = replace(context, version={**context.version, 'target_mode': 'fbd'})
    assert GenerationToolHandlers._binding(changed) != before
    project_only = replace(changed, project={**changed.project, 'target_mode': 'st'})
    assert GenerationToolHandlers._binding(project_only) == GenerationToolHandlers._binding(changed)


def test_sorting_request_keeps_user_wiring_without_recalling_ordinary_device_chapters():
    from application.confirmed_generation_context import build_direct_generation_context
    requirement = ("X001：启动按钮；X003：停止按钮；D0：Vision Sensor 分类结果。\n"
                   "D0=1~3为蓝色，4~6为绿色，7~9为灰色。\n"
                   "Y000：入口输送带；Y002：出口输送带；Y003：Sorter 1 turn；Y004：Sorter 1 belt；"
                   "Y005：Sorter 2 turn；Y006：Sorter 2 belt；Y007：Sorter 3 turn；Y010：Sorter 3 belt。\n"
                   "运输延时分别0.8s、3.1s、5.5s，分拣输出保持1s。输送线上同时存在多件物料，连续出现同色物料。")
    context = build_direct_generation_context(requirement, "FX3U", model_profile={})
    assert context.generation_request == requirement
    addresses = {row['address'] for row in context.io_bindings}
    assert {'X1', 'X3', 'D0', 'Y0', 'Y2', 'Y3', 'Y4', 'Y5', 'Y6', 'Y7', 'Y10'} <= addresses
    evidence = context.handoff['generation_evidence']
    assert not evidence.get('structured_facts', {}).get('targets', {}).get('devices')
    assert all(row.get('instruction_opcode') not in {'PRUN', 'ROR', 'DRVI', 'DRVA'}
               and not row.get('source_section_id') for row in evidence.get('records', []))
    assert '"name":"STMR"' in context.knowledge_context
    assert 'capability:instruction:STMR' not in context.knowledge_context
    assert '[INSTRUCTION FACTS ' not in context.knowledge_context
    assert 'budget_omitted' not in json.dumps(evidence)


def test_selected_approach_does_not_reopen_functional_alternatives(monkeypatch):
    from application.capability_context import augment_generation_knowledge
    from knowledge.evidence import KnowledgeContext
    calls = []
    def discover(requirement, **kwargs):
        calls.append((requirement, kwargs))
        return {'version': 'test', 'functions': [], 'candidates': [], 'gaps': []}
    monkeypatch.setattr(knowledge_retriever, 'discover_capabilities', discover)
    spec = {'selected_approach': {'name': '已有事件记录', 'generation_guide': '保留逐件到达时刻'}}
    before = copy.deepcopy(spec)
    context = augment_generation_knowledge('考虑队列、移位、循环',
        KnowledgeContext('', {'structured_facts': {'targets': {'instructions': []}}}), confirmed_spec=spec)
    assert calls[0][0] == ''
    assert context.manifest['capability_discovery']['selection_scope'] == 'selected_approach'
    assert spec == before


@pytest.mark.parametrize('direct', [False, True])
def test_generation_wire_excludes_audit_values_without_mutating_backend(direct):
    from application.confirmed_generation_context import build_confirmed_generation_context, build_direct_generation_context
    from application.generation_agent import _compact_wire_renderer
    from knowledge.evidence import KnowledgeContext
    spec = {'plc_model': 'FX3U', 'summary': 'X0 controls Y0', 'parameters': [],
        'selected_approach': {'generation_guide': 'Keep the enable condition',
            'generation_contract': {'required_opcodes': ['MOV'], 'source_id': 'PRIVATE_SPEC_SENTINEL'}},
        'io_table': [{'address': 'X0', 'kind': 'X', 'label': 'Enable'}]}
    packet = {'content_origin': 'authored_fact', 'kind': 'instruction', 'target': 'MOV',
        'source_evidence': [{'id': 'PRIVATE_SOURCE_SENTINEL'}],
        'value': {'facts': [{'id': 'PRIVATE_FACT_SENTINEL', 'dimension': 'parameters.arity',
            'value': {'count': 2}, 'status': 'source_verified',
            'sources': [{'id': 'PRIVATE_SOURCE_SENTINEL'}]}],
            'test_results': ['PRIVATE_TEST_SENTINEL'], 'verification': 'PRIVATE_CHECK_SENTINEL'}}
    block = '[KNOWLEDGE {"id":"PRIVATE_RECORD_SENTINEL"}]\n' + json.dumps(packet) + '\n[/KNOWLEDGE]'
    manifest = {'records': [{'id': 'PRIVATE_RECORD_SENTINEL'}], 'test_results': ['PRIVATE_TEST_SENTINEL']}
    before = copy.deepcopy(spec)
    options = dict(knowledge_builder=lambda *a, **k: KnowledgeContext(block, manifest),
                   wire_renderer=_compact_wire_renderer('FX3U', direct=direct), model_profile={})
    context = (build_direct_generation_context('X0 controls Y0', 'FX3U', confirmed_spec=spec, **options) if direct else
               build_confirmed_generation_context(spec, 'FX3U', **options))
    wire = json.dumps(context.wire_packet)
    assert 'SENTINEL' not in wire
    assert 'knowledge_status' in wire and 'source_verified' in wire
    assert 'parameters.arity' in wire and 'MOV' in wire and 'X0' in wire
    assert 'PRIVATE_RECORD_SENTINEL' in json.dumps(context.handoff)
    assert spec == before


@pytest.mark.parametrize('value', [{'target': 'SFTL', 'value': {'source': 'S', 'count': 1024}},
    {'text': 'The shift count must not exceed the region length.'},
    {'interface': [{'name': 'EN', 'type': 'bit'}], 'st_signature': 'DUTY(EN, n1, n2, D)'}])
def test_technical_context_repeated_projection_is_stable(value):
    from shared.model_context import technical_block, technical_context_text
    block = technical_block(value)
    assert technical_context_text(block) == block
    assert technical_context_text(technical_context_text(block)) == block


def test_missing_retrieval_remains_visible_in_the_actual_model_message():
    from application.generation_agent import _compact_wire_renderer
    from knowledge.evidence import KnowledgeContext
    renderer = _compact_wire_renderer('FX3U')
    packet = renderer({}, KnowledgeContext('', {'status': 'unavailable', 'reason': 'retrieval_failed',
        'failure': {'path': 'PRIVATE_LOCAL_SOURCE'}}), 'ANDP ST', None, '', [])
    from application.generation_wire import expand_context_document
    payload = expand_context_document(json.loads(packet['messages'][1]['content']))
    assert payload['knowledge_coverage']['status'] == 'unavailable'
    assert 'PRIVATE_LOCAL_SOURCE' not in json.dumps(packet)


def test_context_deduplication_preserves_the_schema_reference_root():
    from application.generation_wire import structured_context_text, expand_context_document
    schema = {'type': 'object', '$defs': {'node': {'type': 'object',
              'description': 'complete schema declaration ' * 20,
              'properties': {'child': {'$ref': '#/$defs/node'}}}},
              'properties': {'root': {'$ref': '#/$defs/node'}}}
    payload = {'context_schema_version': 3, 'interface': {'parameter_schema': schema},
               'second_interface': {'parameter_schema': copy.deepcopy(schema)},
               'third_interface': {'model_schema': {'$ref': '#/objects/schema_local'}}}
    compact = json.loads(structured_context_text(payload))
    assert expand_context_document(compact) == payload
    assert compact['interface']['parameter_schema'] == schema


def test_source_data_type_is_not_a_named_instruction_target():
    from application.generation_support import _build_knowledge_query
    query = _build_knowledge_query({'labels': [{'name': 'count', 'data_type': 'INT'}],
                                    'nodes': [{'template': 'function:INT'}]})
    assert query.splitlines() == ['count', 'function:INT']


def test_core_construction_receives_confirmation_provenance_without_sending_it():
    from application.generation_agent import _compact_wire_renderer
    from application.context_compiler import ContextCompiler, ContextCompilerInput
    from plc.construction import compile_constructions
    spec = {'plc_model': 'FX3U', 'behavior_constraints': [{'id': 'boot', 'kind': 'initialize',
        'status': 'confirmed', 'provenance': {'source': 'user_confirmed', 'evidence': ['PRIVATE_USER_PROOF_SENTINEL']},
        'values': {'M10': False}, 'execution_context': {'program_type': 'scan'}}], 'selected_approach': {'construction_plan': {'instances': [
            {'id': 'boot_instance', 'requirement_id': 'boot', 'method': 'first_scan_isolation'}]}}}
    before = copy.deepcopy(spec)
    assert compile_constructions(spec)['instances']
    result = ContextCompiler().compile(ContextCompilerInput(confirmed_spec=spec, plc_model='FX3U',
        wire_renderer=_compact_wire_renderer('FX3U'), model_profile={}))
    wire = json.dumps(result.wire_packet)
    assert 'boot_instance' in wire and 'M10' in wire
    assert 'PRIVATE_USER_PROOF_SENTINEL' not in wire and 'provenance' not in wire
    assert spec == before


def test_incidental_mnemonics_do_not_expand_call_contracts():
    from application.capability_context import augment_generation_knowledge
    from knowledge.evidence import KnowledgeContext
    context = augment_generation_knowledge('', KnowledgeContext(
        'Reference example mentions MOV and DMOV; the requested fact is DUTY.',
        {'records': [{'id': 'clock', 'instruction_opcode': 'DUTY'}],
         'structured_facts': {'targets': {'instructions': [{'opcode': 'DUTY'}]}}}), plc_model='Q00J')
    assert context.manifest['capability_discovery']['included_call_contracts'] == ['call-contract:Q00J:DUTY']
    assert 'call-contract:Q00J:MOV' not in context
    assert 'call-contract:Q00J:DMOV' not in context


def _repair_candidate():
    return {"r": [{"h": None, "s": [], "b": [
        {"i": ["P D0", "NO M8002"], "o": ["SFTL M1 M100 K65 K1"]}]}]}


@pytest.mark.parametrize("embedded", [True, False])
def test_repair_retrieval_keeps_baseline_calls_and_explicit_new_facts(embedded):
    from application.confirmed_generation_context import build_direct_generation_context
    from application.compact_protocol import expand_compact_ladder
    candidate = _repair_candidate()
    before = copy.deepcopy(candidate)
    requirement = ("D0：分类结果。修复 P D0 非法触点；D0回零后登记。保留 SFTL，"
                   "输出窗口 OR。另查询 CMP 指令的结果位。\n")
    current = None
    if embedded:
        requirement += json.dumps(candidate)
    else:
        current = expand_compact_ladder(candidate)
    context = build_direct_generation_context(requirement, "FX3U", current_program=current)
    discovery = context.handoff["capability_discovery"]
    assert discovery["selection_scope"] == "repair_baseline"
    assert set(discovery["included_candidates"]) == {"instruction:SFTL", "instruction:CMP"}
    targets = context.handoff["structured_facts"]["targets"]["instructions"]
    assert {t["opcode"] for t in targets} == {"SFTL", "CMP"}
    assert 'ZRN' not in context.knowledge_context
    assert context.generation_request == requirement
    assert candidate == before


@pytest.mark.parametrize("requirement", [
    '修复程序：{"r": [',
    '修复程序：' + json.dumps(_repair_candidate()) + '\n' + json.dumps(_repair_candidate()),
    '解释示例：' + json.dumps(_repair_candidate()),
])
def test_ambiguous_or_missing_repair_baseline_does_not_pin_retrieval(requirement):
    from application.generation_support import repair_retrieval_scope
    assert repair_retrieval_scope(requirement) is None


def test_explicit_instruction_still_receives_complete_facts():
    from application.capability_context import augment_generation_knowledge
    from knowledge.evidence import KnowledgeContext
    context = augment_generation_knowledge('使用 STMR', KnowledgeContext('',
        {'structured_facts': {'targets': {'instructions': [{'opcode': 'STMR', 'base_opcode': 'STMR'}]}}}))
    discovery = context.manifest['capability_discovery']
    assert discovery['included_evidence']
    assert '[PLC KNOWLEDGE]' in context and 'parameters.arity' in context
    assert 'knowledge_status' in context


@pytest.mark.parametrize('reply', [
    {'status': 'needs_input', 'missing_info': [{'id': 'level', 'question': 'X0 按下时为 1 还是 0？'}]},
    {'status': 'needs_input', 'missing_info': [
        {'id': 'level', 'question': 'X0 按下时为 1 还是 0？', 'options': ['1', '0']},
        {'id': 'delay', 'question': '延时多少秒？'},
    ]},
    {'r': [{'h': None, 's': [], 'b': [{'i': ['NO X0'], 'o': ['COIL Y0']}]}]},
])
def test_direct_still_uses_one_request_without_spec_confirmation(monkeypatch, reply):
    from application.generation_agent import generate_direct_ladder
    from model_runtime.provider import TextDelta
    from model_profile_fixtures import offline_runtime_profile
    requests = []
    class Provider:
        profile = offline_runtime_profile()
        def stream(self, request):
            requests.append(request)
            yield TextDelta(json.dumps(reply, ensure_ascii=False))
    monkeypatch.setattr(knowledge_retriever, 'build_knowledge_context', lambda *a, **k: '')
    with api.provider_scope(Provider(), model_name='offline'):
        if reply.get('status') == 'needs_input':
            from application.compact_protocol import CompactProtocolError
            with pytest.raises(CompactProtocolError):
                generate_direct_ladder('X0 控制 Y0。', 'FX3U', model_name='offline', construction_examples=False)
        else:
            result = generate_direct_ladder('X0 控制 Y0。', 'FX3U', model_name='offline', construction_examples=False)
            assert result['ladder']['rungs'] and not result.get('missing_info')
            assert result['decisions'] == result['open_items'] == []
    assert len(requests) == 1
    assert requests[0].max_retries == 0
    prompt = requests[0].messages[0].content
    assert '未提取不等于未提供' in prompt
    assert '内部地址、指令、数据结构和实现方法由生成器自行决定' in prompt
    for obsolete in ('needs_input', '二者不能混合', '首次追问前完整检查', '真实缺参先问', 'Agent A'):
        assert obsolete not in prompt


@pytest.mark.parametrize('requirement', ['用 ST 计算 2.0 ** 3.0。', '用 ST 计算幂运算。', '用 EXPT 计算幂。',
                                        'Result := Base ** Exponent;'])
@pytest.mark.parametrize('confirmed', [False, True])
def test_st_numeric_rules_reach_first_model_request_without_an_extra_call(requirement, confirmed):
    from model_runtime.provider import TextDelta
    from model_profile_fixtures import offline_runtime_profile
    requests, handoffs = [], []
    class Provider:
        profile = offline_runtime_profile()
        def stream(self, request):
            requests.append(request)
            yield TextDelta(json.dumps({'st_code': 'D0 := 1;'}))
    with api.provider_scope(Provider(), model_name='offline'):
        _, content = api.stream_model_response(requirement, 'offline', None, 'st', plc_model='FX3U',
            confirmed_context={'plc_model': 'FX3U', 'summary': '计算一个数值。'} if confirmed else None,
            on_generation_context=handoffs.append)
    assert json.loads(content) == {'st_code': 'D0 := 1;'}
    assert len(requests) == 1 and requests[0].max_retries == 0
    prompt = '\n'.join(message.content for message in requests[0].messages)
    assert 'power_base_must_be_positive' in prompt
    assert 'power_result_outside_normal_REAL_range' in prompt
    assert 'call_available' in prompt and 'false' in prompt
    assert 'knowledge_status' in prompt
    assert 'depends_on' in prompt and '"id":"source:' not in prompt
    assert 'requires' in prompt
    assert handoffs[0]['generation_evidence']['status'] != 'excluded'


def test_unselected_option_does_not_trigger_detail_lookup(monkeypatch):
    from application.capability_context import augment_generation_knowledge
    from knowledge.evidence import KnowledgeContext
    import knowledge.instruction_facts as facts
    candidate = {'id': 'instruction:STMR', 'kind': 'instruction', 'name': 'STMR',
                 'family': 'STMR', 'purpose': 'Special Timer', 'interface': [],
                 'execution': 'continuous', 'unknown': ['process_applicability'],
                 'functions': ['timer'], 'sources': [{'id': 'timer-source'}]}
    monkeypatch.setattr(knowledge_retriever, 'discover_capabilities', lambda *a, **k:
        {'version': 'test', 'functions': ['timer'], 'candidates': [candidate], 'gaps': []})
    monkeypatch.setattr(facts, 'retrieve_instruction_facts', lambda *a, **k: pytest.fail('Unselected detail expansion'))
    context = augment_generation_knowledge('延时动作', KnowledgeContext('',
        {'structured_facts': {'targets': {'instructions': []}}}))
    assert context.manifest['capability_discovery']['included_candidates'] == ['instruction:STMR']
    assert 'timer-source' not in context and 'process_applicability' in context
    assert context.manifest['capability_discovery']['candidates'][0]['sources'][0]['id'] == 'timer-source'
    assert context.manifest['capability_discovery']['included_evidence'] == []


def test_query_projection_keeps_all_distinct_values_and_handles_shared_containers():
    from application.generation_support import _build_knowledge_query
    values = [f'fact-{i}' for i in range(500)]
    cyclic = [values]
    cyclic.append(cyclic)
    query = _build_knowledge_query(cyclic, values, char_limit=1)
    assert query.splitlines() == values
    assert not query.truncated


@pytest.mark.parametrize('cpu,device,hardware', [
    ('FX2N', 'M8028', {'cpu_version': '3.00'}),
    ('FX1S', 'D8021', {'input_voltage': '24V DC'}),
    ('FX1N', 'D8020', {'input_voltage': '24V DC'}),
    ('Q03UDE', 'SM605', {'serial_first5': 10102, 'memory_card': 'compatible'}),
    ('FX3S', 'D8030', {'hardware_variant': 'FX3S-20MR/ES'}),
    ('Q12PRH', 'SM1518', {'system_mode': 'redundant', 'program_execution': 'scan'}),
    ('L26-BT', 'SB0041', {'cc_link_source': 'built_in', 'station_role': 'master', 'sb_refresh_start': 'SB0'}),
])
def test_confirmed_device_conditions_survive_projection_and_reach_shared_retrieval(cpu,device,hardware):
    from application.confirmed_generation_context import build_confirmed_generation_context
    from plc.device_definitions import resolve_device_definition
    spec = {'plc_model':cpu, 'summary':'M0 enables the requested function', 'parameters':[],
            'io_table':[{'address':'M0','kind':'M','label':'Enable'}],
            'hardware_profile':{**hardware, 'source':'user_confirmed_spec',
                                'raw_response':'private provider content', 'invented_fact':'not confirmed'}}
    before = copy.deepcopy(spec)
    calls = []
    def knowledge(query, **kwargs):
        sent = query.metadata['hardware_context']
        calls.append(sent)
        assert all(sent.get(key) == value for key,value in hardware.items())
        assert 'raw_response' not in sent and 'invented_fact' not in sent
        definition = resolve_device_definition(device,cpu,context=sent)
        assert definition['status'] == 'source_verified'
        return json.dumps({'device':device,'status':definition['status']})
    context = build_confirmed_generation_context(spec,cpu,knowledge_builder=knowledge)
    assert len(calls) == 1 and spec == before
    assert all(context.confirmed_spec['hardware_profile'].get(key) == value for key,value in hardware.items())
    assert device in context.knowledge_context and 'source_verified' not in context.knowledge_context


@pytest.mark.parametrize("row", json.loads(
    (private_path('tests/fixtures/gxworks2_native_cpu_menu.json')).read_text(encoding="utf-8")
)["rows"], ids=lambda row: row["cpu"])
def test_every_observed_native_menu_and_member_retains_generation_identity(monkeypatch, row):
    monkeypatch.setattr(api, "load_full_config", lambda: pytest.fail("Native target must not fall back"))
    menu = row["cpu"]
    for cpu in {menu, *menu.removesuffix("(RemoteI/O)").split("/")}:
        assert api._resolve_plc_model("FX3U reference", explicit_model=cpu) == cpu
        assert api._detect_plc_model(f"目标 {cpu}。") == cpu


@pytest.mark.parametrize("model", [
    "FX1N", "Q00J", "Q03UDV", "L02", "FX3UC", "FX5U",
    "FX1N/FX1NC", "Q02/Q02H", "L02/L02-P", "QJ72LP25/QJ72BR15(RemoteI/O)",
])
def test_selected_cpu_does_not_depend_on_legacy_prompt_profile(monkeypatch, model):
    monkeypatch.setattr(api, "load_full_config", lambda: pytest.fail("Selected CPU must not fall back to config"))
    assert api._resolve_plc_model("FX3U reference", explicit_model=model.lower()) == model
    assert api._resolve_plc_model("FX3U reference", {"plc_model": model}) == model
    assert api._detect_plc_model(f"为 {model} 编程") == model


@pytest.mark.parametrize("selection", ["explicit", "confirmed", "config"])
def test_unknown_selected_cpu_is_rejected_instead_of_defaulted(monkeypatch, selection):
    monkeypatch.setattr(api, "load_full_config", lambda: {"plc_model": "Q_UNKNOWN" if selection == "config" else "FX3U"})
    with pytest.raises(ValueError, match="Unknown PLC target"):
        api._resolve_plc_model(
            "program", {"plc_model": "Q_UNKNOWN"} if selection == "confirmed" else None,
            "Q_UNKNOWN" if selection == "explicit" else None,
        )


def test_cpu_text_detection_respects_identity_boundaries_and_config(monkeypatch):
    monkeypatch.setattr(api, "load_full_config", lambda: {"plc_model": "L02"})
    assert api._detect_plc_model("MY_FX3U_LIBRARY Q03UDVX") == "L02"
    assert api._resolve_plc_model("FX3U", {"plc_model": "FX5U"}, "Q00J") == "Q00J"


@pytest.mark.parametrize("model", ["FX1N", "Q00J", "Q03UDV", "L02", "FX3UC"])
def test_legacy_st_and_motion_context_do_not_substitute_fx3u(model):
    from knowledge.patterns import _assemble_st_prompt
    from application.generation_context import _specialist_context
    prompt = _assemble_st_prompt(model)
    assert model in prompt and "M8340" not in prompt and "FX3U / GX Works2" not in prompt
    classification = {"workflow_route": {"knowledge_bundles": ["motion_control", "motion_control_fx5u"]}}
    assert _specialist_context(classification, target_mode="ladder", plc_model=model) == ""


@pytest.mark.parametrize("model", ["FX1N", "Q00J", "Q03UDV", "L02"])
@pytest.mark.parametrize("language", ["ladder", "st"])
def test_new_cpu_reaches_api_and_tool_retrieval_without_fx3u_profile(monkeypatch, model, language):
    calls = []
    def retrieve(query, **kwargs):
        calls.append(kwargs["plc_model"])
        return f"Official manual evidence scoped to {kwargs['plc_model']}."
    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", retrieve)
    monkeypatch.setattr(api, "load_full_config", lambda: pytest.fail("Bound CPU must not read global config"))
    requirement = "X0 controls Y0."
    messages, _, _ = api._prepare_api_call(requirement, "offline", "medium", language, plc_model=model)
    context = build_tool_context({"id": "new-cpu", "plc_model": model, "target_mode": language})
    result = build_default_tool_registry().call("get_generation_context", {
        "user_requirement": requirement, "workflow": "detailed",
    }, context)
    assert result["ok"], result
    assert calls and set(calls) == {model}
    for prompt in (json.loads(messages[1]["content"])["target_profile"], result["data"]["generation_input"]["target_profile"]):
        assert f'"model": "{model}"' in prompt
        assert "profile_status" not in prompt
        assert '"model": "FX3U"' not in prompt
        assert '"device_validation"' not in prompt
        assert "M8340" not in prompt and "M8029=" not in prompt
    if language == "st":
        assert f"# Selected PLC\n{model}" in messages[0]["content"]
        contract = result['data']['output_contract']
        assert contract['format'] == 'st_body_json'
        assert contract['schema_ref'] == '#/generation_input/output_schema'
        assert result['data']['generation_input']['output_schema']['required'] == ['st_code']
        assert 'rungs' not in result['data']['generation_input']['output_schema']['properties']
        assert 'create_program_candidate' not in str(contract)


@pytest.mark.parametrize('language', ['ladder', 'st'])
def test_source_call_contract_is_carried_with_discovery_when_detail_lookup_is_not_packed(language):
    import json
    from application.capability_context import augment_generation_knowledge
    knowledge = augment_generation_knowledge(
        'CPU Q00J: register a scan clock at SM420 with one scan ON and three scans OFF, enabled by X0. '
        'The CPU updates the clock during END processing; disabling X0 must not reset it.',
        plc_model='Q00J', target_mode=language)
    discovery = knowledge.manifest['capability_discovery']
    candidate = next(row for row in discovery['candidates'] if row['name'] == 'DUTY')
    source = candidate['target_call']
    assert source['target_model'] == 'Q00J'
    assert source['st_call'] == {'name': 'DUTY', 'enable': True, 'returns': 'bit'}
    assert source['st_signature'] == 'DUTY(EN, n1, n2, D)'
    assert source['ladder_signature'] == 'DUTY n1 n2 D'
    assert 'enclosing IF does not replace EN' in source['enable_rule']
    assert 'ON scans' in source['source_notes'] and 'OFF scans' in source['source_notes']
    from shared.model_context import technical_value
    assert json.dumps(technical_value(source), ensure_ascii=False, separators=(',', ':'), sort_keys=True) in knowledge
    assert 'contract_sources' not in knowledge
    assert candidate['interface'][2]['allowed_devices'] == ['SM420','SM421','SM422','SM423','SM424','SM430','SM431','SM432','SM433','SM434']


def test_composition_is_opt_in_and_shared_between_fbd_api_and_mcp():
    from application.composition_context import CompositionExperiment, composition_scope, composition_experiment
    from application.fbd import build_fbd_generation_context, catalog_snapshot
    from gxw.object_model import default_baseline
    requirement = "使用TON实现延时控制"
    catalog = catalog_snapshot(default_baseline())
    with composition_scope(CompositionExperiment("D", input_version=(("version_id", "v1"),))):
        direct = build_fbd_generation_context(requirement, plc_model="FX3U", catalog=catalog)
        context = build_tool_context({"id": "composed-fbd", "plc_model": "FX3U", "target_mode": "fbd"})
        result = build_default_tool_registry().call("get_generation_context", {"user_requirement": requirement}, context)
        assert result["ok"], result
        assert result["data"]["generation_instructions"] == direct["generation_instructions"]
        assert "generation_handoff" not in result["data"]
        assert result["data"]["generation_input"]["knowledge"] == direct["generation_input"]["knowledge"]
        assert direct["generation_handoff"]["capability_discovery"]["experiment_arm"] == "D"
    assert composition_experiment().arm == "A"


def test_public_composition_receipts_never_expose_omitted_units():
    from application.projects import public
    from agent_runtime.runtime import _public_value
    receipt = {"capability_discovery": {"included_composition_units": ["one"],
        "composition_units": [{"id": "one", "value": {"confirmation": "candidate"}},
                              {"id": "two", "value": {"confirmation": "candidate"}}],
        "omitted_composition_units": [{"id": "two", "reason": "remaining_evidence_budget"}]}}
    assert public(receipt) == _public_value(receipt)
    assert [r["id"] for r in public(receipt)["capability_discovery"]["composition_units"]] == ["one"]


def test_fbd_api_and_mcp_expose_identical_directory_discovery_and_selection():
    from application.fbd import build_fbd_generation_context, catalog_snapshot
    from gxw.object_model import default_baseline
    from plc.maintainability import SELECTION_POLICY
    raw = "使用TON实现延时控制，保留既有目录接口。"
    catalog = catalog_snapshot(default_baseline())
    api_context = build_fbd_generation_context(raw, plc_model="FX3U", catalog=catalog)
    context = build_tool_context({"id": "fbd-context", "plc_model": "FX3U", "target_mode": "fbd"})
    registry = build_default_tool_registry()
    result = registry.call("get_generation_context", {"user_requirement": raw}, context)
    assert result["ok"], result
    data = result["data"]
    assert data["generation_instructions"] == api_context["generation_instructions"]
    assert data["generation_input"]["catalog"] == api_context["generation_input"]["catalog"]
    assert "catalog" not in data
    assert data["generation_input"]["previous"] == api_context["generation_input"]["previous"]
    assert "current_model" not in data
    assert "generation_handoff" not in data
    assert data["generation_input"]["knowledge"] == api_context["generation_input"]["knowledge"]
    assert "[PLC KNOWLEDGE]" not in data["generation_instructions"]
    assert data["generation_context_id"]
    assert data["confirmed_spec"] is None


def test_fbd_saved_engineering_input_is_identical_for_web_and_mcp():
    from application.fbd import build_fbd_generation_context, catalog_snapshot
    from gxw.object_model import read_project_context
    from gxw.offline_project import read_offline_project
    from tests.test_gxw_offline_authoring import native_project
    raw = native_project()
    selected = 'POU_01.Program.pou'
    catalog = catalog_snapshot(raw, selected)
    comments, tables, _ = read_offline_project(raw).comments()
    request = 'Change ScaleValue and preserve all callers'
    direct = build_fbd_generation_context(request, plc_model='FX3U', catalog=catalog,
        previous=catalog['parameter_model'], instances_model=read_project_context(raw, selected).object_model(),
        source_baseline=raw, device_comments=comments, comment_tables=tables)
    context = build_tool_context({'id':'fbd-parity', 'target_mode':'fbd', 'plc_model':'FX3U'},
        version={'id':'v1', 'program_name':selected, 'target_mode':'fbd'}, fbd_baseline=raw, native_snapshot=raw)
    result = build_default_tool_registry().call('get_generation_context', {'user_requirement':request}, context)
    assert result['ok'], result
    assert result['data']['generation_input'] == direct['generation_input']
    payload = direct['generation_input']
    assert payload['source_context']['st_sources']
    assert payload['source_context']['fbd_callers'][0]['source_checks']['status']
    assert payload['task_scopes'] and payload['project_options'] and payload['call_bindings']


def test_fbd_context_preserves_every_call_of_a_shared_instance():
    from application.fbd_context import call_bindings
    catalog = {'nodes':[{'template':'function_block:TON','ports':[{'name':'IN','data_type':'BOOL','side':'in'}]}]}
    model = {'networks':[{'calls':[
        {'id':'one','type':'TON','instance':'Shared','arguments':{'IN':'M0'}},
        {'id':'two','type':'TON','instance':'Shared','arguments':{'IN':'M1'}}]}]}
    rows = call_bindings(model, catalog)
    assert [row['id'] for row in rows] == ['one','two']
    assert [row['arguments'][0]['value'] for row in rows] == ['M0','M1']


def test_fbd_context_keeps_whole_nested_array_types_and_explicit_missing_dependencies():
    from application.fbd_context import select_task_catalog
    root = [{'name':'nested','data_type':'ARRAY[1..3] OF Inner'}]
    inner = [{'name':'member'+str(i),'data_type':'INT'} for i in range(300)]
    catalog = {'nodes':[{'template':'function_block:Work','ports':[
        {'name':'value','data_type':'ARRAY[0..4] OF Root'}, {'name':'missing','data_type':'Absent'}]}],
        'structures':{'Root':root,'Inner':inner}}
    selected = select_task_catalog(catalog, templates=['function_block:Work'])
    assert selected['structured_types'] == {'Root':root,'Inner':inner}
    assert selected['structure_gaps'] == ['Absent']


def test_fbd_context_does_not_mistake_an_fb_type_for_a_missing_structure():
    from application.fbd_context import select_task_catalog
    catalog = {'nodes': [
        {'template': 'function_block:Work', 'ports': [{'name': 'value', 'data_type': 'Root'}]},
        {'template': 'function_block:TON', 'ports': [{'name': 'IN', 'data_type': 'BOOL'}]}],
        'structures': {'Root': [{'name': 'timer', 'data_type': 'ARRAY[0..2] OF TON'}]}}
    result = select_task_catalog(catalog, request='Work')
    assert result['structure_gaps'] == []
    assert 'function_block:Work' not in result['optional_templates']
    assert 'function_block:TON' not in result['optional_templates']


def test_fbd_context_budget_removes_auxiliary_interfaces_without_cutting_required_members():
    from application.fbd_context import compose_fbd_context, FBDContextBudgetError
    ports = [{'name': 'Pin' + str(i), 'data_type': 'BOOL', 'side': 'in'} for i in range(8000)]
    catalog = {'program': 'MAIN', 'nodes': [
        {'template': 'function:Work', 'ports': [{'name': 'value', 'data_type': 'Root'}]},
        {'template': 'function:MOV', 'ports': ports}],
        'structures': {'Root': [{'name': 'member' + str(i), 'data_type': 'INT'} for i in range(300)]}}
    result = compose_fbd_context(request='Work', plc_model='FX3U', catalog=catalog,
                                 model_profile={'context_window': 48000})
    assert [row['template'] for row in result.payload['catalog']] == ['function:Work']
    assert result.payload['structures']['Root'] == catalog['structures']['Root']
    assert result.budget_report['fits'] is True
    assert any(row['id'] == 'catalog.function:MOV' for row in result.omitted_units)
    # Explicitly requesting that same interface makes every pin mandatory.
    with pytest.raises(FBDContextBudgetError):
        compose_fbd_context(request='Work MOV', plc_model='FX3U', catalog=catalog,
                            model_profile={'context_window': 48000})


def test_fbd_final_tool_envelope_rechecks_the_budget_after_adding_schemas():
    from application.fbd_context import compile_envelope
    required = {'template': 'function:Work', 'ports': [{'name': 'value', 'data_type': 'INT'}]}
    alternative = {'template': 'function:Alternative', 'ports': [{'name': 'Pin', 'data_type': 'BOOL'}] * 16000}
    envelope = {'context': {'generation_input': {'catalog': [required, alternative],
                'source_context': {'st_sources': [{'st_text': 'Value := 1;'}]}}}, 'tool_schema': {'type': 'object'}}
    saved, report = compile_envelope(envelope, instructions='Use exact supplied interfaces',
        model_profile={'context_window': 24000}, optional_templates=['function:Alternative'])
    assert report['fits'] is True
    assert saved['context']['generation_input']['catalog'] == [required]
    assert saved['context']['generation_input']['source_context']['st_sources'][0]['st_text'] == 'Value := 1;'


def test_fbd_context_uses_bound_nested_calls_and_all_reverse_fb_callers(monkeypatch):
    from types import SimpleNamespace
    from application.fbd_context import source_program_context
    import gxw.fb_authoring as authoring
    import gxw.st_program as st_program
    import gxw.object_model as object_model
    import gxw.ladder_lowering as lowering
    from gxw.models import GXWFormatError
    rows = [dict(name=name, program=name+'.pou', labels=name+'.lh', kind=kind, language=language,
                 source_status='available', declarations=[], st_text=body) for name,kind,language,body in [
        ('Top','program','fbd',None), ('Outer','function_block','st','InnerInstance();'),
        ('Inner','function_block','st','Value := 0;'),
        ('OtherCaller','function_block','st','PrivateInstance();'),
        ('Unrelated','program','st','Value := 1;')]]
    rows[0]['model'] = {'nodes':[{'id':'outer','template':'function_block:Outer','symbol':'OuterInstance'}]}
    groups = {row['program']:{'labels':row['labels']} for row in rows}
    class Sources:
        program_name = 'Top.pou'
        source_groups = groups
        def label(self,name):
            if (self.program_name,name.casefold()) in [('Outer.pou','innerinstance'),('OtherCaller.pou','privateinstance')]:
                return self.program_name.replace('.pou','.lh'), SimpleNamespace(data_type='Inner')
            return None
        def definition(self,name,kind):
            if name not in ('Outer','Inner'):
                raise GXWFormatError('unknown')
            return name+'.lh', {'source_program':name+'.pou'}
    snapshot = dict(pous=rows, cpu='FX3U', selected_source='Top.pou', structures={})
    monkeypatch.setattr(st_program,'project_context_snapshot',lambda *a,**k:snapshot)
    monkeypatch.setattr(authoring,'_context',lambda *a,**k:({}, {}, groups, Sources(), 'Top.pou'))
    monkeypatch.setattr(object_model,'read_project_context',lambda *a,**k:object())
    monkeypatch.setattr(object_model,'source_checks',lambda *a,**k:dict(status='passed',findings=[],execution={}))
    monkeypatch.setattr(lowering,'fbd_topology_from_context',lambda *a,**k:dict(networks=[]))
    result = source_program_context(b'context',selected_program='Top.pou')
    assert {row['name'] for row in result['st_sources']} == {'Outer','Inner','OtherCaller'}
    assert result['transaction']['affected_sources'] == ['Inner.pou','OtherCaller.pou','Outer.pou','Top.pou']
    assert [row['source_name'] for row in result['omitted_st_sources']] == ['Unrelated.pou']


def test_new_receipt_projection_is_identical_for_web_and_mcp_without_retrieval_replay():
    from application.projects import public
    from agent_runtime.runtime import _public_value
    receipt = {"capability_discovery": {"functions": ["copy"], "queries": ["private diagnostic query"],
        "candidates": [{"id": "delivered", "name": "COPY", "interface": [{"name": "src"}], "sources": []},
                       {"id": "omitted", "name": "alternative"}],
        "included_candidates": ["delivered"], "omitted_candidates": ["omitted"],
        "tokens": {"basis": "deterministic_heuristic_estimate", "net_delta": 100}}}
    assert public(receipt) == _public_value(receipt)
    result = public(receipt)["capability_discovery"]
    assert result["tokens"]["net_delta"] == 100
    assert "queries" not in result
    assert [c["id"] for c in result["candidates"]] == ["delivered"]
    assert "interface" not in result["candidates"][0]


def test_raw_direct_api_mcp_share_context_and_keep_user_facts(monkeypatch):
    from application.confirmed_generation_context import build_direct_generation_context
    from application.generation_agent import _compact_wire_renderer
    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", lambda *a, **k: "")
    raw = "X0：启动按钮，按下为1。Y0：输出。按住启动按钮时输出，不自保持。"
    direct = build_direct_generation_context(raw, "FX3U", wire_renderer=_compact_wire_renderer("FX3U", direct=True))
    registry = build_default_tool_registry()
    context = build_tool_context({"id": "raw-direct", "plc_model": "FX3U"})
    result = registry.call("get_generation_context", {"user_requirement": raw}, context)
    assert result["ok"], result
    data = result["data"]
    assert data["workflow_mode"] == "direct_generation"
    assert data["generation_request"] == raw
    assert data["generation_instructions"] == direct.wire_packet["messages"][0]["content"]
    assert data["confirmed_spec_ref"] == "#/generation_input/confirmed_spec"
    assert data["generation_input"]["confirmed_spec"]["io_bindings"] == direct.io_bindings
    submitted = registry.call("create_program_candidate", {"generation_context_id": data["generation_context_id"],
        "ladder": {"r": [{"b": [{"i": ["NO X0"], "o": ["COIL Y0"]}]}]}}, context)
    assert submitted["ok"], submitted
    assert submitted["data"]["requires_confirmation"]
    assert submitted["data"]["generation_handoff"]["mode"] == "direct_generation"


@pytest.mark.parametrize("direct", [False, True])
def test_compact_paths_deliver_core_scan_timing_guidance_without_examples(monkeypatch, direct):
    from application.confirmed_generation_context import (
        build_confirmed_generation_context, build_direct_generation_context,
    )
    from application.construction_examples import prepare_construction_examples
    from application.generation_agent import _compact_wire_renderer
    from plc.timing import scan_timing_guidance
    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", lambda *a, **k: "")
    raw = "FX3U，X0打开后以本次启动为起点，每秒更新显示，关闭复位。"
    renderer = _compact_wire_renderer("FX3U", direct=direct,
        example_block=prepare_construction_examples("FX3U", False))
    if direct:
        context = build_direct_generation_context(raw, "FX3U", wire_renderer=renderer)
    else:
        context = build_confirmed_generation_context({"summary": raw}, "FX3U", wire_renderer=renderer)
    delivered = context.wire_packet["messages"][0]["content"]
    assert scan_timing_guidance("FX3U") in delivered
    assert context.handoff["generation_execution_policy"] == ("direct-program-v1" if direct else "settled-facts-v8-native-timing")
    assert "## PLC execution semantics" not in delivered


def _ladder():
    return {
        "device_comments": {"X0": "Start", "X3": "Permit", "Y0": "Motor"},
        "rungs": [{"rung_id": 1, "header_element": None,
                   "shared_inputs": [{"type": "NO", "address": "X3"}],
                   "branches": [{"branch_id": 1, "y_offset_level": 0,
                                 "inputs": [{"type": "parallel_block", "branches": [
                                     [{"type": "NO", "address": "X0"}],
                                     [{"type": "NO", "address": "Y0"}],
                                 ]}],
                                 "outputs": [{"type": "COIL", "address": "Y0"}]}]}],
    }


@pytest.mark.parametrize("editing", [False, True])
def test_api_and_external_context_have_identical_generation_instructions(monkeypatch, editing):
    calls = []

    def retrieve(query, **kwargs):
        calls.append((query, kwargs))
        return "Official timer evidence: T0 to T199 use the documented time base."

    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", retrieve)
    monkeypatch.setattr(api, "load_full_config", lambda: pytest.fail("Bound context must not read global config"))
    monkeypatch.setattr(api, "get_active_provider", lambda: pytest.fail("No model call is allowed"))
    requirement = "Use FX3U T1 with a timer preset K30; X0 starts Y0 and X3 permits running."
    spec = {"summary": "Confirmed timer requirement", "io_table": [
        {"address": "X0", "label": "Start"}, {"address": "X3", "label": "Permit"},
        {"address": "Y0", "label": "Motor"}], "parameters": [{"name": "delay", "value": "K30"}]}
    ladder = _ladder() if editing else None
    context = build_tool_context(
        {"id": "shared-context", "plc_model": "FX3U", "confirmed_spec": spec},
        version={"id": "v0001", "plc_model": "FX3U", "confirmed_spec_snapshot": spec} if editing else None,
        ladder=ladder,
    )
    model_request = generation_user_input(requirement, is_edit_mode=editing)
    messages, history, persist = api._prepare_api_call(
        model_request, "offline", "high", "ladder", plc_model="FX3U",
        is_edit_mode=editing, confirmed_spec=spec, current_version_json=ladder,
        conversation_history=[{"role": "assistant", "content": "Private old conversation"}],
    )
    api_calls = copy.deepcopy(calls)
    calls.clear()
    result = build_default_tool_registry().call("get_generation_context", {"user_requirement": requirement}, context)
    assert result["ok"], result
    data = result["data"]
    assert data["generation_instructions"] == messages[0]["content"]
    assert data["generation_request"] == (model_request if editing else CONFIRMED_GENERATION_REQUEST)
    assert calls == api_calls
    for _query, options in calls:
        assert options["plc_model"] == "FX3U"
        assert options["task_type"] == ("edit" if editing else "generate")
        assert options["top_k"] == 5
        assert "token_budget" not in options
        assert "char_budget" not in options
    assert data["current_version_id"] == ("v0001" if editing else None)
    assert "Private old conversation" not in json.dumps(data)
    assert persist is False and len(history) == (2 if editing else 1)
    if not editing:
        assert "Private old conversation" not in json.dumps(messages + history)
        assert history == [{"role": "user", "content": CONFIRMED_GENERATION_REQUEST}]
    assert context.ladder == ladder


@pytest.mark.parametrize("model,target_mode", [("FX3U", "ladder"), ("FX5U", "ladder"), ("FX5U", "st")])
def test_empty_arguments_use_bound_model_and_api_retrieval_failure_fallback(monkeypatch, model, target_mode, capsys):
    def unavailable(*args, **kwargs):
        raise RuntimeError("C:/private/index.sqlite api_key=must-not-be-disclosed")

    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", unavailable)
    context = build_tool_context({"id": "empty", "plc_model": model, "target_mode": target_mode})
    result = build_default_tool_registry().call("get_generation_context", {}, context)
    assert result["ok"]
    data = result["data"]
    assert data["plc_model"] == model
    assert data["generation_input"]["confirmed_spec"] is None and data["has_confirmed_spec"] is False
    assert data["current_version_id"] is None
    assert '"special_m"' in data["generation_input"]["target_profile"]
    assert "retrieved for the current request" not in data["generation_instructions"]
    assert "private/index" not in json.dumps(result)
    assert "must-not-be-disclosed" not in json.dumps(result)
    assert "must-not-be-disclosed" not in capsys.readouterr().err
    if model == "FX5U":
        assert '"model": "FX5U"' in data["generation_input"]["target_profile"]


def test_public_context_projects_current_ladder_and_cleans_annotation_paths_and_credentials(monkeypatch):
    secret = "do-not-copy-private-state"
    private_path = "C:/" + "Users/private-user/project/program.json"
    spec = {"summary": "Motor", "user_notes": "See " + private_path,
            "api_key": secret, "history": [{"content": secret}],
            "hardware_context": {"notes": "agent_token=hidden-value", "provider": secret}}
    ladder = _ladder()
    ladder.update(api_key=secret, messages=[{"content": secret}], source={"path": private_path})
    ladder["rungs"][0]["ui_state"] = {"token": secret}
    ladder["rungs"][0]["branches"][0]["inputs"][0]["private_path"] = private_path
    ladder["device_comments"]["Y0"] = "Motor " + private_path
    ladder["device_comments"]["api_key"] = secret
    context = build_tool_context({"id": "private", "plc_model": "FX3U", "confirmed_spec": spec,
                                  "workspace": private_path, "credentials": {"key": secret},
                                  "messages": [{"content": secret}]}, ladder=ladder)
    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", lambda *args, **kwargs:
                        "Official documentation https://example.test/manual.pdf; file " + private_path)
    result = build_default_tool_registry().call("get_generation_context", {
        "user_requirement": "X0 starts Y0. api_key=client-secret Bearer private-bearer",
    }, context)
    assert result["ok"], result
    wire = json.dumps(result, ensure_ascii=False)
    for private in (secret, private_path, "hidden-value", "client-secret", "private-bearer", "private-user"):
        assert private not in wire
    assert "https://example.test/manual.pdf" in wire
    assert "[private path]" in wire and "[private credential]" in wire
    assert '"ui_state"' not in wire and '"messages"' not in wire
    assert "X3" in wire and "parallel_block" in wire
    assert context.project["confirmed_spec"]["api_key"] == secret


def test_source_projection_retains_real_branch_conditions_and_output_operands():
    ladder = _ladder()
    ladder["rungs"][0]["branches"][0]["outputs"] += [
        {"type": "TIMER", "address": "T1", "value": "K30"},
        {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K1", "D0"]},
    ]
    assert public_generation_ladder(ladder) == ladder


@pytest.mark.parametrize("arguments", [{"user_requirement": 1}, {"user_requirement": "a" * 64001},
                                        {"plc_model": "FX5U"}, {"confirmed_spec": {}}])
def test_optional_requirement_does_not_open_server_owned_context(arguments):
    result = build_default_tool_registry().call("get_generation_context", arguments,
                                               build_tool_context({"id": "bound", "plc_model": "FX3U"}))
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENTS"


def test_context_can_run_in_a_process_with_provider_config_and_desktop_imports_blocked(tmp_path):
    script = "import importlib.abc, json, sys, types\nclass BlockPrivate(importlib.abc.MetaPathFinder):\n    def find_spec(self, fullname, path=None, target=None):\n        if fullname.split('.')[0] in {'api', 'model_provider', 'openai', 'config_manager', 'credential_store',\n                                     'PyQt5', 'PyQt6', 'qt_compat', 'main', 'pywinauto', 'win32com'}:\n            raise AssertionError('Unexpected dependency: ' + fullname)\nsys.meta_path.insert(0, BlockPrivate())\nsys.modules['knowledge.retriever'] = types.SimpleNamespace(build_knowledge_context=lambda *a, **k: '')\nfrom agent_runtime.plc_tools import build_default_tool_registry, build_tool_context\nresult = build_default_tool_registry().call('get_generation_context', {'user_requirement': 'FX3U T1 K30'},\n    build_tool_context({'id': 'isolated', 'plc_model': 'FX3U'}))\nassert result['ok'], result\nassert result['data']['context_schema_version'] == 3\nprint(json.dumps({'ok': True}))\n"
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                   "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"ok": True}
@pytest.mark.parametrize('language', ['ladder','st'])
def test_model_requirement_hardware_evidence_is_not_starved_by_ordinary_device_mentions(language):
    from application.generation_context import _build_knowledge_context
    from knowledge.evidence import KnowledgeQuery
    requirement = '内置以太网连接1已配置；M0与打开完成状态同时成立且M1为FALSE时M20为TRUE，其余为FALSE。'
    query = KnowledgeQuery(requirement,precompiled=True,
        metadata={'target_mode':language,'capability_requirement':requirement,'rag_evidence_token_budget':3000})
    result = _build_knowledge_context(query,plc_model='L02')
    assert '[PLC KNOWLEDGE]' in result
    assert 'SD1282' in result and '"bit_zero_connection":1' in result
    assert any(row.get('device_definition_status')=='source_verified' for row in result.manifest['records'])


@pytest.mark.parametrize('model,requirement,hardware,device,status', [
    ('FX2C','M0时将 PLC 类型和系统版本原始字值复制至D100，撤销时保持',{},'D8001','source_verified'),
    ('FX2C','Read the system version with MOV when M1 is on',{},'D8001','source_verified'),
    ('FX1N','输入滤波 M0 控制设置',{'input_voltage':'24V DC'},'D8020','source_verified'),
    ('FX3S','Read first potentiometer',{'cpu_full_model':'FX3S-20MR/ES'},'D8030','source_verified'),
    ('FX3S','Read first potentiometer',{},'D8030','conditions_missing'),
])
def test_feature_definitions_deliver_cpu_conditions_and_source_together(model,requirement,hardware,device,status):
    from application.generation_context import _build_knowledge_context
    from knowledge.evidence import KnowledgeQuery
    query=KnowledgeQuery(requirement,precompiled=True,metadata={'capability_requirement':requirement,
        'target_mode':'ladder','rag_evidence_token_budget':3000,'hardware_context':hardware})
    context=_build_knowledge_context(query,plc_model=model,confirmed_context={'hardware_profile':hardware})
    assert device in context and 'PDF pages' not in context and '"status":"source_verified"' not in context
    assert any(row.get('device_definition_status') == status for row in context.manifest['records'])
    assert context.manifest['used_tokens'] > 0 and 'token_budget' not in context.manifest


@pytest.mark.parametrize('language',['ladder','st'])
@pytest.mark.parametrize('cpu,summary,hardware,device',[
    ('FX2C','M0为真时将PLC类型与系统版本的原始字值复制至D100，撤销时保持',{},'D8001'),
    ('FX1N','M0控制输入滤波设置，不将输入滤波的作用范围当成执行条件',{'input_voltage':'24V DC'},'D8020'),
    ('Q03UDE','M20等于M0与内置以太网连接1打开完成状态且M1为FALSE',{},'SD1282'),
])
def test_public_generation_tool_uses_confirmed_function_when_request_is_omitted(language,cpu,summary,hardware,device):
    from agent_runtime.plc_tools import build_tool_context
    context=build_tool_context({'id':'feature-query','plc_model':cpu,'target_mode':language,
        'confirmed_spec':{'summary':summary,'io_table':[{'address':'M0','kind':'M'}],
                          'hardware_profile':hardware,'selected_approach':{}}})
    result=build_default_tool_registry().call('get_generation_context',{},context)
    assert result['ok'],result
    text=json.dumps(result['data']['generation_input']['knowledge'])
    assert device in text
    assert 'PDF pages' not in text
