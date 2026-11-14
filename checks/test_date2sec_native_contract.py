"""Own six-field DATE2SEC/P record consumers; no calendar execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_DATE2SEC_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / 'ql-date2sec-native-contract-repair'
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


@pytest.mark.parametrize('cpu', ['Q00U', 'Q03UDV'])
@pytest.mark.parametrize('opcode', ['DATE2SEC', 'DATE2SECP'])
def test_date2sec_independent_six_word_record_and_two_word_result(monkeypatch, cpu, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import reviewed_native_operand_parameters, reviewed_native_fact_groups
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    spec = registry.resolve(opcode, cpu=cpu, operand_count=2)
    assert spec.native_operand_order == ('S', 'D') and spec.operands[0].word_count == 6
    assert spec.execution_form == ('pulse' if opcode == 'DATE2SECP' else 'continuous')
    args = ['D100', 'D200']
    parameters = reviewed_native_operand_parameters(spec, opcode, args, cpu)
    source = next(p for p in parameters[0] if p.get('word_device_layout', {}).get('kind') == 'independent_elements')
    assert source['word_device_layout']['single_scalar'] is False
    assert [(f['role'], f['offset'], f['minimum'], f['maximum']) for f in source['referent_fields']] == [
        ('year', 0, 2000, 2067), ('month', 1, 1, 12), ('day', 2, 1, 31),
        ('hour', 3, 0, 23), ('minute', 4, 0, 59), ('second', 5, 0, 59)]
    conversion = next(g.value for g in reviewed_native_fact_groups(spec, opcode, args, cpu,
                        dimensions={'effects.conversion'}) if g.value.get('operation') == 'date_time_record_to_seconds')
    assert conversion['epoch'] == dict(year=2000, month=1, day=1, hour=0, minute=0, second=0)
    assert conversion['weekday_input_read'] is False
    assert all(conversion[k] is False for k in ['calendar_algorithm_established', 'timezone_established',
                                                'output_word_order_established', 'core_execution_established'])
    assert analyze_instruction_access(opcode, args, plc_model=cpu) == (
        [f'D{i}' for i in range(100, 106)], ['D200', 'D201'])
    for rung in _rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        validate_plc_ir(json.loads(json.dumps(ir)))
        assert 'D106' not in ir['networks'][0]['reads']


@pytest.mark.parametrize('opcode', ['DATE2SEC', 'DATE2SECP'])
def test_date2sec_public_validator_binds_confirmed_serial_only_where_required(monkeypatch, opcode):
    from plc.validation import validate_ladder_full
    for cpu in ['Q00U', 'Q03UDV']:
        _bind(monkeypatch, _registry(cpu, opcode))
        for serial in [19041, 19042]:
            for rung in _rungs(opcode, ['D100', 'D200']):
                data = dict(device_comments={}, rungs=[rung])
                if cpu == 'Q03UDV' and serial == 19041:
                    with pytest.raises(ValueError):
                        validate_ladder_full(data, plc_model=cpu, confirmed_spec={'cpu_serial_first_five': serial})
                else:
                    validate_ladder_full(data, plc_model=cpu, confirmed_spec={'cpu_serial_first_five': serial})


@pytest.mark.parametrize('opcode', ['DATE2SEC', 'DATE2SECP'])
def test_date2sec_storage_boundaries_and_own_operand_forms(monkeypatch, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch, _registry('Q00U', opcode))
    maximum = DEVICE_LIMITS['Q00U']['D']
    for args, valid in [([f'D{maximum - 5}', f'D{maximum - 1}'], True),
                        ([f'D{maximum - 4}', 'D200'], False), (['D100', f'D{maximum}'], False)]:
        for rung in _rungs(opcode, args):
            if valid:
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q00U'))
            else:
                with pytest.raises(ValueError, match='memory extent exceeds'):
                    validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q00U'))
    for source in ['K1', 'H1', '"201601"', 'M0', 'Z0', r'U0\G100', r'J1\W100']:
        for rung in _rungs(opcode, [source, 'D200']):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q00U'))
    for destination in ['Z0', r'U0\G100', r'J1\W100', r'J1\SW100']:
        for rung in _rungs(opcode, ['D100', destination]):
            validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q00U'))
    for destination in ['M0', 'K1', r'J1\Y100']:
        for rung in _rungs(opcode, ['D100', destination]):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q00U'))


@pytest.mark.parametrize('opcode', ['DATE2SEC', 'DATE2SECP'])
def test_date2sec_existing_q03udv_codec_and_source_csv_roundtrip(monkeypatch, tmp_path, opcode):
    _bind(monkeypatch, _registry('Q03UDV', opcode))
    _codec_csv_roundtrip(tmp_path, opcode, 'Q03UDV', 'q03udv')



def test_date2sec_independent_record_has_no_96bit_numeric_scalar(monkeypatch):
    from plc.instruction_constraints import numeric_operand_type, literal_operand_values
    from plc.ir import build_plc_ir, validate_plc_ir
    registry = _registry('Q00U', 'DATE2SEC')
    _bind(monkeypatch, registry)
    spec = registry.resolve('DATE2SEC', cpu='Q00U', operand_count=2)
    assert numeric_operand_type(spec, 0) is None
    assert numeric_operand_type(spec, 1).bits == 32
    # Lexical extraction may identify a literal; source validation still rejects it.
    assert literal_operand_values(spec, ['H1', 'D200'], plc_model='Q00U', opcode='DATE2SEC')['S'] == 1
    for rung in _rungs('DATE2SEC', ['H1', 'D200']):
        with pytest.raises(ValueError, match='not source-authorized'):
            validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q00U'))
