"""Own-source fixed ASCII operands and exact forms; no conversion execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_ASCII_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / ('ql-dabcdp-native-contract-repair' if opcode == 'DABCDP' else
                     'fx-dabin-native-contract-repair' if cpu.startswith('FX') else 'ql-dabin-native-contract-repair')
    registry = copy.copy(original)
    for name in ('_cpu_contracts', '_definition_contract_bases', '_definition_owners'):
        setattr(registry, name, dict(getattr(original, name)))
    registry._loaded_definition_contracts = set(original._loaded_definition_contracts)
    registry._clear_definition_contracts()
    contract_file = folder / 'contract-additions.json'
    if not contract_file.exists():
        contract_file = folder / 'ql-contract-additions.json'
    contracts = json.loads(contract_file.read_text(encoding='utf-8'))['cpu_contracts']
    if ('mitsubishi', opcode, cpu) not in registry._cpu_contracts:
        registry.load_target_contracts(contracts)
    source_file = folder / 'source-facts.json'
    if not source_file.exists():
        source_file = folder / 'ql-source-facts.json'
    source = next(e for e in json.loads(source_file.read_text(encoding='utf-8'))['entries']
                  if e['target_model'] == cpu and e['opcode'] == opcode)
    entry = copy.deepcopy(original.definition_entry(opcode, cpu=cpu))
    entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith(('authored:ql:', 'authored:applied:'))] + copy.deepcopy(source['facts'])
    entry['facts'], _ = compose_programming_facts(registry, opcode, cpu=cpu, existing=entry['facts'])
    entry['call_available'] = False
    assert refresh_reviewed_call_admission(entry, registry), (cpu, opcode)
    registry._definition_owners[('mitsubishi', opcode, cpu)] = entry
    return registry


def _bind(monkeypatch, registry):
    import plc.instructions
    import plc.ir
    import plc.validation
    for module in (plc.instructions, plc.ir, plc.validation):
        monkeypatch.setattr(module, 'DEFAULT_INSTRUCTION_REGISTRY', registry)


def _rungs(opcode, source='D100', destination='D200'):
    args = [source, destination]
    return [dict(rung_id=1, ordered_instructions=[dict(op='LD', args=['X0']),
                dict(op=opcode, args=args), dict(op='END', args=[])]),
            dict(rung_id=1, branches=[dict(branch_id=1, y_offset_level=0,
                inputs=[dict(type='NO', address='X0')],
                outputs=[dict(type='APP_INSTR', opcode=opcode, operands=args)])])]


def test_dabcdp_own_fixed_payload_and_rising_edge_metadata(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import reviewed_native_fact_groups
    registry = _registry('Q03UDV', 'DABCDP')
    _bind(monkeypatch, registry)
    form = registry.resolve_catalog_form('DABCDP', cpu='Q03UDV')
    assert form.opcode == form.base_mnemonic == 'DABCDP' and not form.double
    assert form.spec.execution_form == 'pulse' and 'execution_form' in form.spec.verified_fields
    spec = registry.resolve('DABCDP', cpu='Q03UDV', operand_count=2)
    gates = reviewed_native_fact_groups(spec, 'DABCDP', ['D100', 'D200'], 'Q03UDV',
                                        dimensions={'execution.operation_condition'})
    gate = next(g.value for g in gates if g.value.get('enabled_when') == 'execution_condition_rising_edge')
    assert gate['core_execution_established'] is False and gate['native_execution_verified'] is False
    assert analyze_instruction_access('DABCDP', ['D100', 'D200'], plc_model='Q03UDV') == (['D100', 'D101'], ['D200'])
    for rung in _rungs('DABCDP'):
        validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))
    for rung in _rungs('DABCDP', source='"8765"'):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
        validate_plc_ir(ir)
        assert ir['networks'][0]['reads'] == ['X0']


def test_dabcdp_own_fixed_source_range_and_literal_rejections(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch, _registry('Q03UDV', 'DABCDP'))
    maximum = DEVICE_LIMITS['Q03UDV']['D']
    for rung in _rungs('DABCDP', source=f'D{maximum - 1}'):
        validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))
    for source in [f'D{maximum}', 'M0', 'Z0', 'K1234', r'U0\G100']:
        for rung in _rungs('DABCDP', source=source):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))



def _codec_csv_roundtrip(tmp_path, opcode, cpu, profile):
    import csv
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxworks2.csv_manager import GXWORKS2_HEADER, GXWORKS2_COMMENT_HEADER
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from plc.ir import ir_to_ladder, validate_plc_ir
    args = ['D100', 'D200']
    raw = encode_instruction(opcode, args, profile=profile, plc_model=cpu, text_encoding='cp932')
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile=profile, text_encoding='cp932')
    assert not listing.gaps
    assert [(r.mnemonic, list(r.args)) for r in listing.instructions] == [(opcode, args)]
    assert listing.reconstruct() == raw
    program, comments = tmp_path / 'MAIN.csv', tmp_path / 'COMMENT.csv'
    with program.open('w', encoding='utf-16', newline='') as out:
        csv.writer(out, delimiter='\t', lineterminator='\r\n').writerows(
            [['MAIN'], ['PLC信息:', cpu], GXWORKS2_HEADER,
             ['', '', 'LD', 'X0', '', '', ''], ['', '', opcode, 'D100', '', '', ''],
             ['', '', '', 'D200', '', '', ''], ['', '', 'END', '', '', '', '']])
    with comments.open('w', encoding='utf-16', newline='') as out:
        csv.writer(out, delimiter='\t', lineterminator='\r\n').writerows([['COMMENT'], GXWORKS2_COMMENT_HEADER])
    source = inspect_gxworks2_csv(program, comments, plc_model=cpu, program_name='MAIN')
    assert source['program_ir'] is not None, source['coverage']
    target, target_comments = tmp_path / 'saved.csv', tmp_path / 'saved_comments.csv'
    write_gxworks2_csv_from_source(source, target, target_comments, plc_model=cpu)
    reread = inspect_gxworks2_csv(target, target_comments, plc_model=cpu, program_name='MAIN')
    expected = [('LD', ['X0']), (opcode, args), ('END', [])]
    for snapshot in (source, reread):
        assert snapshot['program_ir'] is not None, snapshot['coverage']
        validate_plc_ir(snapshot['program_ir'])
        assert [(r['op'], r['args']) for rung in ir_to_ladder(snapshot['program_ir'])['rungs']
                for r in rung['ordered_instructions']] == expected
        assert snapshot['coverage']['runtime_verified'] is False


def test_dabcdp_existing_codec_and_source_csv_keep_exact_pulse_name(monkeypatch, tmp_path):
    _bind(monkeypatch, _registry('Q03UDV', 'DABCDP'))
    _codec_csv_roundtrip(tmp_path, 'DABCDP', 'Q03UDV', 'q03udv')


@pytest.mark.parametrize('opcode,enabled_when,execution_form', [
    ('DABIN', 'execution_condition_ON', 'continuous'),
    ('DABINP', 'execution_condition_rising_edge', 'pulse')])
def test_QL_dabin_own_six_ASCII_span_signed_result_and_gate(monkeypatch, opcode, enabled_when, execution_form):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import reviewed_native_fact_groups, reviewed_native_operand_parameters
    registry = _registry('Q03UDV', opcode)
    _bind(monkeypatch, registry)
    form = registry.resolve_catalog_form(opcode, cpu='Q03UDV')
    assert form.opcode == form.base_mnemonic == opcode and not form.double
    assert form.spec.execution_form == execution_form
    spec = registry.resolve(opcode, cpu='Q03UDV', operand_count=2)
    args = ['D100', 'D200']
    parameters = reviewed_native_operand_parameters(spec, opcode, args, 'Q03UDV')
    assert any(p.get('word_device_layout', {}).get('word_count') == 3 for p in parameters[0])
    assert any(p.get('signed') is True and p.get('encoding') == 'binary' for p in parameters[1])
    groups = reviewed_native_fact_groups(spec, opcode, args, 'Q03UDV',
                                        dimensions={'execution.operation_condition', 'effects.conversion'})
    gate = next(g.value for g in groups if g.dimension == 'execution.operation_condition')
    assert gate['enabled_when'] == enabled_when and gate['core_execution_established'] is False
    conversion = next(g.value for g in groups if g.dimension == 'effects.conversion')
    assert conversion['source_character_count'] == 6 and conversion['source_word_count'] == 3
    assert conversion['other_sign_ASCII_codes_processed_as'] == 'positive'
    assert conversion['full_source_relation_complete'] is False
    assert analyze_instruction_access(opcode, args, plc_model='Q03UDV') == (['D100', 'D101', 'D102'], ['D200'])
    for source in ['D100', '"-25108"', '" 32767"']:
        for rung in _rungs(opcode, source=source):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
            validate_plc_ir(ir)
            if source.startswith('"'):
                assert ir['networks'][0]['reads'] == ['X0']


@pytest.mark.parametrize('opcode', ['DABIN', 'DABINP'])
def test_QL_dabin_three_word_range_and_own_device_permissions(monkeypatch, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch, _registry('Q03UDV', opcode))
    maximum = DEVICE_LIMITS['Q03UDV']['D']
    for rung in _rungs(opcode, source=f'D{maximum - 2}'):
        validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))
    for source in [f'D{maximum - 1}', 'M0', 'Z0', 'K1234', r'U0\G100']:
        for rung in _rungs(opcode, source=source):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))
    for destination in ['Z1', 'K4Y40', r'J1\W100', r'U0\G100']:
        for rung in _rungs(opcode, destination=destination):
            validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))


@pytest.mark.parametrize('opcode', ['DABIN', 'DABINP'])
def test_QL_dabin_existing_codec_and_source_csv_preserve_literal_form(monkeypatch, tmp_path, opcode):
    _bind(monkeypatch, _registry('Q03UDV', opcode))
    _codec_csv_roundtrip(tmp_path, opcode, 'Q03UDV', 'q03udv')


@pytest.mark.parametrize('cpu', ['FX3U', 'FX3UC'])
@pytest.mark.parametrize('opcode', ['DABIN', 'DABINP'])
def test_FX_dabin_own_span_devices_sign_and_version(monkeypatch, tmp_path, cpu, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import reviewed_native_fact_groups
    from plc.instruction_constraints import check_instruction_constraints, literal_operand_values
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    args = ['D100', 'D200']
    spec = registry.resolve(opcode, cpu=cpu, operand_count=2)
    assert spec.execution_form == ('pulse' if opcode == 'DABINP' else 'continuous')
    assert analyze_instruction_access(opcode, args, plc_model=cpu) == (['D100', 'D101', 'D102'], ['D200'])
    processing = next(g.value for g in reviewed_native_fact_groups(spec, opcode, args, cpu,
                        dimensions={'effects.documented_processing'})
                      if g.value.get('operation') == 'fixed_sign_and_five_decimal_ASCII_to_signed_BIN16')
    assert processing['source_word_count'] == 3 and processing['destination_word_count'] == 1
    assert processing['other_sign_ASCII_codes_processed_as'] == 'operation_error'
    assert processing['Core_execution_verified'] is False and processing['full_source_relation_complete'] is False
    for version, accepted in [('2.19', False), ('2.20', True)]:
        report = check_instruction_constraints(opcode, literal_operand_values(spec, args, plc_model=cpu, opcode=opcode),
            plc_model=cpu, language='ladder', registry=registry,
            context={'project_mode': 'native_ladder_instruction', 'cpu_version': version})
        cpu_checks = [c for c in report['checks'] if c.get('dimension') == 'constraints.cpu_availability']
        assert any(c['status'] == ('passed' if accepted else 'failed') for c in cpu_checks), report
        if accepted:
            assert not any(c['status'] == 'failed' for c in cpu_checks), report
    for destination in ['D200', 'K1Y0', 'K4M0', 'K4S0', 'V0', 'Z0', r'U0\G100']:
        for rung in _rungs(opcode, destination=destination):
            validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))
    # FX's own source table has no character-literal/digit/buffer/direct-index input grant.
    for source in ['"-25108"', 'K4M0', r'U0\G100', 'V0', 'Z0']:
        for rung in _rungs(opcode, source=source):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))
    for destination in ['K5M0', 'K4X0', 'K1', 'M0']:
        for rung in _rungs(opcode, destination=destination):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu))
    if cpu == 'FX3U':
        _codec_csv_roundtrip(tmp_path, opcode, cpu, 'fx3u')
