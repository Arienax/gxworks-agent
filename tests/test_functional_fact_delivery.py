"""Natural requirements reach the final model with source-scoped instruction semantics."""
import copy
import json

import pytest

from application.generation_wire import expand_context_document


ORIGINAL_REQUIREMENT = """#三色物料自动分拣控制
某生产线采用 PLC 控制物料输送与分拣。视觉系统识别物料颜色，将结果写入DO。物料沿主输送线
依次经过三个分拣站，分别进入蓝色、绿色和灰色支线。每个分拣站由转向机构和分拣皮带组成。
请根据以下IO分配及控制要求，直接生成三菱 FX3U PLC 梯形图程序。
一、1O分配表
「地址|功能║
##
比t
|×1|启动按钮，常开|
|x3|停止信号，常闭
|DO|视觉识别结果|
IYo|入口输送带|
1Y2|出口输送带|
|Y3|蓝色分拣站转向机构|
IY4| 蓝色分拣站皮带|
1Y5|绿色分拣站转向机构|
|Y6|绿色分拣站皮带|
1Y71灰色分拣站转向机构|
IY10|灰色分拣站皮带|
## 二、控制要求
1. 按下启动按钮X1，入口输送带Y0、出口输送带 Y2开始运行，松开按钮后保持运行。
2.视觉识别结果规定：D0=0表示无物料，1~3表示蓝色，4~6表示绿色，7~9表示灰色。物料经过视觉检测区有0-数字-0的过程；。
3. 以物料被识别的时刻为起点，按下表控制分拣：
|物料颜色|到站延时|动作输出|动作时间|
----1
|蓝色|0.6s|Y3、Y4同时开启|约1s|
|绿色|2.6s| Y5、Y6同时开启|约1s|
灰色|4.7s|Y7、Y10同时开启|约1s|
4.允许多件物料连续进入生产线，应分别完成追踪和分拣。各站转向机构与对应皮带始终同步动作，同色物料连续经过同一分拣站时分拣站机构不复位
作。
5按下停止按钮，生产线动作立刻停止，再按下启动恢复
"""


@pytest.fixture(scope='module', params=['summary', 'original'])
def sorting_context(request):
    from application.confirmed_generation_context import build_direct_generation_context
    from application.generation_agent import _compact_wire_renderer
    requirement = ('FX3U 梯形图：允许多件物料连续进入输送线，应分别完成追踪和分拣。'
                   'D0 从0变为颜色编号再回零；蓝色0.6s、绿色2.6s、灰色4.7s到站，'
                   '各站动作1s，同色连续物料的动作窗口重叠时保持输出。停止后冻结追踪，启动恢复。')
    if request.param == 'original':
        requirement = ORIGINAL_REQUIREMENT
    return build_direct_generation_context(requirement, 'FX3U', model_profile={},
        wire_renderer=_compact_wire_renderer('FX3U', direct=True))


def wire_payload(context):
    return expand_context_document(json.loads(context.wire_packet['messages'][1]['content']))


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from dictionaries(item)
    elif isinstance(value, list):
        for item in value:
            yield from dictionaries(item)


def test_natural_requirement_delivers_shift_parameters_and_state_update(sorting_context):
    payload = wire_payload(sorting_context)
    facts = [unit for unit in payload['knowledge'] if unit.get('kind') == 'instruction'
             and unit.get('target') == 'SFTL']
    assert facts
    assert all(unit['scope']['models'] == ['FX3U'] for unit in facts)
    assert all(unit['context']['language'] == 'ladder' for unit in facts)
    nodes = [node for fact in facts for node in dictionaries(fact)]
    shift = next(node for node in nodes if node.get('kind') == 'range_shift')
    assert shift['direction'] == 'left' and shift['element_bits'] == 1
    assert [stage['operation'] for stage in shift['stages']] == ['shift_existing_bits', 'copy_input_bits']
    purposes = {node.get('purpose') for node in nodes if isinstance(node.get('purpose'), str)}
    assert {'incoming_bits_head', 'shift_register_head', 'shift_register_length', 'shift_bit_count'} <= purposes
    assert any(node.get('when_not_executed') == 'no_destination_write' for node in nodes)
    assert any(node.get('kind') == 'same_head' and node.get('result') == 'operation_error' for node in nodes)
    assert sorting_context.handoff['structured_facts']['targets']['instructions'] == []
    assert 'instruction:SFTL' in sorting_context.handoff['capability_discovery']['included_candidates']


def test_natural_timed_tracking_drops_unrelated_instructions_and_resources(sorting_context):
    units = wire_payload(sorting_context)['knowledge']
    instructions = {unit.get('target') or unit.get('name') for unit in units
                    if unit.get('kind') in {'instruction', 'instruction_capability'}}
    assert not instructions & {'PLSR', 'DPLSR', 'DAND', 'WDT', 'DUTY', 'TIMER_CONT_FB_M'}
    assert not any(unit.get('kind') == 'device' and unit.get('target') == 'M8330' for unit in units)
    comparison_forms = {form['opcode'] for unit in units if unit.get('kind') == 'instruction'
                        and unit.get('value', {}).get('family') == 'CMP'
                        for form in unit['value']['forms']}
    assert len(comparison_forms) <= 1


def test_complete_semantics_retain_mcp_research_groups(sorting_context):
    from application.instruction_research import research_plan
    payload = wire_payload(sorting_context)
    card = next(unit for unit in payload['knowledge']
                if unit.get('kind') == 'instruction_capability' and unit.get('name') == 'SFTL')
    assert card['functions'] == ['range_shift']
    assert 'core_interface' not in card
    plan = research_plan({'plc_model': 'FX3U', 'target_mode': 'ladder',
                          'workflow_mode': 'direct_generation', 'generation_input': payload})
    assert {'SFWR', 'SFRD'} <= set(plan['families']['queue'])
    assert 'SFTL' in plan['families']['range_shift'] and 'SFTL' not in plan['families']['queue']
    assert 'explicit' not in plan['families']


def test_natural_timing_delivers_timer_ranges_and_clock_definitions(sorting_context):
    payload = wire_payload(sorting_context)
    units = payload['knowledge']
    clocks = {unit['target']: unit['value']['semantic_facts'] for unit in units
              if unit.get('kind') == 'device' and unit.get('target') in {'M8011', 'M8012'}}
    assert clocks['M8011']['period_ms'] == 10
    assert clocks['M8012']['period_ms'] == 100
    ranges = [node for unit in units if unit.get('target') == 'T' for node in dictionaries(unit)
              if 'first' in node and 'last' in node and 'unit_ms' in node]
    assert any(row['first'] == 200 and row['last'] == 245 and row['unit_ms'] == 10 for row in ranges)
    assert any(row['first'] == 246 and row['last'] == 249 and row.get('latched') is True for row in ranges)
    assert any(row['first'] == 250 and row['last'] == 255 and row.get('latched') is True for row in ranges)


def test_explicit_out_delivers_declared_timer_counter_dependencies():
    from knowledge.retriever import retrieve_fact_aware_knowledge
    rows = retrieve_fact_aware_knowledge('FX3U OUT', plc_model='FX3U', task_type='generate',
        language='ladder', top_k=0, targets={'instructions': [{'opcode': 'OUT'}]})
    assert {'T', 'C'} <= {row.get('fact_target') for row in rows if row.get('fact_kind') == 'device_family'}
    assert all(row.get('technical_status') != 'candidate_evidence' for row in rows)


@pytest.mark.parametrize('requirement,cpu,function,expected,unrelated', [
    ('追踪多个事件直到各自处理完成', 'FX3U', 'range_shift', {'SFTL', 'WSFL'}, {'POP', 'SFL', 'SFRD'}),
    ('用先进先出队列读写记录', 'FX3U', 'queue', {'SFWR', 'SFRD'}, {'POP', 'SFL', 'SFTL', 'WSFL'}),
    ('字区连续数据作为移位寄存器', 'FX3U', 'range_shift', {'WSFL'}, {'SFWR', 'SFRD', 'SFL', 'WDT'}),
    ('通1个扫描、断3个扫描的用户时钟', 'FX3U', 'scan_clock', {'DUTY'}, {'WDT', 'STMR', 'SFTL'}),
    ('刷新 PLC 看门狗', 'Q00J', 'watchdog_refresh', {'WDT'}, {'DUTY', 'ZRST', 'FOR'}),
])
def test_functional_discovery_filters_by_operation(requirement, cpu, function, expected, unrelated):
    from knowledge.retriever import discover_capabilities
    found = discover_capabilities(requirement, plc_model=cpu)
    names = {card['name'] for card in found['candidates'] if function in card['functions']}
    assert expected <= names
    assert not names & unrelated


@pytest.mark.parametrize("mode,cpu,requirement,expected", [
    ("direct", "FX3U", ORIGINAL_REQUIREMENT, {"range_shift": {"SFTL"}, "queue": {"SFWR", "SFRD"}}),
    ("design", "FX3U", ORIGINAL_REQUIREMENT, {"range_shift": {"SFTL"}, "queue": {"SFWR", "SFRD"}}),
    ("design", "Q00J", "扫描任务处理期间刷新 PLC 看门狗。", {"watchdog_refresh": {"WDT"}}),
    ("direct", "FX3U", "生成通1个扫描、断3个扫描的用户时钟。", {"scan_clock": {"DUTY"}}),
])
def test_analysis_prompt_delivers_scoped_call_forms_before_plan_selection(mode, cpu, requirement, expected):
    from application.analysis_context import assemble_analysis_prompt
    from application.generation_context import _build_knowledge_context, _load_plc_models
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from shared.model_context import technical_context_units

    prompt = assemble_analysis_prompt(requirement, plc_model=cpu, analysis_mode=mode,
        model_loader=_load_plc_models, knowledge_builder=_build_knowledge_context,
        resolve_opcode=lambda opcode: DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=cpu))
    units = technical_context_units(prompt.system_prompt)
    facts = {unit["target"]: unit for unit in units if unit.get("kind") == "instruction"}
    for function, instructions in expected.items():
        options = {unit["name"] for unit in units if unit.get("kind") == "instruction_capability"
                   and function in unit.get("functions", [])}
        assert instructions <= options
        for opcode in instructions:
            fact = facts[opcode]
            assert fact["scope"]["models"] == [cpu]
            assert fact["context"]["language"] == "ladder"
            assert any(form["opcode"] == opcode for form in fact["value"]["forms"])
            assert fact["value"]["parameter_tables"] and fact["value"]["additional_knowledge"]
    manifest = prompt.knowledge_context.manifest
    assert manifest["capability_discovery"]["selection_scope"] == "analysis_options"
    assert manifest["structured_facts"]["targets"]["instructions"] == []


def test_confirmed_plan_keeps_instruction_roles_and_retrieves_the_whole_selected_method():
    from application.analysis_response import _parse_analysis_response
    from application.confirmed_generation_context import build_confirmed_generation_context
    from application.generation_agent import _compact_wire_renderer
    from plc.specification.confirmed import build_review_draft
    from plc.specification.provenance import seal_confirmation

    choices = [{"opcode": "BMOV", "role": "归档当前批次记录"},
               {"opcode": "M8002", "role": "首次扫描初始化批次状态"},
               {"opcode": "NO_DOC_REPLAY", "role": "补充诊断记录"}]
    selected = {"approach_id": "queue", "name": "先进先出队列", "implementation_semantics": [],
                "generation_guide": "按登记顺序读取，复位时对连续记录区批量清零。",
                "instruction_choices": choices}
    other = {"approach_id": "shift", "name": "位区移位", "implementation_semantics": [],
             "instruction_choices": [{"opcode": "SFTL", "role": "移动整段标志位"}]}
    analysis = _parse_analysis_response(json.dumps({"summary": "缓冲采样记录", "approaches": [other, selected]}),
        user_text="按顺序保存和读取采样，接到归档命令时保存当批记录，复位时清空。")
    draft = build_review_draft(analysis)
    draft["selected_approach"] = draft["approaches"][1]
    spec, receipt = seal_confirmation(draft)
    assert receipt["confirmation"]["selected_origin"] == "model_proposal"
    assert spec["selected_approach"]["instruction_choices"] == choices
    assert spec["selected_approach"]["generation_contract"]["required_opcodes"] == []

    context = build_confirmed_generation_context(spec, "FX3U", wire_renderer=_compact_wire_renderer("FX3U"))
    payload = wire_payload(context)
    assert payload["confirmed_spec"]["selected_approach"]["instruction_choices"] == choices
    assert {"queue", "bulk_reset"} <= set(context.handoff["capability_discovery"]["functions"])
    instructions = {unit["target"]: unit for unit in payload["knowledge"] if unit["kind"] == "instruction"}
    assert instructions["BMOV"]["scope"]["models"] == ["FX3U"]
    assert "SFTL" not in instructions and "NO_DOC_REPLAY" not in instructions
    targets = context.handoff["structured_facts"]["targets"]
    assert targets["devices"] == ["M8002"]
    assert "M8002" not in {row["opcode"] for row in targets["instructions"]}
    assert any(unit["kind"] == "device" and unit["target"] == "M8002" for unit in payload["knowledge"])
    unresolved = [row for row in context.handoff["fact_coverage"]["requirements"]
                  if row["target"] == "NO_DOC_REPLAY"]
    assert unresolved and {row["status"] for row in unresolved} == {"unresolved"}

    pinned = build_review_draft({"approaches": [{"approach_id": "queue", "name": "先进先出队列"}]}, spec)
    assert pinned["selected_approach"]["instruction_choices"] == choices


def test_mcp_pages_deliver_functional_facts_and_research(sorting_context):
    from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
    from application.read_delivery import assemble_read_pages
    registry = build_default_tool_registry()
    context = build_tool_context({'id': 'functional_facts', 'plc_model': 'FX3U',
                                  'target_mode': 'ladder', 'workflow_mode': 'direct_generation'})
    pages = [registry.call('get_generation_context',
                          {'user_requirement': sorting_context.generation_request}, context)]
    assert pages[0]['ok'], pages[0]
    while next_page := pages[-1]['data']['delivery']['next']:
        pages.append(registry.call(next_page['tool'], next_page['arguments'], context))
        assert pages[-1]['ok'], pages[-1]
    data = assemble_read_pages(pages)
    assert 'SFTL' in data['instruction_research']['families']['range_shift']
    facts = [unit for unit in data['generation_input']['knowledge']
             if unit.get('kind') == 'instruction' and unit.get('target') == 'SFTL']
    assert facts and any(node.get('kind') == 'range_shift' for node in dictionaries(facts))


@pytest.mark.parametrize('kind', ['instruction', 'instruction_capability'])
def test_research_accepts_legacy_and_current_discovery_cards(kind):
    from application.instruction_research import research_plan
    card = {'kind': kind, 'name': 'SFTL', 'functions': ['queue']}
    assert research_plan({'knowledge': [card]})['families'] == {'queue': ['SFTL']}


def test_receipt_detects_removed_functional_link(sorting_context):
    from application.capability_context import reconcile_discovery
    from shared.model_context import technical_block
    units = copy.deepcopy(wire_payload(sorting_context)['knowledge'])
    units = [unit for unit in units if not (unit.get('kind') == 'instruction_capability'
                                          and unit.get('name') == 'SFTL')]
    receipt = reconcile_discovery(sorting_context.handoff['capability_discovery'],
                                  '\n\n'.join(technical_block(unit) for unit in units))
    assert 'instruction:SFTL' not in receipt['included_candidates']
    assert 'instruction:SFTL' in receipt['omitted_candidates']


@pytest.mark.parametrize('reuse_main_hits', [False, True])
def test_discovery_shares_recall_and_preserves_exact_relation_lookup(monkeypatch, reuse_main_hits):
    from knowledge import capability_discovery, fact_corpus, retriever
    from knowledge.evidence import KnowledgeQuery

    functions = ['timer', 'queue', 'range_shift']
    queries = [{'function': name, 'query': name} for name in functions]
    monkeypatch.setattr(capability_discovery, 'functional_queries', lambda _: queries)
    def row(identity):
        return {'id': identity, 'manual_type': 'official'}
    monkeypatch.setattr(fact_corpus, 'resolve',
                        lambda kind, targets, **kw: [row(kind + ':' + target) for target in targets])
    full_queries, resource_queries = [], []
    def full(query, **options):
        full_queries.append(str(query))
        return [row('full:' + str(query))]
    def resource(query, **options):
        resource_queries.append(str(query))
        return [row('clock-resource')]
    monkeypatch.setattr(retriever, 'retrieve_fact_aware_knowledge', full)
    monkeypatch.setattr(retriever.core, 'retrieve_broad_knowledge', resource)

    def inspect(requirement, *, retrieve, **options):
        for function in functions:
            rows = retrieve(KnowledgeQuery(function, metadata={'semantic_function': function}),
                            top_k=64, source_lanes=('fact',))
            identities = {value['id'] for value in rows}
            assert 'function:' + function in identities
            assert 'clock-resource' in identities
            if reuse_main_hits:
                assert 'instruction:BMOV' in identities
        assert len(full_queries) == (0 if reuse_main_hits else 1)
        assert len(resource_queries) == 1
        assert retrieve('PLC BMOV', top_k=8, source_lanes=('fact',)) == [row('full:PLC BMOV')]
        return {'ok': True}
    monkeypatch.setattr(capability_discovery, 'discover', inspect)
    assert retriever.discover_capabilities('process', retrieved_records=(
        [{'instruction_opcode': 'BMOV'}] if reuse_main_hits else [])) == {'ok': True}


def test_ladder_discovery_does_not_scan_st_catalog_for_missing_native_form(monkeypatch):
    from knowledge.capability_discovery import _instruction_card
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    monkeypatch.setattr(registry, 'resolve_form', lambda *args, **kwargs: None)
    def unexpected(*args, **kwargs):
        raise AssertionError('Ladder must not enumerate ST call signatures')
    monkeypatch.setattr(registry, 'resolve_st_call', unexpected)
    assert _instruction_card('UNKNOWN', 'FX3U', {}, target_mode='ladder') is None
