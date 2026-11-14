import pytest

from plc.runtime_semantics import control_runtime_facts, timer_runtime_fact


@pytest.mark.parametrize('cpu,address,opcode,unit_ms', [
    ('FX3U', 'T0', 'OUT', 100),
    ('FX3U', 'T199', 'OUT', 100),
    ('FX3U', 't200', 'out', 10),
    ('FX3U', 'T245', 'OUT', 10),
    ('FX3U', 'T256', 'OUT', 1),
    ('FX3U', 'T511', 'OUT', 1),
    ('FX5U', 'T0', 'OUT', 100),
    ('FX5U', 'T0', 'OUTH', 10),
    ('FX5U', 'T0', 'OUTHS', 1),
])
def test_timer_lookup_preserves_documented_timebase_and_source(cpu, address, opcode, unit_ms):
    fact = timer_runtime_fact(cpu, address, opcode)
    assert fact['unit_ms'] == unit_ms
    assert fact['reset_on_disable'] is True
    assert fact['device'] == address.upper()
    assert fact['form'] == opcode.upper()
    assert fact['target_model'] == cpu
    assert fact['source'] == control_runtime_facts(cpu)['timers']['source']


@pytest.mark.parametrize('cpu,address,opcode', [
    ('FX3U', 'M0', 'OUT'),
    ('FX3U', 'T0junk', 'OUT'),
    ('FX3U', 'T512', 'OUT'),
    ('FX3U', 'T0', 'UNSUPPORTED'),
    ('FX9U', 'T0', 'OUT'),
])
def test_timer_lookup_does_not_invent_missing_facts(cpu, address, opcode):
    assert timer_runtime_fact(cpu, address, opcode) is None


def test_fx3u_always_on_keeps_its_list_and_manual_binding():
    facts = control_runtime_facts('FX3U')
    assert facts['always_on'] == ['M8000']
    assert facts['always_on_source']['manual_number'] == 'JY997D16601'
    assert facts['always_on_source']['revision'] == 'R'
    assert facts['always_on_source']['pdf_page'] == 867
    assert 'RUN mode' in facts['always_on_source']['condition']


def test_unknown_cpu_does_not_inherit_the_always_on_binding():
    facts = control_runtime_facts('FX9U')
    assert 'always_on' not in facts


def test_transfer_widths_keep_their_own_manual_pages():
    fx = control_runtime_facts('FX1S')
    assert fx['transfers'] == {'MOV': 16, 'DMOV': 32}
    assert fx['transfers_source']['manual_number'] == 'JY997D34701'
    assert fx['transfers_source']['pdf_pages'] == [141, 142, 143]
    q = control_runtime_facts('Q03UDV')
    assert q['transfers'] == {'MOV': 16, 'DMOV': 32}
    assert q['transfers_source']['manual_number'] == 'SH080809ENG'
    assert q['transfers_source']['pdf_pages'] == [301, 302]

