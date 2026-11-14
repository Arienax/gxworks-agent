"""Exact DGBINP, DGRYP and DEADD source consumers; no execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode, package, *, admitted=True):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_FOLLOWUP79_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and (not admitted or entry.get('call_available')), (cpu, opcode, original.fact_bundle_version, entry)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    folder = BASE / package
    registry = copy.copy(original)
    for name in ('_cpu_contracts', '_definition_contract_bases', '_definition_owners'):
        setattr(registry, name, dict(getattr(original, name)))
    registry._loaded_definition_contracts = set(original._loaded_definition_contracts)
    registry._clear_definition_contracts()
    contract_file = folder / 'contract-additions.json'
    contracts = json.loads(contract_file.read_text(encoding='utf-8'))['cpu_contracts']
    if admitted and ('mitsubishi', opcode, cpu) not in registry._cpu_contracts:
        registry.load_target_contracts(contracts)
    source_file = folder / 'source-facts.json'
    source = next(e for e in json.loads(source_file.read_text(encoding='utf-8'))['entries']
                  if e['target_model'] == cpu and e['opcode'] == opcode)
    previous = original.definition_entry(opcode, cpu=cpu)
    assert previous is not None, (cpu, opcode, 'existing knowledge entry required')
    entry = copy.deepcopy(previous)
    entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith(('authored:ql:', 'authored:applied:'))] + copy.deepcopy(source['facts'])
    entry['facts'], _ = compose_programming_facts(registry, opcode, cpu=cpu, existing=entry['facts'])
    previous_available = entry.get('call_available')
    if admitted:
        entry['call_available'] = False
    # Negative facts must reject publicly even if legacy compatibility metadata remains true.
    assert bool(refresh_reviewed_call_admission(entry, registry)) is admitted, (cpu, opcode, admitted)
    if not admitted:
        assert entry.get('call_available') == previous_available, (cpu, opcode, 'legacy metadata must be preserved')
    registry._definition_owners[('mitsubishi', opcode, cpu)] = entry
    return registry

from checks.test_ascii_conversion_native_contract import _bind
from checks.test_ddrd_ddwr_native_contract import _rungs


def _public(cpu, opcode, args, confirmed=None):
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    for rung in _rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        yield ir, public(PLCCore().validate_project(ir, confirmed_spec=confirmed))






@pytest.mark.parametrize('cpu',['Q02H','Q03UDV','L02'])
@pytest.mark.parametrize('opcode',['DGBINP','DGRYP'])
def test_QL_gray_p_own_unsigned_storage_complete_relation_and_trigger(monkeypatch,cpu,opcode):
    from plc.instructions import reviewed_native_operand_parameters,reviewed_native_fact_groups
    from plc.ir import analyze_instruction_access
    registry=_registry(cpu,opcode,'ql-gray-bin-native-contract-repair')
    _bind(monkeypatch,registry)
    args=['D0','D100']
    spec=registry.resolve(opcode,cpu=cpu,operand_count=2)
    assert spec.native_operand_order==('S','D'),(cpu,opcode,spec)
    parameters=reviewed_native_operand_parameters(spec,opcode,args,cpu)
    assert all(any(p.get('word_device_layout',{}).get('word_count')==2 and p['word_device_layout'].get('signed') is False for p in items) for items in parameters),(cpu,opcode,parameters)
    facts=reviewed_native_fact_groups(spec,opcode,args,cpu,dimensions={'effects.documented_operation','execution.operation_condition'})
    relation=next(f.value for f in facts if f.dimension=='effects.documented_operation' and f.value.get('inputs')==['S'] and f.value.get('code')=='binary_reflected_gray')
    assert relation['operation']==('gray_to_binary' if opcode=='DGBINP' else 'binary_to_gray'),(cpu,opcode,relation)
    assert relation['input_value_type']==dict(bits=32,kind='int',signed=False)
    assert relation['output_value_type']==dict(bits=32,kind='int',signed=False)
    assert relation['source_value_range']==dict(minimum=0,maximum=2147483647)
    assert relation['value_relation']['op']=='bit_xor' and relation['output_layout']['word_count']==2
    gate=next(f.value for f in facts if f.dimension=='execution.operation_condition' and f.value.get('enabled_when')=='execution_condition_rising_edge')
    assert gate['core_execution_established'] is False,(cpu,opcode,gate)
    assert analyze_instruction_access(opcode,args,plc_model=cpu)==(['D0','D1'],['D100','D101'])
    for _,result in _public(cpu,opcode,args):
        assert result['valid'] is True,(cpu,opcode,args,result)


@pytest.mark.parametrize('opcode',['DGBINP','DGRYP'])
def test_QL_gray_p_own_source_literals_are_unsigned_and_bounded(monkeypatch,opcode):
    from plc.instruction_constraints import literal_operand_values,numeric_operand_type
    from plc.instructions import reviewed_native_operand_parameters
    registry=_registry('Q03UDV',opcode,'ql-gray-bin-native-contract-repair')
    _bind(monkeypatch,registry)
    spec=registry.resolve(opcode,cpu='Q03UDV',operand_count=2)
    assert numeric_operand_type(spec,0).signed is False
    parameters=reviewed_native_operand_parameters(spec,opcode,['K0','D100'],'Q03UDV')
    assert parameters[0] and all(p.get('passing')!='device_reference' for p in parameters[0]),(opcode,'No old ref-only source declaration may remain active',parameters)
    for source,value in [('K0',0),('K2147483647',2147483647),('H7FFFFFFF',2147483647)]:
        assert literal_operand_values(spec,[source,'D100'],plc_model='Q03UDV',opcode=opcode)['S']==value
        for ir,result in _public('Q03UDV',opcode,[source,'D100']):
            assert result['valid'] is True,(opcode,source,result)
            assert ir['networks'][0]['reads']==['X0'],(opcode,ir)
    for args in [['K-1','D100'],['K2147483648','D100'],['H80000000','D100'],['HFFFFFFFF','D100'],['D0','K0'],['D0','H0']]:
        for _,result in _public('Q03UDV',opcode,args):
            assert result['valid'] is False,(opcode,args,result)


@pytest.mark.parametrize('opcode',['DGBINP','DGRYP'])
def test_QL_gray_p_storage_boundary_and_selected_digit_bits(monkeypatch,opcode):
    from plc.ir import analyze_instruction_access
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch,_registry('Q03UDV',opcode,'ql-gray-bin-native-contract-repair'))
    maximum=DEVICE_LIMITS['Q03UDV']['D']
    for _,result in _public('Q03UDV',opcode,[f'D{maximum-1}','D100']):
        assert result['valid'] is True,(opcode,maximum,result)
    for args in [[f'D{maximum}','D100'],['D0',f'D{maximum}'],['K9M0','D100'],['D0','K9M100']]:
        for _,result in _public('Q03UDV',opcode,args):
            assert result['valid'] is False,(opcode,args,result)
    assert analyze_instruction_access(opcode,['K4M0','K4M100'],plc_model='Q03UDV')==([f'M{i}' for i in range(16)],[f'M{i}' for i in range(100,116)])
    for _,result in _public('Q03UDV',opcode,['K4M0','K4M100']):
        assert result['valid'] is True,(opcode,result)


@pytest.mark.parametrize('cpu,profile',[('Q02H','q02'),('Q03UDV','q03udv'),('L02','l02')])
@pytest.mark.parametrize('opcode',['DGBINP','DGRYP'])
def test_QL_gray_p_existing_codec_and_source_csv(monkeypatch,tmp_path,cpu,profile,opcode):
    from checks.test_ascii_conversion_native_contract import _codec_csv_roundtrip
    _bind(monkeypatch,_registry(cpu,opcode,'ql-gray-bin-native-contract-repair'))
    _codec_csv_roundtrip(tmp_path,opcode,cpu,profile)


@pytest.mark.parametrize('opcode',['DEADD','DEADDP'])
def test_FX_deadd_integer_constants_keep_own_execution_coercion(monkeypatch,opcode):
    from plc.instructions import reviewed_native_operand_parameters,reviewed_native_fact_groups
    registry=_registry('FX3U',opcode,'fx-deadd-native-contract-repair')
    _bind(monkeypatch,registry)
    args=['K1','H2','D100']
    spec=registry.resolve(opcode,cpu='FX3U',operand_count=3)
    parameters=reviewed_native_operand_parameters(spec,opcode,args,'FX3U')
    for items in parameters[:2]:
        assert any(p.get('constant_coercion')==dict(source_radices=['K','H'],destination_type='REAL',conversion='integer_value_to_floating_point_at_execution') for p in items),parameters
    operation=next(g.value for g in reviewed_native_fact_groups(spec,opcode,args,'FX3U',dimensions={'effects.documented_processing'}) if g.value.get('operation')=='binary_floating_point_addition')
    assert operation['integer_constants_converted_to_floating_point']==['K','H']
    assert operation['integer_constants_bit_reinterpreted_as_REAL'] is False
    for ir,result in _public('FX3U',opcode,args,{'cpu_version':'2.20'}):
        assert result['valid'] is True,(opcode,args,result)
        assert ir['networks'][0]['reads']==['X0'],(opcode,ir)
        assert ir['networks'][0]['writes']==['D100','D101'],(opcode,ir)


FX_DEADD_VERSIONS={'FX2N':None,'FX3S':'1.00','FX3G':'1.10','FX3GC':'1.40','FX3U':'2.20','FX3UC':'1.00'}


@pytest.mark.parametrize('cpu',list(FX_DEADD_VERSIONS))
@pytest.mark.parametrize('opcode',['DEADD','DEADDP'])
def test_FX_deadd_own_REAL_regions_gate_and_model_conditions(monkeypatch,cpu,opcode):
    from plc.ir import analyze_instruction_access
    from plc.instructions import reviewed_native_operand_parameters,reviewed_native_fact_groups
    registry=_registry(cpu,opcode,'fx-deadd-native-contract-repair')
    _bind(monkeypatch,registry)
    args=['D0','D10','D100']
    spec=registry.resolve(opcode,cpu=cpu,operand_count=3)
    parameters=reviewed_native_operand_parameters(spec,opcode,args,cpu)
    assert len(parameters)==3 and all(any(p['type']=='REAL' and p.get('word_device_layout',{}).get('word_count')==2 for p in items) for items in parameters),(cpu,opcode,parameters)
    facts=reviewed_native_fact_groups(spec,opcode,args,cpu,dimensions={'effects.documented_processing','execution.operation_condition'})
    operation=next(f.value for f in facts if f.dimension=='effects.documented_processing' and f.value.get('operation')=='binary_floating_point_addition')
    assert operation['same_device_allowed'] is True and operation['continuous_same_device_uses_previous_result_next_scan'] is True
    assert operation['full_source_relation_complete'] is False and operation['Core_execution_verified'] is False and operation['Native_execution_verified'] is False
    if cpu=='FX2N':
        assert operation['flag_source_conflict']['mapping_resolved'] is False,operation
    else:
        assert 'flag_source_conflict' not in operation,(cpu,opcode,operation)
    gate=next(f.value for f in facts if f.dimension=='execution.operation_condition')
    assert gate['condition']==('rising_edge' if opcode.endswith('P') else 'execution_condition_ON'),(cpu,opcode,gate)
    assert analyze_instruction_access(opcode,args,plc_model=cpu)==(['D0','D1','D10','D11'],['D100','D101'])
    version=FX_DEADD_VERSIONS[cpu]
    confirmed={'cpu_version':version} if version else None
    for operands in [args,['D0','D10','D0']]:
        for _,result in _public(cpu,opcode,operands,confirmed):
            assert result['valid'] is True,(cpu,opcode,operands,result)
    if version:
        major,minor=map(int,version.split('.'))
        low=f'{major}.{minor-1:02d}' if minor else f'{major-1}.99'
        for _,result in _public(cpu,opcode,args,{'cpu_version':low}):
            assert result['valid'] is False,(cpu,opcode,low,result)


@pytest.mark.parametrize('opcode',['DEADD','DEADDP'])
def test_FX_deadd_slot_permissions_follow_legacy_and_modern_own_tables(monkeypatch,opcode):
    for cpu in ['FX2N','FX3S','FX3G','FX3U']:
        _bind(monkeypatch,_registry(cpu,opcode,'fx-deadd-native-contract-repair'))
        confirmed={'cpu_version':FX_DEADD_VERSIONS[cpu]} if FX_DEADD_VERSIONS[cpu] else None
        for args in [['M0','D10','D100'],['V0','D10','D100'],['D0','D10','K0'],['D0','D10','K4M100']]:
            for _,result in _public(cpu,opcode,args,confirmed):
                assert result['valid'] is False,(cpu,opcode,args,result)
        if cpu in {'FX2N','FX3S'}:
            for _,result in _public(cpu,opcode,['R0','D10','D100'],confirmed):
                assert result['valid'] is False,(cpu,opcode,'R not source allowed',result)
        if cpu=='FX2N':
            for _,result in _public(cpu,opcode,['E1.0','D10','D100'],confirmed):
                assert result['valid'] is False,(cpu,opcode,'E not legacy allowed',result)
        else:
            for _,result in _public(cpu,opcode,['E1.0','E2.0','D100'],confirmed):
                assert result['valid'] is True,(cpu,opcode,'E own modern allowed',result)


@pytest.mark.parametrize('cpu,manual,revision,pages',[('FX0','jy992d48301j','J',{247}),('FX1S','jy992d88101e','E',{259})])
def test_FX_deadd_own_negative_reaches_public_despite_legacy_compatibility(monkeypatch,cpu,manual,revision,pages):
    from checks.test_native_cpu_availability_delivery import test_known_native_cpu_exclusion_reaches_constraint_APP_and_ordered_public_errors
    registry=_registry(cpu,'DEADD','fx-deadd-native-contract-repair',admitted=False)
    _bind(monkeypatch,registry)
    test_known_native_cpu_exclusion_reaches_constraint_APP_and_ordered_public_errors(cpu,'DEADD',['D0','D2','D10'],manual,revision,pages)


@pytest.mark.parametrize('opcode',['DEADD','DEADDP'])
def test_FX_deadd_existing_header_own_integer_token_codec_and_source_csv(monkeypatch,tmp_path,opcode):
    import csv
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxworks2.csv_manager import GXWORKS2_HEADER,GXWORKS2_COMMENT_HEADER
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from plc.instruction_steps import instruction_step_width
    from plc.ir import ir_to_ladder,validate_plc_ir
    _bind(monkeypatch,_registry('FX3U',opcode,'fx-deadd-native-contract-repair'))
    args=['K1','H2','D100']
    assert instruction_step_width(opcode,args,plc_model='FX3U').steps==13
    raw=encode_instruction(opcode,args,profile='fx3u',plc_model='FX3U',text_encoding='cp932')
    listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile='fx3u',text_encoding='cp932')
    assert not listing.gaps and listing.reconstruct()==raw,(opcode,listing)
    assert [(r.mnemonic,list(r.args),r.step_width) for r in listing.instructions]==[(opcode,args,13)]
    program,comments=tmp_path/'MAIN.csv',tmp_path/'COMMENT.csv'
    with program.open('w',encoding='utf-16',newline='') as out:
        csv.writer(out,delimiter='\t',lineterminator='\r\n').writerows([['MAIN'],['PLC信息:','FX3U'],GXWORKS2_HEADER,['','','LD','X0','','',''],['','',opcode,'K1','','',''],['','','','H2','','',''],['','','','D100','','',''],['','','END','','','','']])
    with comments.open('w',encoding='utf-16',newline='') as out:
        csv.writer(out,delimiter='\t',lineterminator='\r\n').writerows([['COMMENT'],GXWORKS2_COMMENT_HEADER])
    source=inspect_gxworks2_csv(program,comments,plc_model='FX3U',program_name='MAIN')
    assert source['program_ir'] is not None,(opcode,source['coverage'])
    target,target_comments=tmp_path/'saved.csv',tmp_path/'saved_comments.csv'
    write_gxworks2_csv_from_source(source,target,target_comments,plc_model='FX3U')
    reread=inspect_gxworks2_csv(target,target_comments,plc_model='FX3U',program_name='MAIN')
    for snapshot in [source,reread]:
        assert snapshot['program_ir'] is not None,(opcode,snapshot['coverage'])
        validate_plc_ir(snapshot['program_ir'])
        assert [(r['op'],r['args']) for rung in ir_to_ladder(snapshot['program_ir'])['rungs'] for r in rung['ordered_instructions']]==[('LD',['X0']),(opcode,args),('END',[])]
        assert snapshot['coverage']['runtime_verified'] is False
