"""Own-source signed word-to-doubleword conversion consumers; no algorithm execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_DBL_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / 'ql-dbl-native-contract-repair'
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
    previous = original.definition_entry(opcode, cpu=cpu)
    assert previous is not None, (cpu, opcode, 'existing knowledge entry required')
    entry = copy.deepcopy(previous)
    entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith(('authored:ql:', 'authored:applied:'))] + copy.deepcopy(source['facts'])
    entry['facts'], _ = compose_programming_facts(registry, opcode, cpu=cpu, existing=entry['facts'])
    entry['call_available'] = False
    assert refresh_reviewed_call_admission(entry, registry), (cpu, opcode)
    registry._definition_owners[('mitsubishi', opcode, cpu)] = entry
    return registry

from checks.test_ascii_conversion_native_contract import _bind
from checks.test_ddrd_ddwr_native_contract import _rungs


def _public(cpu, opcode, args):
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    for rung in _rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        yield ir, public(PLCCore().validate_project(ir))


@pytest.mark.parametrize('cpu', ['Q00J', 'Q02H', 'Q03UDV', 'Q02PH', 'L02'])
@pytest.mark.parametrize('opcode', ['DBL', 'DBLP'])
def test_dbl_own_signed_head_relation_and_public_paths(monkeypatch, cpu, opcode):
    from plc.ir import analyze_instruction_access
    from plc.instructions import reviewed_native_fact_groups, reviewed_native_operand_parameters
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    args = ['D0', 'D100']
    spec = registry.resolve(opcode, cpu=cpu, operand_count=2)
    assert spec.native_operand_order == ('S', 'D'), (cpu, opcode)
    parameters = reviewed_native_operand_parameters(spec, opcode, args, cpu)
    for index, bits, words in [(0, 16, 1), (1, 32, 2)]:
        assert any(p.get('word_device_layout', {}).get('value_bits') == bits
                   and p['word_device_layout']['word_count'] == words
                   and p['word_device_layout']['signed'] is True for p in parameters[index]), (cpu, opcode, index, parameters)
    facts = reviewed_native_fact_groups(spec, opcode, args, cpu,
                    dimensions={'effects.conversion', 'execution.operation_condition'})
    conversion = next(f.value for f in facts if f.dimension == 'effects.conversion' and f.value.get('source') == 'S')
    assert conversion['result_relation'] == dict(operator='sign_extend', source_bits=16,
                destination_bits=32, preserve_signed_integer_value=True), (cpu, opcode, conversion)
    assert conversion['destination_word_layout'] == dict(lower_16_bits_word_offset=0, upper_16_bits_word_offset=1)
    assert conversion['published_example']['destination_hex'] == 'FFFFFB2E'
    assert conversion['full_source_relation_complete'] is True
    for key in ['source_destination_overlap_behavior_established', 'command_FALSE_output_behavior_established',
                'core_execution_established', 'native_execution_verified']:
        assert conversion[key] is False, (cpu, opcode, key)
    gate = next(f.value for f in facts if f.dimension == 'execution.operation_condition')
    assert gate['enabled_when'] == ('execution_condition_ON' if opcode == 'DBL' else 'execution_condition_rising_edge'), (cpu, opcode, gate)
    assert analyze_instruction_access(opcode, args, plc_model=cpu) == (['D0'], ['D100', 'D101'])
    for ir, result in _public(cpu, opcode, args):
        assert result['valid'] is True, (cpu, opcode, args, result)
        assert ir['networks'][0]['writes'] == ['D100', 'D101'], (cpu, opcode, ir)


@pytest.mark.parametrize('opcode', ['DBL', 'DBLP'])
def test_dbl_signed_literals_and_device_destination(monkeypatch, opcode):
    from plc.instruction_constraints import literal_operand_values
    registry = _registry('Q03UDV', opcode)
    _bind(monkeypatch, registry)
    spec = registry.resolve(opcode, cpu='Q03UDV', operand_count=2)
    for token, value in [('HFB2E', -1234), ('HFFFF', -1), ('K-32768', -32768), ('K32767', 32767)]:
        assert literal_operand_values(spec, [token, 'D100'], plc_model='Q03UDV', opcode=opcode)['S'] == value
        for ir, result in _public('Q03UDV', opcode, [token, 'D100']):
            assert result['valid'] is True, (opcode, token, result)
            assert ir['networks'][0]['reads'] == ['X0'], (opcode, token, ir)
    for args in [['K32768', 'D100'], ['K-32769', 'D100'], ['H10000', 'D100'], ['D0', 'K0'], ['D0', 'H0']]:
        for _, result in _public('Q03UDV', opcode, args):
            assert result['valid'] is False, (opcode, args, result)


@pytest.mark.parametrize('opcode', ['DBL', 'DBLP'])
def test_dbl_digit_selection_uses_own_group_limits(monkeypatch, opcode):
    from plc.ir import analyze_instruction_access
    registry = _registry('Q03UDV', opcode)
    _bind(monkeypatch, registry)
    spec = registry.resolve(opcode, cpu='Q03UDV', operand_count=2)
    assert [p.digit_groups_max for p in spec.operands] == [4, 8]
    for source_groups, destination_groups in [(1, 4), (4, 8), (4, 1)]:
        args = [f'K{source_groups}M0', f'K{destination_groups}M100']
        reads, writes = analyze_instruction_access(opcode, args, plc_model='Q03UDV')
        assert reads == [f'M{i}' for i in range(source_groups * 4)], (opcode, args, reads)
        assert writes == [f'M{i}' for i in range(100, 100 + destination_groups * 4)], (opcode, args, writes)
        for _, result in _public('Q03UDV', opcode, args):
            assert result['valid'] is True, (opcode, args, result)
    for args in [['K5M0', 'D100'], ['D0', 'K9M100']]:
        for _, result in _public('Q03UDV', opcode, args):
            assert result['valid'] is False, (opcode, args, result)


@pytest.mark.parametrize('opcode', ['DBL', 'DBLP'])
def test_dbl_source_one_word_destination_two_word_range(monkeypatch, opcode):
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch, _registry('Q03UDV', opcode))
    maximum = DEVICE_LIMITS['Q03UDV']['D']
    for _, result in _public('Q03UDV', opcode, [f'D{maximum}', f'D{maximum - 1}']):
        assert result['valid'] is True, (opcode, maximum, result)
    for _, result in _public('Q03UDV', opcode, ['D0', f'D{maximum}']):
        assert result['valid'] is False, (opcode, maximum, result)


@pytest.mark.parametrize('cpu,profile', [('Q02H', 'q02'), ('Q03UDV', 'q03udv'), ('L02', 'l02')])
@pytest.mark.parametrize('opcode', ['DBL', 'DBLP'])
def test_dbl_existing_codec_and_source_csv(monkeypatch, tmp_path, cpu, profile, opcode):
    from checks.test_ascii_conversion_native_contract import _codec_csv_roundtrip
    _bind(monkeypatch, _registry(cpu, opcode))
    _codec_csv_roundtrip(tmp_path, opcode, cpu, profile)
