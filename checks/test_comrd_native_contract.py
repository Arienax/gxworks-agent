"""Source-bound comment identity and conditional character-storage access."""
import copy
import json
from pathlib import Path
import pytest

ROOT = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')
OWNERS = [('Q03UDV', op, 'ql', 16, 'SM701', True) for op in ('COMRD', 'COMRDP')]
OWNERS += [('FX3U', op, 'fx', 8, 'M8091', False) for op in ('COMRD', 'COMRDP')]


def _owner_registry(cpu='Q03UDV', opcode='COMRD', family='ql', mutate=None):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    if mutate is None:
        entry = original.definition_entry(opcode, cpu=cpu)
        return original, bool(entry and entry.get('call_available'))
    registry = copy.copy(original)
    for name in ('_cpu_contracts', '_definition_contract_bases', '_definition_owners'):
        setattr(registry, name, dict(getattr(original, name)))
    registry._loaded_definition_contracts = set(original._loaded_definition_contracts)
    registry._clear_definition_contracts()
    source = next(e for e in json.loads((ROOT / (family + '-comrd-native-contract-repair') /
                  'source-facts.json').read_text(encoding='utf-8'))['entries']
                  if e['target_model'] == cpu and e['opcode'] == opcode)
    entry = copy.deepcopy(original.definition_entry(opcode, cpu=cpu))
    assert entry is not None
    entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith(('authored:ql:', 'authored:applied:'))]
    own = copy.deepcopy(source['facts'])
    if mutate is not None:
        mutate(own)
    entry['facts'] += own
    entry['facts'], _ = compose_programming_facts(registry, opcode, cpu=cpu, existing=entry['facts'])
    entry['call_available'] = False
    admitted = refresh_reviewed_call_admission(entry, registry)
    registry._definition_owners[('mitsubishi', opcode, cpu)] = entry
    return registry, admitted


def _bind_registry(monkeypatch, registry):
    import plc.instructions
    import plc.ir
    import plc.validation
    monkeypatch.setattr(plc.instructions, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    monkeypatch.setattr(plc.ir, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    monkeypatch.setattr(plc.validation, 'DEFAULT_INSTRUCTION_REGISTRY', registry)


def _rungs(opcode, source, destination):
    return [dict(rung_id=1, ordered_instructions=[dict(op='LD', args=['X1']),
            dict(op=opcode, args=[source, destination]), dict(op='END', args=[])]),
        dict(rung_id=1, branches=[dict(branch_id=1, y_offset_level=0,
            inputs=[dict(type='NO', address='X1')],
            outputs=[dict(type='APP_INSTR', opcode=opcode, operands=[source, destination])])])]


@pytest.mark.parametrize('cpu,opcode,family,count,selector,active', OWNERS)
@pytest.mark.parametrize('source', ['D0', 'M0'])
def test_comrd_metadata_identity_and_base_storage_keep_tail_conditional(
        monkeypatch, cpu, opcode, family, count, selector, active, source):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry, admitted = _owner_registry(cpu, opcode, family)
    assert admitted
    _bind_registry(monkeypatch, registry)
    for rung in _rungs(opcode, source, 'D100'):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        validate_plc_ir(ir)
        network = ir['networks'][0]
        assert source not in network['reads']
        assert selector in network['reads']
        assert {f'D{n}' for n in range(100, 100 + count)} <= set(network['writes'])
        # Aggregate writes are a conservative may-set; the tail remains a
        # distinct conditional region, never an unconditional 17/9-word span.
        assert f'D{100 + count}' in network['writes']
        tails = [r for r in network['access_regions'] if r.get('condition', {}).get('device') == selector]
        assert len(tails) == 1
        tail = tails[0]
        assert tail['condition'] == dict(context='device_state', device=selector, equals=active)
        assert tail['offset'] == count and tail['addresses'] == [f'D{100 + count}']
        assert tail['count'] == 1 and tail['value_when_condition'] == 0
        assert 'device_value:' + selector in tail['requires']
        assert selector in tail['dependencies']


@pytest.mark.parametrize('cpu,family,count,selector', [('Q03UDV', 'ql', 16, 'SM701'), ('FX3U', 'fx', 8, 'M8091')])
def test_comrd_base_tail_range_boundaries_remain_distinct(monkeypatch, cpu, family, count, selector):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.device_profiles import DEVICE_LIMITS
    registry, admitted = _owner_registry(cpu, 'COMRD', family)
    assert admitted
    _bind_registry(monkeypatch, registry)
    maximum = DEVICE_LIMITS[cpu]['D']
    for rung in _rungs('COMRD', 'D0', f'D{maximum - count + 1}'):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        validate_plc_ir(ir)
        tails = [r for r in ir['networks'][0]['access_regions'] if r.get('condition', {}).get('device') == selector]
        assert len(tails) == 1
        assert 'conditional_storage_region_exceeds_device_range' in tails[0]['requires']
        assert 'addresses' not in tails[0]
    for rung in _rungs('COMRD', 'D0', f'D{maximum - count + 2}'):
        with pytest.raises(ValueError, match='memory extent exceeds'):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
            validate_plc_ir(ir)


def test_comrd_FX_indexed_selector_reads_index_but_not_selected_value(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry, admitted = _owner_registry('FX3U', 'COMRD', 'fx')
    assert admitted
    _bind_registry(monkeypatch, registry)
    for rung in _rungs('COMRD', 'D0Z0', 'D100'):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U')
        validate_plc_ir(ir)
        assert 'Z0' in ir['networks'][0]['reads']
        assert 'D0' not in ir['networks'][0]['reads']


@pytest.mark.parametrize('source,destination', [('K1', 'D100'), ('"D0"', 'D100'), ('D0', '"text"')])
def test_comrd_identity_and_destination_reject_literals(monkeypatch, source, destination):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry, admitted = _owner_registry()
    assert admitted
    _bind_registry(monkeypatch, registry)
    for rung in _rungs('COMRD', source, destination):
        with pytest.raises(ValueError):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
            validate_plc_ir(ir)


@pytest.mark.parametrize('change', ['missing_selector', 'reads_value', 'unreviewed', 'fake_word_layout'])
def test_comrd_selector_requires_reviewed_identity_without_fabricated_storage(change):
    def mutate(facts):
        for fact in facts:
            value = fact.get('value', {})
            if fact['dimension'] != 'parameters.typed_operands' or not value.get('native_form'):
                continue
            source = value['operands'][0]
            if change == 'missing_selector': source.pop('selector_kind')
            elif change == 'reads_value': source['device_value_read'] = True
            elif change == 'unreviewed': fact['status'] = 'candidate_evidence'
            else: source['word_device_layout'] = dict(device_unit_bits=16, word_count=1)
    _, admitted = _owner_registry(mutate=mutate)
    assert not admitted



def test_comrd_word_value_timer_head_keeps_conditional_tail_address(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry, admitted = _owner_registry()
    assert admitted
    _bind_registry(monkeypatch, registry)
    for rung in _rungs('COMRD', 'D0', 'T0'):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
        validate_plc_ir(ir)
        tail = next(r for r in ir['networks'][0]['access_regions'] if r.get('condition', {}).get('device') == 'SM701')
        assert tail['addresses'] == ['T16']
        assert 'conditional_storage_head_not_resolved' not in tail['requires']


def test_comrd_QL_index_permission_is_not_inferred_from_FX():
    from plc.ir import build_plc_ir, validate_plc_ir
    for rung in _rungs('COMRD', 'D0Z0', 'D100'):
        with pytest.raises(ValueError, match='Indexed comment-device selection is not source-verified'):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
            validate_plc_ir(ir)
