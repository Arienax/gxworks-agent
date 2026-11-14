"""Own-source doubleword block-subtraction consumers; no algorithm execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_DBKM_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / ('fx-dbkm-native-contract-repair' if cpu.startswith('FX') else 'ql-dbkm-native-contract-repair')
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


@pytest.mark.parametrize('cpu,profile', [('Q03UDV', 'q03udv'), ('FX3U', 'fx3u')])
@pytest.mark.parametrize('opcode', ['DBK-', 'DBK-P'])
def test_dbkm_existing_descriptor_and_source_CSV(monkeypatch, tmp_path, cpu, profile, opcode):
    import csv
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxworks2.csv_manager import GXWORKS2_HEADER, GXWORKS2_COMMENT_HEADER
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from checks.test_chk_codec import _ir_sequence
    from plc.ir import validate_plc_ir
    from plc.instruction_steps import instruction_step_width
    _bind(monkeypatch, _registry(cpu, opcode))
    args = ['D0', 'D20', 'D100', 'K3']
    raw = encode_instruction(opcode, args, profile=profile, plc_model=cpu, text_encoding='cp932')
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile=profile, text_encoding='cp932')
    assert not listing.gaps
    assert [(r.mnemonic, list(r.args)) for r in listing.instructions] == [(opcode, args)]
    assert listing.reconstruct() == raw
    if cpu.startswith('FX'):
        assert instruction_step_width(opcode, args, plc_model=cpu).steps == 17
        assert listing.instructions[0].step_width == 17  # Own R606/R608.
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



def _confirmed(cpu):
    return {'cpu_version': '2.20'} if cpu.startswith('FX') else {'cpu_serial_first_five': 10102}


def _constraint_report(registry, cpu, opcode, args):
    from plc.instruction_constraints import literal_operand_values, check_instruction_constraints
    spec = registry.resolve(opcode, cpu=cpu, operand_count=len(args))
    values = literal_operand_values(spec, args, plc_model=cpu, opcode=opcode)
    context = {'project_mode': 'native_ladder_instruction', **_confirmed(cpu)}
    return check_instruction_constraints(opcode, values, plc_model=cpu, language='ladder',
                                        context=context, registry=registry)


def test_QL_dbkm_own_identical_region_conflict_is_unverified_not_a_ban(monkeypatch):
    cpu, opcode = 'Q03UDV', 'DBK-'
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    facts = [f for f in registry.definition_entry(opcode, cpu=cpu)['facts']
             if f['dimension'] == 'constraints.disjoint_regions'
             and isinstance(f['value'].get('identical_region_source_conflict'), dict)]
    assert len(facts) == 2, (cpu, opcode, facts)
    for index, destination in [(0, 'D0'), (1, 'D20')]:
        fact = next(f for f in facts if f['value']['source_operand_index'] == index)
        assert fact['value'].get('allow_identical') is not True, (cpu, opcode, fact)
        report = _constraint_report(registry, cpu, opcode, ['D0', 'D20', destination, 'K3'])
        item = next(item for item in report['checks'] if item['fact_id'] == fact['id'])
        assert item['status'] == 'unverified', (cpu, opcode, destination, item, report)

    for index, destination in [(0, 'D0'), (1, 'D20')]:
        fact = next(f for f in facts if f['value']['source_operand_index'] == index)
        report = _constraint_report(registry, cpu, opcode, ['D0', 'D20', destination, 'K0'])
        item = next(item for item in report['checks'] if item['fact_id'] == fact['id'])
        assert item['status'] == 'passed', (cpu, opcode, destination, 'K0', item, report)


@pytest.mark.parametrize('cpu', ['Q00U', 'Q03UD', 'Q03UDV', 'L02', 'FX3U', 'FX3UC'])
@pytest.mark.parametrize('opcode', ['DBK-', 'DBK-P'])
def test_dbkm_own_n_elements_use_two_words_and_public_gate(monkeypatch, cpu, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import reviewed_native_fact_groups
    from plc.core import PLCCore
    from application.projects import public
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    args = ['D0', 'D20', 'D100', 'K3']
    spec = registry.resolve(opcode, cpu=cpu, operand_count=4)
    assert spec.native_operand_order == ('S1', 'S2', 'D', 'n'), (cpu, opcode, spec.native_operand_order)
    gate = next(g.value for g in reviewed_native_fact_groups(spec, opcode, args, cpu,
                dimensions={'execution.operation_condition'}))
    expected_gate = 'execution_condition_rising_edge' if opcode.endswith('P') else 'execution_condition_ON'
    if cpu.startswith('FX'):
        assert gate['condition'] == ('rising_edge' if opcode.endswith('P') else 'execution_condition_ON')
    else:
        assert gate['enabled_when'] == expected_gate
        assert gate['core_execution_established'] is False
    reads, writes = analyze_instruction_access(opcode, args, plc_model=cpu)
    assert set(reads) == {f'D{i}' for i in range(6)} | {f'D{i}' for i in range(20, 26)}
    assert set(writes) == {f'D{i}' for i in range(100, 106)}
    confirmed = {'cpu_version': '2.20'} if cpu.startswith('FX') else {'cpu_serial_first_five': 10102}
    for rung in _rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        validate_plc_ir(ir)
        result = public(PLCCore().validate_project(ir, confirmed_spec=confirmed))
        assert result.get('valid') is True, (cpu, opcode, result)
        low = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': '2.19'} if cpu.startswith('FX') else {'cpu_serial_first_five': 10101}))
        if cpu.startswith('FX') or cpu == 'Q03UD':
            assert low.get('valid') is False and low.get('counts', {}).get('error', 0) > 0, (cpu, opcode, low)
        else:
            assert low.get('valid') is True, (cpu, opcode, low)


@pytest.mark.parametrize('cpu', ['Q03UDV', 'FX3U'])
def test_dbkm_repeated_S2_constants_keep_signed_32bit_element_values(monkeypatch, cpu):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instruction_constraints import literal_operand_values
    from plc.core import PLCCore
    from application.projects import public
    registry = _registry(cpu, 'DBK-')
    _bind(monkeypatch, registry)
    spec = registry.resolve('DBK-', cpu=cpu, operand_count=4)
    for literal, expected in [('HFFFFFFFF', -1), ('H80000000', -2147483648), ('K-1', -1), ('K-2147483648', -2147483648)]:
        args = ['D0', literal, 'D100', 'K3']
        assert literal_operand_values(spec, args, plc_model=cpu, opcode='DBK-')['S2'] == expected
        from plc.instruction_constraints import check_instruction_constraints
        values = literal_operand_values(spec, args, plc_model=cpu, opcode='DBK-')
        values.update({'$OPERANDS': args, '$1': expected, '$3': 3})
        context = {'project_mode': 'native_ladder_instruction', **_confirmed(cpu)}
        if cpu.startswith('FX'): context['cpu_version'] = '2.20'
        report = check_instruction_constraints('DBK-', values, plc_model=cpu, language='ladder',
                 context=context, registry=registry)
        root_name = 'dbkm_disjoint_s2' if cpu.startswith('FX') else 'dbkm_overlap_s2'
        overlap = next(item for item in report['checks'] if root_name in item['fact_id'])
        assert overlap['status'] == 'not_applicable', overlap
        assert analyze_instruction_access('DBK-', args, plc_model=cpu) == ([f'D{i}' for i in range(6)], [f'D{i}' for i in range(100, 106)])
        for rung in _rungs('DBK-', args):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
            validate_plc_ir(ir)
            result = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': '2.20'} if cpu.startswith('FX') else None))
            assert result.get('valid') is True, (cpu, 'DBK-', result)


@pytest.mark.parametrize('cpu', ['Q03UDV', 'FX3U'])
def test_dbkm_device_n_keeps_unknown_extent_and_own_count_width(monkeypatch, cpu):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    _bind(monkeypatch, _registry(cpu, 'DBK-'))
    args = ['D0', 'D20', 'D100', 'D200']
    reads, writes = analyze_instruction_access('DBK-', args, plc_model=cpu)
    assert 'D200' in reads
    assert ('D201' in reads) is cpu.startswith('FX')
    # Head fallback is conservative; no runtime n value is supplied.
    assert not any(f'D{i}' in writes for i in range(102, 106))
    for rung in _rungs('DBK-', args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        validate_plc_ir(ir)
        spans = [r for r in ir['networks'][0]['access_regions'] if r['operand_index'] in {0, 1, 2}]
        assert {r['operand_index'] for r in spans} == {0, 1, 2}
        assert all(isinstance(r['count'], dict) and 'resolved_count' not in r and 'addresses' not in r for r in spans)
        assert all('parameter_value:n' in r['requires'] for r in spans)


def test_QL_dbkm_partial_overlap_disjoint_bounds_and_zero_data_span(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.device_profiles import DEVICE_LIMITS
    from plc.core import PLCCore
    from application.projects import public
    cpu, opcode = 'Q03UDV', 'DBK-'
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    ids = {f['value']['source_operand_index']: f['id'] for f in registry.definition_entry(opcode, cpu=cpu)['facts']
           if f['dimension'] == 'constraints.disjoint_regions' and 'identical_region_source_conflict' in f['value']}
    for destination, index in [('D1', 0), ('D21', 1)]:
        args = ['D0', 'D20', destination, 'K3']
        report = _constraint_report(registry, cpu, opcode, args)
        item = next(c for c in report['checks'] if c['fact_id'] == ids[index])
        assert item['status'] == 'failed', (cpu, opcode, args, item, report)
        for rung in _rungs(opcode, args):
            result = public(PLCCore().validate_project(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu), confirmed_spec=_confirmed(cpu)))
            assert result.get('valid') is False and result.get('counts', {}).get('error', 0) > 0, (cpu, opcode, args, result)
    report = _constraint_report(registry, cpu, opcode, ['D0', 'D20', 'D100', 'K3'])
    assert all(c['status'] == 'passed' for c in report['checks'] if c['fact_id'] in ids.values()), (cpu, opcode, report)
    maximum = DEVICE_LIMITS[cpu]['D']
    for index in [0, 2]:
        args = ['D0', 'K-1', 'D100', 'K3']
        args[index] = f'D{maximum - 5}'
        for rung in _rungs(opcode, args):
            validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))
        args[index] = f'D{maximum - 4}'
        for rung in _rungs(opcode, args):
            with pytest.raises(ValueError): validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))
    assert analyze_instruction_access(opcode, ['D0', 'D20', 'D0', 'K0'], plc_model=cpu) == ([], []), (cpu, opcode)


def test_FX_dbkm_own_same_head_and_partial_overlap_are_rejected(monkeypatch):
    cpu = 'FX3U'
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch, _registry(cpu, 'DBK-'))
    for destination in ['D0', 'D20']:
        for rung in _rungs('DBK-', ['D0', 'D20', destination, 'K3']):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
            if cpu.startswith('FX'):
                from plc.core import PLCCore
                from application.projects import public
                result = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': '2.20'}))
                assert result.get('valid') is False and result.get('counts', {}).get('error', 0) > 0, result
            else:
                validate_plc_ir(ir)
    for destination in ['D1', 'D21']:
        for rung in _rungs('DBK-', ['D0', 'D20', destination, 'K3']):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
            if cpu.startswith('FX'):
                result = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': '2.20'}))
                assert result.get('valid') is False and result.get('counts', {}).get('error', 0) > 0, result
            else:
                with pytest.raises(ValueError): validate_plc_ir(ir)
    maximum = DEVICE_LIMITS[cpu]['D']
    for index in [0, 2]:
        args = ['D0', 'K-1', 'D100', 'K3']
        args[index] = f'D{maximum - 5}'
        for rung in _rungs('DBK-', args):
            validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))
        args[index] = f'D{maximum - 4}'
        for rung in _rungs('DBK-', args):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))


def test_FX_dbkm_C235_payload_unit_stays_unknown_without_losing_32bit_n(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    registry = _registry('FX3U', 'DBK-')
    _bind(monkeypatch, registry)
    assert registry.definition_entry('DBK-', cpu='FX3U')['call_available'] is True
    for count in ['K3', 'D200']:
        args = ['C235', 'D20', 'D100', count]
        reads, _ = analyze_instruction_access('DBK-', args, plc_model='FX3U')
        assert 'C235' in reads and not ({f'C{i}' for i in range(236, 241)} & set(reads)), reads
        if count == 'D200': assert {'D200', 'D201'} <= set(reads)
        for rung in _rungs('DBK-', args):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U')
            validate_plc_ir(ir)
            region = next(r for r in ir['networks'][0]['access_regions'] if r['operand_index'] == 0)
            assert 'addresses' not in region and 'physical_bit_region' not in region
            assert 'resolved_count' not in region and region['requires']
            assert region['source_layout']['device_unit_bits'] == 16
