"""Source-specific dead-band operands and public delivery; no numeric executor grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_DBAND_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / ('fx-dband-native-contract-repair' if cpu.startswith('FX') else 'ql-dband-native-contract-repair')
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


@pytest.mark.parametrize('cpu', ['FX3U', 'FX3UC'])
@pytest.mark.parametrize('opcode', ['DBAND', 'DBANDP', 'BAND', 'BANDP'])
def test_FX_band_source_third_operand_is_a_device_in_public_APP_and_ordered(monkeypatch, cpu, opcode):
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    _bind(monkeypatch, _registry(cpu, opcode))
    version = '2.20' if cpu == 'FX3U' else '1.00'
    for literal in ['K0', 'H0']:
        args = ['K-10', 'K10', literal, 'D30']
        for rung in _rungs(opcode, args):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
            result = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': version}))
            assert result.get('valid') is False, (cpu, opcode, literal, result)
            assert result.get('counts', {}).get('error', 0) > 0, result
    for args in [['K-10', 'K10', 'D20', 'D30'], ['HFFFF', 'HFFFF', 'D20', 'D30']]:
        for rung in _rungs(opcode, args):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
            result = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': version}))
            assert result.get('valid') is True, (cpu, opcode, args, result)
    if opcode in {'DBAND', 'DBANDP'}:
        _assert_FX_dband_own_slot_namespaces_and_index_dependencies(cpu, opcode)


@pytest.mark.parametrize('cpu', ['Q03UDV', 'L02'])
@pytest.mark.parametrize('opcode', ['DBAND', 'DBANDP'])
def test_QL_dband_own_signed_dword_storage_and_partial_deadband_facts(monkeypatch, cpu, opcode):
    from plc.ir import build_plc_ir, analyze_instruction_access
    from plc.core import PLCCore
    from plc.instructions import reviewed_native_fact_groups
    from plc.instruction_constraints import literal_operand_values
    from application.projects import public
    registry = _registry(cpu, opcode)
    _bind(monkeypatch, registry)
    spec = registry.resolve(opcode, cpu=cpu, operand_count=4)
    assert spec.native_operand_order == ('S1', 'S2', 'S3', 'D')
    assert [o.word_count for o in spec.operands] == [2, 2, 2, 2]
    args = ['D100', 'D110', 'D120', 'D130']
    assert analyze_instruction_access(opcode, args, plc_model=cpu) == (
        ['D100', 'D101', 'D110', 'D111', 'D120', 'D121'], ['D130', 'D131'])
    facts = reviewed_native_fact_groups(spec, opcode, args, cpu,
                dimensions={'effects.data_control', 'execution.operation_condition'})
    operation = next(f.value for f in facts if f.value.get('operation') == 'signed_32bit_dead_band_control')
    assert operation['value_bits'] == 32 and operation['signed'] is True
    assert operation['cases'] == [
        dict(condition='S3_less_than_S1', result_operation='subtract', left='S3', right='S1'),
        dict(condition='S3_greater_than_S2', result_operation='subtract', left='S3', right='S2'),
        dict(condition='S1_less_than_or_equal_S3_less_than_or_equal_S2', result_value=0)]
    assert operation['published_overflow_example'] == dict(lower_limit=1000, input=-2147483648, output=2147482648)
    assert operation['general_overflow_algorithm_established'] is False
    assert operation['core_execution_established'] is False
    gate = next(f.value for f in facts if f.dimension == 'execution.operation_condition')
    assert gate['enabled_when'] == ('execution_condition_rising_edge' if opcode == 'DBANDP' else 'execution_condition_ON')
    assert literal_operand_values(spec, ['HFFFFFFFF', 'K0', 'K0', 'D130'], plc_model=cpu, opcode=opcode)['S1'] == -1
    # QL's own table permits a literal third input; FX's own table does not.
    for operands in [args, ['K-10', 'K10', 'K0', 'D130'], ['HFFFFFFFF', 'K10', 'D120', 'D130']]:
        for rung in _rungs(opcode, operands):
            result = public(PLCCore().validate_project(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)))
            assert result.get('valid') is True, result


def _assert_FX_dband_own_slot_namespaces_and_index_dependencies(cpu, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.core import PLCCore
    from application.projects import public
    version = '2.20' if cpu == 'FX3U' else '1.00'
    baseline = ['D100', 'D110', 'D120', 'D130']
    assert analyze_instruction_access(opcode, baseline, plc_model=cpu) == (
        ['D100', 'D101', 'D110', 'D111', 'D120', 'D121'], ['D130', 'D131'])
    valid = [baseline, ['K1X0', 'K4M0', 'K8S0', 'K1Y0']]
    for slot in range(4):
        for value in [r'U0\G100', 'D100Z0']:
            args = baseline.copy()
            args[slot] = value
            valid.append(args)
    for args in valid:
        for rung in _rungs(opcode, args):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
            result = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': version}))
            if any('\\G' in value for value in args):
                validate_plc_ir(ir, confirmed_spec={'cpu_version': version})
                assert result.get('counts', {}).get('error', 0) == 0, result
                if result.get('valid') is not True:
                    assert result.get('status') == 'unverified', result
            else:
                assert result.get('valid') is True, (cpu, opcode, args, result)
            if 'D100Z0' in args:
                assert 'Z0' in ir['networks'][0]['reads']
    invalid = []
    for slot in range(4):
        for value in ['V0', 'Z0']:
            args = baseline.copy()
            args[slot] = value
            invalid.append(args)
    invalid.append(['D100', 'D110', 'D120', 'K1X0'])
    for args in invalid:
        for rung in _rungs(opcode, args):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
            result = public(PLCCore().validate_project(ir, confirmed_spec={'cpu_version': version}))
            assert result.get('valid') is False and result.get('counts', {}).get('error', 0) > 0, (args, result)
