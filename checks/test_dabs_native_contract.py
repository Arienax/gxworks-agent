"""Servo ABS transfer and drum table storage; no mathematical ABS or execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_DABS_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / 'ql-fx-dabs-native-review'
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



from checks.test_ascii_conversion_native_contract import _bind
from checks.test_ddrd_ddwr_native_contract import _rungs


@pytest.mark.parametrize('cpu', ['FX3U', 'FX2N'])
def test_dabs_own_servo_three_input_three_output_bits_and_position_words(monkeypatch, cpu):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    registry = _registry(cpu, 'DABS')
    _bind(monkeypatch, registry)
    form = registry.resolve_catalog_form('DABS', cpu=cpu)
    assert form.opcode == form.base_mnemonic == 'DABS' and not form.double and not form.pulse
    assert form.spec.native_operand_order == ('S', 'D1', 'D2')
    assert [o.data_type for o in form.spec.operands] == ['bit', 'bit', 'dword']
    args = ['X100', 'Y100', 'D1000']
    assert analyze_instruction_access('DABS', args, plc_model=cpu) == (
        ['X100', 'X101', 'X102'], ['Y100', 'Y101', 'Y102', 'D1000', 'D1001'])
    for rung in _rungs('DABS', args):
        validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))


def test_dabsd_own_four_n_words_and_n_output_bits(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    registry = _registry('FX3U', 'DABSD')
    _bind(monkeypatch, registry)
    args = ['D100', 'C200', 'M100', 'K2']
    reads, writes = analyze_instruction_access('DABSD', args, plc_model='FX3U')
    assert set(reads) == {f'D{i}' for i in range(100, 108)} | {'C200'}
    assert set(writes) == {'M100', 'M101'}
    for rung in _rungs('DABSD', args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U')
        validate_plc_ir(json.loads(json.dumps(ir)))
        table = next(r for r in ir['networks'][0]['access_regions'] if r['operand_index'] == 0)
        assert table['unit'] == 'words' and table['resolved_count'] == 8
        assert table['addresses'] == [f'D{i}' for i in range(100, 108)]



def test_dabsd_dynamic_word_and_bit_extents_obey_cpu_boundaries(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch, _registry('FX3U', 'DABSD'))
    limits = DEVICE_LIMITS['FX3U']
    for source, destination, fits in [(f'D{limits["D"] - 7}', f'Y{limits["Y"] - 1:o}', True),
                                      (f'D{limits["D"] - 6}', 'M100', False),
                                      ('D100', f'Y{limits["Y"]:o}', False)]:
        for rung in _rungs('DABSD', [source, 'C235', destination, 'K2']):
            if fits:
                ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U')
                validate_plc_ir(ir)
                assert 'C235' in ir['networks'][0]['reads'] and 'C236' not in ir['networks'][0]['reads']
            else:
                with pytest.raises(ValueError, match='memory extent exceeds'):
                    validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U'))
    for count, words in [('K1', 4), ('H40', 256)]:
        for rung in _rungs('DABSD', ['D100', 'C235', 'M100', count]):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U')
            validate_plc_ir(ir)
            table = next(r for r in ir['networks'][0]['access_regions'] if r['operand_index'] == 0)
            assert table['resolved_count'] == words
    for count in ['K0', 'K65', 'D50']:
        for rung in _rungs('DABSD', ['D100', 'C235', 'M100', count]):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U'))


def test_dabsd_unknown_count_stays_symbolic_without_authorizing_device_n(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    _bind(monkeypatch, _registry('FX3U', 'DABSD'))
    rung = _rungs('DABSD', ['D100', 'C235', 'M100', 'D50'])[0]
    ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U')
    table = next(r for r in ir['networks'][0]['access_regions'] if r['operand_index'] == 0)
    assert table['count']['op'] == 'mul' and 'resolved_count' not in table
    assert 'parameter_value:n' in table['requires']
    assert {'D50', 'D100'} <= set(ir['networks'][0]['reads'])
    assert 'D101' not in ir['networks'][0]['reads']
    with pytest.raises(ValueError):
        validate_plc_ir(ir)


def test_dabsd_span_requires_reviewed_complete_parameter_shape():
    from dataclasses import replace
    from plc.device_identity import instruction_operand_regions
    from plc.instructions import reviewed_native_operand_parameters
    registry = _registry('FX3U', 'DABSD')
    args = ['D100', 'C235', 'M100', 'K2']
    spec = registry.resolve('DABSD', cpu='FX3U', operand_count=4)
    unreviewed = replace(spec, definition_facts=tuple(
        replace(g, status='candidate_evidence') if g.dimension == 'parameters.typed_operands' else g
        for g in spec.definition_facts))
    assert instruction_operand_regions(unreviewed, 'DABSD', args, 'FX3U') == []
    parameters = copy.deepcopy(reviewed_native_operand_parameters(spec, 'DABSD', args, 'FX3U'))
    for parameter in parameters[0]:
        parameter['region_span'].pop('count_expression')
    table = next(r for r in instruction_operand_regions(spec, 'DABSD', args, 'FX3U', parameters=parameters)
                 if r['operand_index'] == 0)
    assert table['count'] is None and 'addresses' not in table
    assert 'source_region_span_not_resolved' in table['requires']


@pytest.mark.parametrize('opcode,args', [('DABS', ['X100', 'Y100', 'D1000']),
                                        ('DABSD', ['D100', 'C235', 'M100', 'K2'])])
def test_dabs_family_existing_fx_codec_and_source_csv_roundtrip(monkeypatch, tmp_path, opcode, args):
    import csv
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxworks2.csv_manager import GXWORKS2_HEADER, GXWORKS2_COMMENT_HEADER
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from checks.test_chk_codec import _ir_sequence
    from plc.ir import validate_plc_ir
    _bind(monkeypatch, _registry('FX3U', opcode))
    raw = encode_instruction(opcode, args, profile='fx3u', plc_model='FX3U', text_encoding='cp932')
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile='fx3u', text_encoding='cp932')
    assert not listing.gaps
    assert [(r.mnemonic, list(r.args)) for r in listing.instructions] == [(opcode, args)]
    assert listing.reconstruct() == raw
    program, comments = tmp_path / 'MAIN.csv', tmp_path / 'COMMENT.csv'
    rows = [['MAIN'], ['PLC信息:', 'FX3U'], GXWORKS2_HEADER,
            ['', '', 'LD', 'X0', '', '', ''], ['', '', opcode, args[0], '', '', '']]
    rows.extend(['', '', '', operand, '', '', ''] for operand in args[1:])
    rows.append(['', '', 'END', '', '', '', ''])
    with program.open('w', encoding='utf-16', newline='') as out:
        csv.writer(out, delimiter='\t', lineterminator='\r\n').writerows(rows)
    with comments.open('w', encoding='utf-16', newline='') as out:
        csv.writer(out, delimiter='\t', lineterminator='\r\n').writerows([['COMMENT'], GXWORKS2_COMMENT_HEADER])
    source = inspect_gxworks2_csv(program, comments, plc_model='FX3U', program_name='MAIN')
    assert source['program_ir'] is not None, source['coverage']
    target, target_comments = tmp_path / 'saved.csv', tmp_path / 'saved_comments.csv'
    write_gxworks2_csv_from_source(source, target, target_comments, plc_model='FX3U')
    reread = inspect_gxworks2_csv(target, target_comments, plc_model='FX3U', program_name='MAIN')
    for snapshot in (source, reread):
        assert _ir_sequence(snapshot['program_ir']) == [('LD', ['X0']), (opcode, args), ('END', [])]
        validate_plc_ir(snapshot['program_ir'])
        assert snapshot['coverage']['runtime_verified'] is False


def test_dabs_own_bit_namespaces_and_word_destination(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    _bind(monkeypatch, _registry('FX3U', 'DABS'))
    for args in [['D100', 'Y100', 'D1000'], ['X100', 'X100', 'D1000'], ['X100', 'Y100', 'K1']]:
        for rung in _rungs('DABS', args):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U'))


def test_dabsd_own_digit_table_is_K8_aligned_and_keeps_group_sequence_unknown(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    _bind(monkeypatch, _registry('FX3U', 'DABSD'))
    for rung in _rungs('DABSD', ['K8M0', 'C235', 'M100', 'K2']):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U')
        validate_plc_ir(ir)
        table = next(r for r in ir['networks'][0]['access_regions'] if r['operand_index'] == 0)
        assert table['resolved_count'] == 8 and 'addresses' not in table
        assert 'digit_group_sequence_layout' in table['requires']
    for source in ['K4M0', 'K8M1']:
        for rung in _rungs('DABSD', [source, 'C235', 'M100', 'K2']):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='FX3U'))
