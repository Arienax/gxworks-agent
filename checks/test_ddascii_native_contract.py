"""Own double ASCII conversion consumers; source-qualified storage only, no execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_DDASCII_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / ('fx-ddabin-native-contract-repair' if cpu.startswith('FX') else 'ql-ddabcd-native-contract-repair')
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



from checks.test_ascii_conversion_native_contract import _bind, _codec_csv_roundtrip
from checks.test_ddrd_ddwr_native_contract import _rungs



OPS = ['DDABCD', 'DDABCDP', 'DDABIN', 'DDABINP']


def _public(cpu, opcode, args, confirmed=None):
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    for rung in _rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        yield ir, public(PLCCore().validate_project(ir, confirmed_spec=confirmed))


@pytest.mark.parametrize('cpu', ['Q03UDV', 'L02'])
@pytest.mark.parametrize('opcode', OPS)
def test_QL_double_ascii_own_fixed_payload_and_partial_conversion(monkeypatch, cpu, opcode):
    from plc.ir import analyze_instruction_access
    from plc.instructions import reviewed_native_fact_groups, reviewed_native_operand_parameters
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    args = ['D0', 'D100']
    spec = registry.resolve(opcode, cpu=cpu, operand_count=2)
    is_bcd = opcode.startswith('DDABCD')
    words, chars = (4, 8) if is_bcd else (6, 11)
    assert spec.native_operand_order == ('S', 'D'), (cpu, opcode, spec)
    parameters = reviewed_native_operand_parameters(spec, opcode, args, cpu)
    source = next(p for p in parameters[0] if p.get('word_device_layout', {}).get('kind') == 'fixed_character_sequence')
    assert source['word_device_layout']['word_count'] == words and source['word_device_layout']['element_count'] == chars
    assert source['word_device_layout']['element_bits'] == 8
    destination = next(p for p in parameters[1] if p.get('word_device_layout', {}).get('value_bits') == 32)
    assert destination['word_device_layout']['word_count'] == 2
    assert destination['word_device_layout']['signed'] is (not is_bcd)
    assert destination['encoding'] == ('BCD' if is_bcd else 'binary')
    groups = reviewed_native_fact_groups(spec, opcode, args, cpu, dimensions={'effects.conversion', 'execution.operation_condition'})
    operation = next(g.value for g in groups if g.dimension == 'effects.conversion' and g.value.get('source') == 'S')
    assert operation['source_character_count'] == chars and operation['source_word_count'] == words
    assert operation['destination_word_order'] == 'low_word_first'
    assert operation['valid_digit_position_mapping_established'] is True
    assert operation['full_source_relation_complete'] is False
    for key in ['source_destination_overlap_behavior_established','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:
        assert operation[key] is False, (cpu, opcode, key, operation)
    if is_bcd:
        assert operation['known_space_ASCII_code'] == 32 and operation['space_digit_value'] == 0
        assert operation['printed_normalization_interval_established'] is False
        assert operation['zero_byte_handling_established'] is False
        assert [p['destination_nibble_bit_offset'] for p in operation['character_positions']] == list(range(28,-1,-4))
    else:
        assert source['ignored_source_byte'] == dict(word_offset=5,byte_offset=1)
        assert operation['ignored_source_byte'] == dict(source_word_offset=5,source_byte_offset=1)
        assert operation['other_sign_ASCII_codes_processed_as'] == 'positive'
        assert operation['zero_digit_ASCII_codes'] == [0,32]
        assert operation['string_terminator_stops_conversion'] is False
        assert len(operation['character_positions']) == 10
    gate = next(g.value for g in groups if g.dimension == 'execution.operation_condition')
    assert gate['enabled_when'] == ('execution_condition_rising_edge' if opcode.endswith('P') else 'execution_condition_ON'), (cpu, opcode, gate)
    assert analyze_instruction_access(opcode,args,plc_model=cpu) == ([f'D{i}' for i in range(words)],['D100','D101'])
    for ir,result in _public(cpu,opcode,args):
        assert result['valid'] is True, (cpu,opcode,args,result)
        assert ir['networks'][0]['writes'] == ['D100','D101'], (cpu,opcode,ir)


@pytest.mark.parametrize('opcode', OPS)
def test_QL_double_ascii_source_and_destination_span_boundaries(monkeypatch,opcode):
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch,_registry('Q03UDV',opcode))
    maximum=DEVICE_LIMITS['Q03UDV']['D']
    words=4 if opcode.startswith('DDABCD') else 6
    for _,result in _public('Q03UDV',opcode,[f'D{maximum-words+1}',f'D{maximum-1}']):
        assert result['valid'] is True,(opcode,maximum,result)
    for args in [[f'D{maximum-words+2}','D100'],['D0',f'D{maximum}']]:
        for _,result in _public('Q03UDV',opcode,args):
            assert result['valid'] is False,(opcode,args,result)


@pytest.mark.parametrize('opcode', OPS)
def test_QL_double_ascii_exact_literals_and_own_slot_namespaces(monkeypatch,opcode):
    from plc.ir import analyze_instruction_access
    _bind(monkeypatch,_registry('Q03UDV',opcode))
    literal='"87654321"' if opcode.startswith('DDABCD') else '"-1234543210"'
    for ir,result in _public('Q03UDV',opcode,[literal,'D100']):
        assert result['valid'] is True,(opcode,literal,result)
        assert ir['networks'][0]['reads'] == ['X0'],(opcode,ir)
    for args in [['M0','D100'],['Z0','D100'],['K1','D100'],[r'U0\G0','D100'],[r'J1\W0','D100'],['D0','K0']]:
        for _,result in _public('Q03UDV',opcode,args):
            assert result['valid'] is False,(opcode,args,result)
    for destination in ['K4M100','K8M100','Z0',r'U0\G100',r'J1\W100']:
        for path_index,(_,result) in enumerate(_public('Q03UDV',opcode,['D0',destination])):
            if path_index == 0 and destination.startswith(('U','J')):
                assert result['valid'] is False and result['status'] == 'unverified' and result['counts']['error'] == 0,(opcode,destination,result)
                code='SOURCE_BUFFER_MEMORY_SEMANTICS_UNVERIFIED' if destination.startswith('U') else 'SOURCE_DEVICE_PROFILE_INCOMPLETE'
                assert any(f['code'] == code for f in result['findings']),(opcode,destination,result)
            else:
                assert result['valid'] is True,(opcode,destination,result)
    assert analyze_instruction_access(opcode,['D0','K4M100'],plc_model='Q03UDV')[1] == [f'M{i}' for i in range(100,116)]


@pytest.mark.parametrize('opcode', OPS)
def test_QL_double_ascii_existing_q_descriptor_and_source_csv(monkeypatch,tmp_path,opcode):
    _bind(monkeypatch,_registry('Q03UDV',opcode))
    _codec_csv_roundtrip(tmp_path,opcode,'Q03UDV','q03udv')


@pytest.mark.parametrize('cpu', ['FX3U','FX3UC'])
@pytest.mark.parametrize('opcode', ['DDABIN','DDABINP'])
def test_FX_double_ascii_own_six_word_source_and_signed_result(monkeypatch,cpu,opcode):
    from plc.ir import analyze_instruction_access
    from plc.instructions import reviewed_native_operand_parameters, reviewed_native_fact_groups
    registry=_registry(cpu,opcode)
    _bind(monkeypatch,registry)
    args=['D0','D100']
    spec=registry.resolve(opcode,cpu=cpu,operand_count=2)
    parameters=reviewed_native_operand_parameters(spec,opcode,args,cpu)
    source=next(p for p in parameters[0] if p.get('word_device_layout',{}).get('element_count')==11)
    assert source['word_device_layout']['word_count']==6 and source['word_device_layout']['ignored_byte_offsets']==[11]
    destination=next(p for p in parameters[1] if p.get('word_device_layout',{}).get('value_bits')==32)
    assert destination['word_device_layout']['signed'] is True and destination['word_device_layout']['word_count']==2
    groups=reviewed_native_fact_groups(spec,opcode,args,cpu,dimensions={'effects.documented_processing','execution.operation_condition'})
    operation=next(g.value for g in groups if g.dimension=='effects.documented_processing')
    assert operation['ignored_source_byte']==dict(source_word_offset=5,source_byte_offset=1)
    assert operation['other_sign_ASCII_codes_processed_as']=='operation_error'
    assert operation['full_source_relation_complete'] is False and operation['Core_execution_verified'] is False and operation['Native_execution_verified'] is False
    gate=next(g.value for g in groups if g.dimension=='execution.operation_condition')
    assert gate['condition']==('rising_edge' if opcode.endswith('P') else 'execution_condition_ON'),(cpu,opcode,gate)
    assert analyze_instruction_access(opcode,args,plc_model=cpu)==([f'D{i}' for i in range(6)],['D100','D101'])
    for _,result in _public(cpu,opcode,args,{'cpu_version':'2.20'}):
        assert result['valid'] is True,(cpu,opcode,args,result)
    for _,result in _public(cpu,opcode,args,{'cpu_version':'2.19'}):
        assert result['valid'] is False,(cpu,opcode,'2.19',result)
    for args in [['"-1234543210"','D100'],['K4M0','D100'],[r'U0\G0','D100'],['V0','D100'],['D0','K4X100']]:
        for _,result in _public(cpu,opcode,args,{'cpu_version':'2.20'}):
            assert result['valid'] is False,(cpu,opcode,args,result)
    for destination in ['V0','Z0','K4M100','K8Y100']:
        for _,result in _public(cpu,opcode,['D0',destination],{'cpu_version':'2.20'}):
            assert result['valid'] is True,(cpu,opcode,destination,result)


def test_FX_double_ascii_C32_source_has_no_fabricated_six_counter_span(monkeypatch):
    from plc.device_identity import instruction_operand_regions
    from plc.ir import analyze_instruction_access
    registry=_registry('FX3U','DDABIN')
    _bind(monkeypatch,registry)
    spec=registry.resolve('DDABIN',cpu='FX3U',operand_count=2)
    # The six 16-bit words must all have the source-established physical unit.
    regions=instruction_operand_regions(spec,'DDABIN',['C194','D100'],'FX3U')
    source=[r for r in regions if r['operand_index']==0]
    expected=[f'C{i}' for i in range(194,200)]
    assert source and all(r.get('resolved_count')==6 and r.get('addresses')==expected for r in source),('C194',source)
    reads,_=analyze_instruction_access('DDABIN',['C194','D100'],plc_model='FX3U')
    assert reads==expected,('C194',reads)
    for head in ['C195','C235']:
        regions=instruction_operand_regions(spec,'DDABIN',[head,'D100'],'FX3U')
        source=[r for r in regions if r['operand_index']==0]
        assert source and all('resolved_count' not in r and 'addresses' not in r and r.get('requires') for r in source),(head,source)
        reads,_=analyze_instruction_access('DDABIN',[head,'D100'],plc_model='FX3U')
        assert reads==[head],(head,reads)


@pytest.mark.parametrize('opcode',['DDABIN','DDABINP'])
def test_FX_double_ascii_existing_descriptor_and_source_csv(monkeypatch,tmp_path,opcode):
    _bind(monkeypatch,_registry('FX3U',opcode))
    _codec_csv_roundtrip(tmp_path,opcode,'FX3U','fx3u')
