"""Own Q/L ASCII conversion storage and conditional writes; no numeric execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_QL_ASCII_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / ('fx-dbinda-native-contract-repair' if cpu.startswith('FX') else 'ql-bcd-ascii-native-contract-repair')
    registry = copy.copy(original)
    for name in ('_cpu_contracts', '_definition_contract_bases', '_definition_owners'):
        setattr(registry, name, dict(getattr(original, name)))
    registry._loaded_definition_contracts = set(original._loaded_definition_contracts)
    registry._clear_definition_contracts()
    contract_file = folder / 'contract-additions.json'
    contracts = json.loads(contract_file.read_text(encoding='utf-8'))['cpu_contracts']
    if ('mitsubishi', opcode, cpu) not in registry._cpu_contracts:
        registry.load_target_contracts(contracts)
    source_file = folder / 'source-facts.json'
    source = next(e for e in json.loads(source_file.read_text(encoding='utf-8'))['entries']
                  if e['target_model'] == cpu and e['opcode'] == opcode)
    entry = copy.deepcopy(original.definition_entry(opcode, cpu=cpu))
    entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith(('authored:ql:', 'authored:applied:'))] + copy.deepcopy(source['facts'])
    entry['facts'], _ = compose_programming_facts(registry, opcode, cpu=cpu, existing=entry['facts'])
    entry['call_available'] = False
    assert refresh_reviewed_call_admission(entry, registry), (cpu, opcode)
    registry._definition_owners[('mitsubishi', opcode, cpu)] = entry
    return registry



from checks.test_ascii_conversion_native_contract import _bind, _codec_csv_roundtrip
from checks.test_ddrd_ddwr_native_contract import _rungs


def test_dbinha_own_unsigned_source_HFFFFFFFF_reaches_public_validation(monkeypatch):
    from plc.instruction_constraints import literal_operand_values, numeric_operand_type
    from plc.instructions import reviewed_native_operand_parameters
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    registry = _registry('Q03UDV', 'DBINHA')
    _bind(monkeypatch, registry)
    spec = registry.resolve('DBINHA', cpu='Q03UDV', operand_count=2)
    parameters = reviewed_native_operand_parameters(spec, 'DBINHA', ['HFFFFFFFF', 'D100'], 'Q03UDV')
    assert any(p['word_device_layout']['signed'] is False for p in parameters[0])
    assert numeric_operand_type(spec, 0).signed is False
    assert literal_operand_values(spec, ['HFFFFFFFF', 'D100'], plc_model='Q03UDV', opcode='DBINHA')['S'] == 4294967295
    for rung in _rungs('DBINHA', ['HFFFFFFFF', 'D100']):
        result = public(PLCCore().validate_project(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')))
        assert result.get('valid') is True, result



def test_dbinda_high_padding_byte_is_conditional_inside_six_word_storage(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.instructions import reviewed_native_operand_parameters
    registry = _registry('Q03UDV', 'DBINDA')
    _bind(monkeypatch, registry)
    args = ['D0', 'D100']
    spec = registry.resolve('DBINDA', cpu='Q03UDV', operand_count=2)
    parameters = reviewed_native_operand_parameters(spec, 'DBINDA', args, 'Q03UDV')
    destination = next(p for p in parameters[1] if p.get('conditional_byte_write'))
    assert destination['word_device_layout']['element_count'] == 11
    assert destination['word_device_layout']['word_count'] == 6
    for rung in _rungs('DBINDA', args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
        network = ir['networks'][0]
        assert 'SM701' in network['reads']
        assert set(network['writes']) == {f'D{i}' for i in range(100, 106)}
        byte_regions = [r for r in network['access_regions'] if r.get('value_bits') == 8]
        assert len(byte_regions) == 2
        assert {(r['condition']['equals'], r['value_when_condition']) for r in byte_regions} == {(False, 0), (True, 32)}
        for region in byte_regions:
            assert region['offset'] == 5 and region['bit_offset'] == 8
            assert region['addresses'] == ['D105'] and region['unit'] == 'bits' and region['resolved_count'] == 8
            assert region['physical_bit_region'] == dict(address_space='D', start_bit=105 * 16 + 8, bit_count=8)
            assert 'device_value:SM701' in region['requires']
        validate_plc_ir(json.loads(json.dumps(ir)))


def test_dbinha_conflicting_tail_offsets_are_candidates_not_a_resolved_tail(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    _bind(monkeypatch, _registry('Q03UDV', 'DBINHA'))
    for rung in _rungs('DBINHA', ['D0', 'D100']):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
        network = ir['networks'][0]
        assert 'SM701' in network['reads']
        assert set(network['writes']) == {f'D{i}' for i in range(100, 105)}
        tails = [r for r in network['access_regions'] if r.get('candidate') is True]
        assert {r['offset'] for r in tails} == {2, 4}
        for region in tails:
            assert region['condition'] == dict(context='device_state', device='SM701', equals=False)
            assert region['source_conflict'] == dict(printed_prose_word_offset=2, own_diagram_word_offset=4)
            assert 'source_tail_offset_conflict' in region['requires']
            assert 'physical_bit_region' not in region
        validate_plc_ir(json.loads(json.dumps(ir)))


@pytest.mark.parametrize('opcode,words,characters', [
    ('DBCDDA', 4, 8), ('DBCDDAP', 4, 8), ('DBINDA', 6, 11),
    ('DBINDAP', 6, 11), ('DBINHA', 4, 8), ('DBINHAP', 4, 8)])
def test_QL_ascii_own_fixed_payload_gate_and_public_storage(monkeypatch, opcode, words, characters):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.instructions import reviewed_native_operand_parameters, reviewed_native_fact_groups
    from plc.core import PLCCore
    from application.projects import public
    registry = _registry('Q03UDV', opcode)
    _bind(monkeypatch, registry)
    args = ['D0', 'D100']
    spec = registry.resolve(opcode, cpu='Q03UDV', operand_count=2)
    parameters = reviewed_native_operand_parameters(spec, opcode, args, 'Q03UDV')
    destination = next(p for p in parameters[1] if p.get('word_device_layout', {}).get('element_count') == characters)
    assert destination['word_device_layout']['word_count'] == words
    gate = next(g.value for g in reviewed_native_fact_groups(spec, opcode, args, 'Q03UDV',
                dimensions={'execution.operation_condition'}))
    assert gate['enabled_when'] == ('execution_condition_rising_edge' if opcode.endswith('P') else 'execution_condition_ON')
    assert gate['core_execution_established'] is False
    for rung in _rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
        validate_plc_ir(ir)
        result = public(PLCCore().validate_project(ir))
        assert result.get('valid') is True, result
        network = ir['networks'][0]
        assert {'D0', 'D1', 'SM701'} <= set(network['reads'])
        from plc.device_identity import instruction_operand_regions
        base = next(r for r in instruction_operand_regions(spec, opcode, args, 'Q03UDV')
                    if r.get('head') == 'D100' and not r.get('condition'))
        assert base['count'] == words and base['addresses'] == [f'D{i}' for i in range(100, 100 + words)]
        if opcode.startswith('DBCDD'):
            tails = [r for r in network['access_regions'] if r.get('condition')]
            assert len(tails) == 1 and tails[0]['offset'] == 4
            assert tails[0]['condition'] == dict(context='device_state', device='SM701', equals=False)
            assert tails[0]['addresses'] == ['D104']
        elif opcode.startswith('DBIND'):
            assert {r['condition']['equals'] for r in network['access_regions'] if r.get('value_bits') == 8} == {False, True}
        else:
            assert {r['offset'] for r in network['access_regions'] if r.get('candidate')} == {2, 4}


@pytest.mark.parametrize('opcode', ['DBCDDA', 'DBINDA', 'DBINHA'])
def test_QL_ascii_own_destination_bounds_and_namespace(monkeypatch, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch, _registry('Q03UDV', opcode))
    words = 6 if opcode == 'DBINDA' else 4
    maximum = DEVICE_LIMITS['Q03UDV']['D']
    for rung in _rungs(opcode, ['D0', f'D{maximum - words + 1}']):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
        validate_plc_ir(ir)
        regions = ir['networks'][0]['access_regions']
        if words == 4:
            assert any('conditional_storage_region_exceeds_device_range' in r.get('requires', []) for r in regions)
        else:
            assert all(f'D{maximum + 1}' not in r.get('addresses', []) for r in regions)
    for destination in [f'D{maximum - words + 2}', 'M0', 'Z0', 'K0', r'U0\G100', r'J1\W100']:
        for rung in _rungs(opcode, ['D0', destination]):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))


@pytest.mark.parametrize('opcode', ['DBCDDA', 'DBCDDAP', 'DBINDA', 'DBINDAP', 'DBINHA', 'DBINHAP'])
def test_QL_ascii_existing_codec_and_source_CSV(monkeypatch, tmp_path, opcode):
    _bind(monkeypatch, _registry('Q03UDV', opcode))
    _codec_csv_roundtrip(tmp_path, opcode, 'Q03UDV', 'q03udv')


@pytest.mark.parametrize('cpu', ['FX3U', 'FX3UC'])
@pytest.mark.parametrize('opcode', ['DBINDA', 'DBINDAP'])
def test_FX_dbinda_own_six_words_M8091_byte_and_version(monkeypatch, cpu, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.instructions import reviewed_native_operand_parameters
    from plc.core import PLCCore
    from application.projects import public
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    spec = registry.resolve(opcode, cpu=cpu, operand_count=2)
    parameters = reviewed_native_operand_parameters(spec, opcode, ['D0', 'D100'], cpu)
    destination = next(p for p in parameters[1] if p.get('conditional_byte_write'))
    assert destination['word_device_layout']['word_count'] == 6
    assert destination['word_device_layout']['element_count'] == 12
    assert destination['conditional_byte_write']['selector'] == 'M8091'
    for rung in _rungs(opcode, ['D0', 'D100']):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        validate_plc_ir(ir)
        network = ir['networks'][0]
        assert {'D0', 'D1', 'M8091'} <= set(network['reads'])
        assert 'SM701' not in network['reads']
        assert set(network['writes']) == {f'D{i}' for i in range(100, 106)}
        byte_regions = [r for r in network['access_regions'] if r.get('value_bits') == 8]
        assert len(byte_regions) == 2
        assert {(r['condition']['equals'], r['value_when_condition']) for r in byte_regions} == {(False, 0), (True, 32)}
        for region in byte_regions:
            assert region['condition']['device'] == 'M8091'
            assert region['offset'] == 5 and region['bit_offset'] == 8 and region['addresses'] == ['D105']
            assert 'device_value:M8091' in region['requires']
        valid = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': '2.20'}))
        assert valid.get('valid') is True, valid
        low = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': '2.19'}))
        assert low.get('valid') is False and low.get('counts', {}).get('error', 0) > 0, low
    for destination in ['M0', 'Z0', 'K0', r'U0\G100']:
        for rung in _rungs(opcode, ['D0', destination]):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))


@pytest.mark.parametrize('opcode', ['DBINDA', 'DBINDAP'])
def test_FX_dbinda_existing_codec_and_source_CSV(monkeypatch, tmp_path, opcode):
    _bind(monkeypatch, _registry('FX3U', opcode))
    _assert_FX_dbinda_own_nine_steps(opcode, 'FX3U')
    _codec_csv_roundtrip(tmp_path, opcode, 'FX3U', 'fx3u')


@pytest.mark.parametrize('opcode,signed,expected,rejected', [
    ('DBINDA', True, -1, ['K2147483648', 'K-2147483649']),
    ('DBINHA', False, 4294967295, ['K-1', 'K4294967296'])])
def test_QL_ascii_own_signed_and_unsigned_literal_boundaries(monkeypatch, opcode, signed, expected, rejected):
    from plc.instruction_constraints import numeric_operand_type, literal_operand_values
    from plc.ir import build_plc_ir, validate_plc_ir
    registry = _registry('Q03UDV', opcode)
    _bind(monkeypatch, registry)
    spec = registry.resolve(opcode, cpu='Q03UDV', operand_count=2)
    assert numeric_operand_type(spec, 0).signed is signed
    assert literal_operand_values(spec, ['HFFFFFFFF', 'D100'], plc_model='Q03UDV', opcode=opcode)['S'] == expected
    for rung in _rungs(opcode, ['HFFFFFFFF', 'D100']):
        validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))
    for literal in rejected:
        for rung in _rungs(opcode, [literal, 'D100']):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))


def _assert_FX_dbinda_own_nine_steps(opcode, cpu):
    from plc.instruction_steps import instruction_step_width
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    args = ['D100', 'D200']
    # Own R679/R681 specifies nine steps for these 32-bit forms.
    assert instruction_step_width(opcode, args, plc_model=cpu).steps == 9
    raw = encode_instruction(opcode, args, profile='fx3u', plc_model=cpu, text_encoding='cp932')
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile='fx3u', text_encoding='cp932')
    assert len(listing.instructions) == 1 and listing.instructions[0].step_width == 9
