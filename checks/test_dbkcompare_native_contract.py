"""Own-source doubleword block-comparison consumers; no algorithm execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_DBKCOMPARE_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / 'ql-dbkcompare-native-contract-repair'
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



def _confirmed(cpu):
    return {'cpu_serial_first_five': 10102}


def _constraint_report(registry, cpu, opcode, args):
    from plc.instruction_constraints import literal_operand_values, check_instruction_constraints
    spec = registry.resolve(opcode, cpu=cpu, operand_count=len(args))
    return check_instruction_constraints(opcode,
        literal_operand_values(spec, args, plc_model=cpu, opcode=opcode),
        plc_model=cpu, language='ladder',
        context={'project_mode': 'native_ladder_instruction', **_confirmed(cpu)}, registry=registry)



def test_dbkcompare_own_doubleword_words_to_bits_overlap_is_a_known_failure(monkeypatch):
    from plc.device_identity import instruction_operand_regions
    cpu, opcode = 'Q03UDV', 'DBKCMP='
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    args = ['D0', 'D100', 'D1.4', 'K2']
    spec = registry.resolve(opcode, cpu=cpu, operand_count=4)
    facts = [f for f in registry.definition_entry(opcode, cpu=cpu)['facts']
             if f['dimension'] == 'constraints.disjoint_regions'
             and f['value'].get('source_operand_index') == 0
             and f['value'].get('source_unit') == 'words'
             and f['value'].get('destination_unit') == 'bits'
             and f['value'].get('source_length_multiplier') == 2]
    assert len(facts) == 1, (cpu, opcode, facts)
    regions = instruction_operand_regions(spec, opcode, args, cpu)
    source = next(r for r in regions if r['operand_index'] == 0)
    destination = next(r for r in regions if r['operand_index'] == 2)
    assert source['resolved_count'] == 4, (cpu, opcode, source)
    assert source['physical_bit_region'] == dict(address_space='D', start_bit=0, bit_count=64), (cpu, opcode, source)
    assert destination['physical_bit_region'] == dict(address_space='D', start_bit=20, bit_count=2), (cpu, opcode, destination)
    report = _constraint_report(registry, cpu, opcode, args)
    item = next(item for item in report['checks'] if item['fact_id'] == facts[0]['id'])
    assert item['status'] == 'failed', (cpu, opcode, args, item, report)


OPS = tuple('DBKCMP' + relation + pulse for relation in ('<', '<=', '<>', '=', '>', '>=') for pulse in ('', 'P'))


@pytest.mark.parametrize('opcode', OPS)
def test_dbkcompare_own_literal_public_doubleword_inputs_bit_outputs_and_gate(monkeypatch, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import reviewed_native_fact_groups, reviewed_native_operand_parameters
    from plc.core import PLCCore
    from application.projects import public
    cpu = 'Q03UDV'
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    args = ['D0', 'D100', 'M0', 'K3']
    spec = registry.resolve(opcode, cpu=cpu, operand_count=4)
    assert spec.native_operand_order == ('S1', 'S2', 'D', 'n'), (cpu, opcode, spec.native_operand_order)
    pins = reviewed_native_operand_parameters(spec, opcode, args, cpu)
    assert all(any(p.get('word_device_layout', {}).get('value_bits') == 32 for p in pins[i]) for i in (0, 1)), (cpu, opcode, pins)
    gate = next(g.value for g in reviewed_native_fact_groups(spec, opcode, args, cpu, dimensions={'execution.operation_condition'}))
    assert gate['enabled_when'] == ('execution_condition_rising_edge' if opcode.endswith('P') else 'execution_condition_ON'), (cpu, opcode, gate)
    operation = next(g.value for g in reviewed_native_fact_groups(spec, opcode, args, cpu, dimensions={'effects.operation'}) if g.value.get('source_element_relation_complete') is True)
    assert operation['core_execution_established'] is False, (cpu, opcode, operation)
    reads, writes = analyze_instruction_access(opcode, args, plc_model=cpu)
    assert set(reads) == {f'D{i}' for i in range(6)} | {f'D{i}' for i in range(100, 106)}, (cpu, opcode, reads)
    assert set(writes) == {'M0', 'M1', 'M2'}, (cpu, opcode, writes)
    for rung in _rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        validate_plc_ir(ir)
        result = public(PLCCore().validate_project(ir, confirmed_spec=_confirmed(cpu)))
        assert result.get('valid') is True, (cpu, opcode, result)


@pytest.mark.parametrize('cpu', ['Q00U', 'Q03UD', 'L02'])
def test_dbkcompare_own_CPU_serial_context_in_public_paths(monkeypatch, cpu):
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    opcode = 'DBKCMP='
    _bind(monkeypatch, _registry(cpu, opcode))
    for rung in _rungs(opcode, ['D0', 'D100', 'M0', 'K2']):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        accepted = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_serial_first_five': 10102}))
        assert accepted.get('valid') is True, (cpu, opcode, accepted)
        low = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_serial_first_five': 10101}))
        if cpu == 'Q03UD':
            assert low.get('valid') is False and low.get('counts', {}).get('error', 0) > 0, (cpu, opcode, low)
        else:
            assert low.get('valid') is True, (cpu, opcode, low)


def test_dbkcompare_own_S1_repeated_signed_constant_and_wrong_slot_rejections(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instruction_constraints import literal_operand_values
    from plc.core import PLCCore
    from application.projects import public
    cpu, opcode = 'Q03UDV', 'DBKCMP='
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    spec = registry.resolve(opcode, cpu=cpu, operand_count=4)
    for literal, expected in [('HFFFFFFFF', -1), ('H80000000', -2147483648), ('K-1', -1), ('K-2147483648', -2147483648)]:
        args = [literal, 'D100', 'D20.4', 'K2']
        assert literal_operand_values(spec, args, plc_model=cpu, opcode=opcode)['S1'] == expected, (cpu, opcode, args)
        report = _constraint_report(registry, cpu, opcode, args)
        source0 = next(c for c in report['checks'] if 'dbkcompare_overlap_s1' in c['fact_id'])
        assert source0['status'] == 'not_applicable', (cpu, opcode, args, source0, report)
        reads, writes = analyze_instruction_access(opcode, args, plc_model=cpu)
        assert set(reads) == {'D100', 'D101', 'D102', 'D103'}, (cpu, opcode, args, reads)
        assert set(writes) == {'D20.4', 'D20.5'}, (cpu, opcode, args, writes)
        for rung in _rungs(opcode, args):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
            result = public(PLCCore().validate_project(ir, confirmed_spec=_confirmed(cpu)))
            assert result.get('valid') is True, (cpu, opcode, args, result)
    for args in [['D0', 'K1', 'M0', 'K2'], ['K2147483648', 'D100', 'M0', 'K2'],
                 ['D0', 'D100', 'M0', 'K-1']]:
        for rung in _rungs(opcode, args):
            with pytest.raises(ValueError): validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))


    for rung in _rungs(opcode, ['D0', 'D100', 'D20', 'K2']):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        result = public(PLCCore().validate_project(ir, confirmed_spec=_confirmed(cpu)))
        assert result.get('valid') is False and result.get('counts', {}).get('error', 0) > 0, (cpu, opcode, result)
        message = result.get('error', '').lower()
        assert 'bit' in message and 'designation' in message, (cpu, opcode, result)


def test_dbkcompare_mixed_physical_overlap_second_half_and_unknown_layouts(monkeypatch):
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    cpu, opcode = 'Q03UDV', 'DBKCMP='
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    for args in [['D0', 'D100', 'D3.4', 'K2'], ['D100', 'D0', 'D3.4', 'K2'], ['D0', 'D100', 'D0.0', 'K1']]:
        report = _constraint_report(registry, cpu, opcode, args)
        own = [c for c in report['checks'] if 'dbkcompare_overlap_' in c['fact_id']]
        assert any(c['status'] == 'failed' for c in own), (cpu, opcode, args, own, report)
        for rung in _rungs(opcode, args):
            result = public(PLCCore().validate_project(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu), confirmed_spec=_confirmed(cpu)))
            assert result.get('valid') is False and result.get('counts', {}).get('error', 0) > 0, (cpu, opcode, args, result)
    for args in [['D0', 'D100', 'D4.0', 'K2'], ['K-1', 'D100', 'D0.4', 'K2']]:
        report = _constraint_report(registry, cpu, opcode, args)
        own = [c for c in report['checks'] if 'dbkcompare_overlap_' in c['fact_id']]
        assert all(c['status'] in {'passed', 'not_applicable'} for c in own), (cpu, opcode, args, own, report)
    for args in [['D0', 'D100', 'D0.F', 'K2'], ['D0Z0', 'D100', 'D0.4', 'K2'], ['D0', 'D100', 'M0', 'D300']]:
        report = _constraint_report(registry, cpu, opcode, args)
        item = next(c for c in report['checks'] if 'dbkcompare_overlap_s1' in c['fact_id'])
        assert item['status'] == 'unverified', (cpu, opcode, args, item, report)


def test_dbkcompare_own_input_output_boundaries_zero_and_device_n(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.device_profiles import DEVICE_LIMITS
    from plc.instructions import reviewed_native_operand_parameters
    cpu, opcode = 'Q03UDV', 'DBKCMP='
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    for index in (0, 1, 2):
        maximum = DEVICE_LIMITS[cpu]['M' if index == 2 else 'D']
        count = 2 if index == 2 else 4
        args = ['D0', 'D100', 'M0', 'K2']
        prefix = 'M' if index == 2 else 'D'
        args[index] = f'{prefix}{maximum - count + 1}'
        for rung in _rungs(opcode, args): validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))
        args[index] = f'{prefix}{maximum - count + 2}'
        for rung in _rungs(opcode, args):
            with pytest.raises(ValueError): validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))
    assert analyze_instruction_access(opcode, ['D0', 'D100', 'M0', 'K0'], plc_model=cpu) == ([], []), (cpu, opcode)
    args = ['D0', 'D100', 'M0', 'D300']
    reads, _ = analyze_instruction_access(opcode, args, plc_model=cpu)
    assert 'D300' in reads and 'D301' not in reads, (cpu, opcode, reads)
    ir = build_plc_ir(dict(device_comments={}, rungs=[_rungs(opcode, args)[0]]), plc_model=cpu)
    regions = ir['networks'][0]['access_regions']
    assert {r['operand_index'] for r in regions} == {0, 1, 2}, (cpu, opcode, regions)
    assert all('resolved_count' not in r and 'parameter_value:n' in r['requires'] for r in regions), (cpu, opcode, regions)
    spec = registry.resolve(opcode, cpu=cpu, operand_count=4)
    pins = reviewed_native_operand_parameters(spec, opcode, ['D0', 'D100', 'D20.4', 'K2'], cpu)
    assert any(p.get('other_word_device_bits') == 'unchanged' for p in pins[2]), (cpu, opcode, pins)


@pytest.mark.parametrize('opcode', OPS)
def test_dbkcompare_existing_q_descriptor_and_source_CSV(monkeypatch, tmp_path, opcode):
    cpu, profile = 'Q03UDV', 'q03udv'
    import csv
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxworks2.csv_manager import GXWORKS2_HEADER, GXWORKS2_COMMENT_HEADER
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from checks.test_chk_codec import _ir_sequence
    from plc.ir import validate_plc_ir
    _bind(monkeypatch, _registry(cpu, opcode))
    args = ['D0', 'D100', 'M0', 'K3']
    raw = encode_instruction(opcode, args, profile=profile, plc_model=cpu, text_encoding='cp932')
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile=profile, text_encoding='cp932')
    assert not listing.gaps
    assert [(r.mnemonic, list(r.args)) for r in listing.instructions] == [(opcode, args)]
    assert listing.reconstruct() == raw
    program, comments = tmp_path / 'MAIN.csv', tmp_path / 'COMMENT.csv'
    rows = [['MAIN'], ['PLC信息:', cpu], GXWORKS2_HEADER,
            ['', '', 'LD', 'X0', '', '', ''], ['', '', opcode, args[0], '', '', '']]
    rows.extend(['', '', '', operand, '', '', ''] for operand in args[1:])
    rows.append(['', '', 'END', '', '', '', ''])
    with program.open('w', encoding='utf-16', newline='') as out:
        csv.writer(out, delimiter='\t', lineterminator='\r\n').writerows(rows)
    with comments.open('w', encoding='utf-16', newline='') as out:
        csv.writer(out, delimiter='\t', lineterminator='\r\n').writerows([['COMMENT'], GXWORKS2_COMMENT_HEADER])
    source = inspect_gxworks2_csv(program, comments, plc_model=cpu, program_name='MAIN')
    assert source['program_ir'] is not None, source['coverage']
    target, target_comments = tmp_path / 'saved.csv', tmp_path / 'saved_comments.csv'
    write_gxworks2_csv_from_source(source, target, target_comments, plc_model=cpu)
    reread = inspect_gxworks2_csv(target, target_comments, plc_model=cpu, program_name='MAIN')
    for snapshot in (source, reread):
        assert _ir_sequence(snapshot['program_ir']) == [('LD', ['X0']), (opcode, args), ('END', [])]
        validate_plc_ir(snapshot['program_ir'])
        assert snapshot['coverage']['runtime_verified'] is False
