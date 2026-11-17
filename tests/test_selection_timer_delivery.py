import copy
import json

import pytest


@pytest.mark.parametrize('direct', [True, False])
def test_selection_rules_reach_final_wire_with_structured_knowledge(direct):
    from application.confirmed_generation_context import build_direct_generation_context, build_confirmed_generation_context
    from application.generation_agent import _compact_wire_renderer
    from knowledge.evidence import KnowledgeContext
    from plc.maintainability import SELECTION_POLICY
    from shared.model_context import technical_knowledge_text

    evidence = '\n'.join(technical_knowledge_text({'text': f'Reference {i}'}) for i in range(44))
    spec = {'plc_model': 'FX3U', 'summary': 'X0 controls Y0', 'parameters': [],
            'selected_approach': {'generation_guide': 'Direct contact'}, 'io_table': []}
    options = dict(knowledge_builder=lambda *a, **k: KnowledgeContext(evidence, {'capability_discovery': {'policy_version': 'test'}}),
                   wire_renderer=_compact_wire_renderer('FX3U', direct=direct), model_profile={})
    context = (build_direct_generation_context('X0 controls Y0', 'FX3U', **options) if direct else
               build_confirmed_generation_context(spec, 'FX3U', **options))
    messages = context.wire_packet['messages']
    system = next(m['content'] for m in messages if m['role'] == 'system')
    assert system.count(SELECTION_POLICY) == 1
    assert context.handoff['capability_discovery']['tokens']['selection_policy_delivered'] is True
    assert '不预设时间窗口逐项 OR' in system
    assert '按已知精度、暂停恢复及并发要求比较完整实现' in system
    if direct:
        assert '相对计时可用一个持续累计的时间参考' not in system
    user = next(m['content'] for m in messages if m['role'] == 'user')
    assert 'Capability selection and maintainability' not in user
    assert 'Reference 43' in user


@pytest.mark.parametrize('model', ['FX3U', 'FX3UC', 'FX3U/FX3UC'])
@pytest.mark.parametrize('runtime', [True, False])
def test_timer_knowledge_uses_same_retention_table_as_executor(model, runtime):
    from knowledge.record_projection import _current_timer_record
    from plc.runtime_semantics import timer_runtime_fact
    from shared.model_context import technical_knowledge_text
    packet = {'content_origin': 'authored_fact', 'kind': 'device_family', 'target': 'T',
              'scope': {'models': ['FX3U', 'FX3UC', 'FX5U']}, 'sources': [],
              'value': {'OUT': [{'first': 0, 'last': 199, 'reset_on_disable': True}]} if runtime else
                       {'source_constraints': {'retentive_form': 'use ST for the retentive timer'}}}
    row = {'text': '保持型使用ST。\n' + json.dumps(packet), 'fact_kind': 'device_family', 'fact_target': 'T',
           **({'runtime_dimension': 'timers'} if runtime else {})}
    before = copy.deepcopy(row)
    result = _current_timer_record(row, model)
    assert row == before
    assert 'use ST for' not in technical_knowledge_text(result)
    assert '保持型使用ST' not in result['text']
    value = json.loads(result['text'])
    assert value['scope']['models'] == [model]
    for address, unit in [('T246', 1), ('T249', 1), ('T250', 100), ('T255', 100)]:
        fact = timer_runtime_fact(model, address)
        interval = next(r for r in value['value']['OUT'] if r['first'] <= int(address[1:]) <= r['last'])
        assert interval['unit_ms'] == fact['unit_ms'] == unit
        assert interval['reset_on_disable'] is fact['reset_on_disable'] is False
    assert _current_timer_record(row, 'Q03UDV') == before


@pytest.mark.parametrize('address,unit', [('T246', 1), ('T249', 1), ('T250', 100), ('T255', 100)])
@pytest.mark.parametrize('reset', ['RST', 'ZRST'])
def test_retentive_timer_holds_fractional_progress_and_reset_clears_it(address, unit, reset):
    from simulator.bounded import ScanMachine
    from tests.test_simulator_verification import _bounded_ladder
    instruction = f'{reset} {address}' + (f' {address}' if reset == 'ZRST' else '')
    ladder = _bounded_ladder((['NO X0'], [f'OUT {address} K10']), (['NO X1'], [instruction]))
    machine = ScanMachine(ladder, plc_model='FX3U', initial={address: 0, address+'.contact': False},
                          execution_context={'program_type': 'scan'})
    def scan(t, enabled, resetting=False):
        return machine.scan({'timestamp_ms': t*unit, 'inputs': {'X0': enabled, 'X1': resetting}})['after']
    scan(0, True)
    assert scan(2.5, True)[address] == 2
    assert scan(3.5, False)[address] == 3
    assert scan(100, False)[address] == 3
    assert scan(200, True)[address] == 3
    assert scan(206.5, True)[address+'.contact'] is True
    assert scan(210, False)[address+'.contact'] is True
    cleared = scan(220, False, True)
    assert cleared[address] == 0 and cleared[address+'.contact'] is False
    assert scan(230, True)[address] == 0
    assert scan(231, True)[address] == 1
    assert not machine.gaps


def test_unknown_retained_timer_value_is_not_assumed_zero_after_disable():
    from simulator.bounded import ScanMachine
    from tests.test_simulator_verification import _bounded_ladder
    machine = ScanMachine(_bounded_ladder((['NO X0'], ['OUT T250 K10'])), plc_model='FX3U',
                          initial={}, execution_context={'program_type': 'scan'})
    machine.scan({'timestamp_ms': 0, 'inputs': {'X0': False}})
    result = machine.scan({'timestamp_ms': 100, 'inputs': {'X0': True}})
    assert result['after'].get('T250') is None
    assert any(g['reason'] == 'initial_timer_clock_state_unknown' for g in machine.gaps)
