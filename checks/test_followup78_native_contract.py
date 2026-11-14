"""Exact DELTA, DDEG and DEADD source consumers; no execution grant."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode, package):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_FOLLOWUP78_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
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


def _public(cpu, opcode, args, confirmed=None):
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    for rung in _rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        yield ir, public(PLCCore().validate_project(ir, confirmed_spec=confirmed))




@pytest.mark.parametrize('cpu',['FX3U','FX3UC'])
@pytest.mark.parametrize('opcode',['DDEG','DDEGP'])
def test_FX_ddeg_own_REAL_storage_degree_relation_and_version(monkeypatch,cpu,opcode):
    from plc.ir import analyze_instruction_access
    from plc.instructions import reviewed_native_operand_parameters,reviewed_native_fact_groups
    registry=_registry(cpu,opcode,'fx-ddeg-native-contract-repair')
    _bind(monkeypatch,registry)
    args=['D0','D100']
    spec=registry.resolve(opcode,cpu=cpu,operand_count=2)
    parameters=reviewed_native_operand_parameters(spec,opcode,args,cpu)
    assert all(any(p['type']=='REAL' and p.get('word_device_layout',{}).get('word_count')==2 for p in items) for items in parameters),(cpu,opcode,parameters)
    groups=reviewed_native_fact_groups(spec,opcode,args,cpu,dimensions={'effects.documented_processing','execution.operation_condition'})
    operation=next(g.value for g in groups if g.dimension=='effects.documented_processing')
    assert operation['operation']=='radians_to_degrees' and operation['input_angle_unit']=='radian' and operation['output_angle_unit']=='degree'
    assert operation['documented_conversion_factor']==dict(numerator=180,denominator='pi')
    assert operation['published_example']==dict(input=1.435792,output=82.26482)
    assert operation['inverse_trigonometric_input_domain_claimed'] is False
    for key in ['current_source_value_verified','special_float_input_behavior_established','command_FALSE_output_behavior_established','indexed_effective_address_verified','Core_execution_verified','Native_execution_verified','full_source_relation_complete']:
        assert operation[key] is False,(cpu,opcode,key,operation)
    gate=next(g.value for g in groups if g.dimension=='execution.operation_condition')
    assert gate['condition']==('rising_edge' if opcode.endswith('P') else 'execution_condition_ON'),(cpu,opcode,gate)
    assert analyze_instruction_access(opcode,args,plc_model=cpu)==(['D0','D1'],['D100','D101'])
    version='2.20' if cpu=='FX3U' else '1.00'
    for operands in [args,['E2.0','D100']]:
        for ir,result in _public(cpu,opcode,operands,{'cpu_version':version}):
            assert result['valid'] is True,(cpu,opcode,operands,result)
            if operands[0].startswith('E'):assert ir['networks'][0]['reads']==['X0'],(cpu,opcode,ir)
    low='2.19' if cpu=='FX3U' else '0.99'
    for _,result in _public(cpu,opcode,args,{'cpu_version':low}):
        assert result['valid'] is False,(cpu,opcode,low,result)


@pytest.mark.parametrize('opcode',['DDEG','DDEGP'])
def test_FX_ddeg_own_namespaces_and_two_word_boundaries(monkeypatch,opcode):
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch,_registry('FX3U',opcode,'fx-ddeg-native-contract-repair'))
    maximum=DEVICE_LIMITS['FX3U']['D']
    for _,result in _public('FX3U',opcode,[f'D{maximum-1}','D100'],{'cpu_version':'2.20'}):
        assert result['valid'] is True,(opcode,maximum,result)
    for args in [[f'D{maximum}','D100'],['D0',f'D{maximum}'],['K2','D100'],['H2','D100'],['M0','D100'],['V0','D100'],['D0','E2.0'],['D0','K4M100']]:
        for _,result in _public('FX3U',opcode,args,{'cpu_version':'2.20'}):
            assert result['valid'] is False,(opcode,args,result)


@pytest.mark.parametrize('opcode',['DDEG','DDEGP'])
def test_FX_ddeg_existing_codec_steps_and_source_csv(monkeypatch,tmp_path,opcode):
    from checks.test_ascii_conversion_native_contract import _codec_csv_roundtrip
    from plc.instruction_steps import instruction_step_width
    _bind(monkeypatch,_registry('FX3U',opcode,'fx-ddeg-native-contract-repair'))
    assert instruction_step_width(opcode,['D100','D200'],plc_model='FX3U').steps==9
    _codec_csv_roundtrip(tmp_path,opcode,'FX3U','fx3u')


@pytest.mark.parametrize('cpu',['Q02H','Q03UDV','L02'])
@pytest.mark.parametrize('opcode',['DELTA','DELTAP'])
def test_QL_delta_direct_output_identity_preserves_configuration_unknown(monkeypatch,cpu,opcode):
    from plc.instructions import reviewed_native_operand_parameters,reviewed_native_fact_groups
    from plc.ir import analyze_instruction_access
    registry=_registry(cpu,opcode,'ql-delta-native-contract-repair')
    _bind(monkeypatch,registry)
    args=['DY0']
    spec=registry.resolve(opcode,cpu=cpu,operand_count=1)
    assert spec.native_operand_order==('D',),(cpu,opcode,spec)
    parameters=reviewed_native_operand_parameters(spec,opcode,args,cpu)
    parameter=next(p for p in parameters[0] if p.get('device_identity',{}).get('kind')=='direct_access_output')
    assert parameter['type']=='BOOL' and parameter['occupied_bit_count']==1
    assert parameter['device_identity']==dict(kind='direct_access_output',prefix='DY')
    assert parameter['range']['device_prefixes']==['DY'] and 'word_device_layout' not in parameter
    for key in ['cpu_Y_number_range_connection_established','installed_module_verified','actual_output_configuration_verified','hardware_target_configuration_verified']:
        assert parameter[key] is False,(cpu,opcode,key,parameter)
    facts=reviewed_native_fact_groups(spec,opcode,args,cpu,dimensions={'effects.operation','execution.operation_condition'})
    operation=next(f.value for f in facts if f.dimension=='effects.operation' and f.value.get('output')=='D')
    assert operation['operation']=='pulse_direct_access_output' and operation['documented_output_action_sequence']==['SET','RST']
    assert operation['pulse_occurs_at_instruction_execution'] is True and operation['pulse_output_is_not_mathematical_delta'] is True
    for key in ['numeric_output_bound_in_this_section_established','physical_pulse_width_established','pulse_width_equals_one_scan_established','command_FALSE_output_behavior_established','hardware_target_configuration_verified','core_execution_established','native_execution_verified','full_source_relation_complete']:
        assert operation[key] is False,(cpu,opcode,key,operation)
    gate=next(f.value for f in facts if f.dimension=='execution.operation_condition')
    assert gate['enabled_when']==('execution_condition_rising_edge' if opcode.endswith('P') else 'execution_condition_ON'),(cpu,opcode,gate)
    assert analyze_instruction_access(opcode,args,plc_model=cpu)==([],['DY0']),(cpu,opcode,'direct output must remain distinct from Y0')
    for ir,result in _public(cpu,opcode,args):
        assert ir['networks'][0]['writes']==['DY0'],(cpu,opcode,ir)
        assert result['valid'] is False and result['status']=='unverified' and result['counts']['error']==0,(cpu,opcode,result)
        assert any(r.get('head')=='DY0' and r.get('requires') for r in ir['networks'][0]['access_regions']),(cpu,opcode,ir)


@pytest.mark.parametrize('opcode',['DELTA','DELTAP'])
def test_QL_delta_rejects_ordinary_devices_and_literals(monkeypatch,opcode):
    _bind(monkeypatch,_registry('Q03UDV',opcode,'ql-delta-native-contract-repair'))
    for operand in ['Y0','M0','D0','K0','H0','K4Y0',r'U0\G0']:
        for _,result in _public('Q03UDV',opcode,[operand]):
            assert result['valid'] is False and result['counts']['error']>0,(opcode,operand,result)


@pytest.mark.parametrize('cpu,profile',[('Q02H','q02'),('Q03UDV','q03udv'),('L02','l02')])
@pytest.mark.parametrize('opcode',['DELTA','DELTAP'])
def test_QL_delta_existing_codec_and_source_csv_preserve_DY(monkeypatch,tmp_path,cpu,profile,opcode):
    import csv
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxworks2.csv_manager import GXWORKS2_HEADER,GXWORKS2_COMMENT_HEADER
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from plc.ir import ir_to_ladder
    _bind(monkeypatch,_registry(cpu,opcode,'ql-delta-native-contract-repair'))
    raw=encode_instruction(opcode,['DY0'],profile=profile,plc_model=cpu,text_encoding='cp932')
    listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
    assert not listing.gaps and listing.reconstruct()==raw,(cpu,opcode,listing)
    assert [(r.mnemonic,list(r.args)) for r in listing.instructions]==[(opcode,['DY0'])]
    program,comments=tmp_path/'MAIN.csv',tmp_path/'COMMENT.csv'
    with program.open('w',encoding='utf-16',newline='') as out:
        csv.writer(out,delimiter='\t',lineterminator='\r\n').writerows([['MAIN'],['PLC信息:',cpu],GXWORKS2_HEADER,['','','LD','X0','','',''],['','',opcode,'DY0','','',''],['','','END','','','','']])
    with comments.open('w',encoding='utf-16',newline='') as out:
        csv.writer(out,delimiter='\t',lineterminator='\r\n').writerows([['COMMENT'],GXWORKS2_COMMENT_HEADER])
    source=inspect_gxworks2_csv(program,comments,plc_model=cpu,program_name='MAIN')
    assert source['program_ir'] is not None,(cpu,opcode,source['coverage'])
    target,target_comments=tmp_path/'saved.csv',tmp_path/'saved_comments.csv'
    write_gxworks2_csv_from_source(source,target,target_comments,plc_model=cpu)
    reread=inspect_gxworks2_csv(target,target_comments,plc_model=cpu,program_name='MAIN')
    for snapshot in [source,reread]:
        assert snapshot['program_ir'] is not None,(cpu,opcode,snapshot['coverage'])
        assert [(r['op'],r['args']) for rung in ir_to_ladder(snapshot['program_ir'])['rungs'] for r in rung['ordered_instructions']]==[('LD',['X0']),(opcode,['DY0']),('END',[])]
        assert snapshot['coverage']['runtime_verified'] is False



def test_QL_delta_identity_and_index_boundaries_are_source_qualified(monkeypatch):
    from plc.device_identity import direct_access_output_reference
    from plc.instructions import reviewed_native_operand_parameters,reviewed_direct_access_output_identity
    from plc.ir import analyze_instruction_access,build_plc_ir
    from plc.validation import parse_device_address
    from plc.core import PLCCore
    from application.projects import public
    registry=_registry('Q03UDV','DELTA','ql-delta-native-contract-repair')
    _bind(monkeypatch,registry)
    spec=registry.resolve('DELTA',cpu='Q03UDV',operand_count=1)
    parameter=next(p for p in reviewed_native_operand_parameters(spec,'DELTA',['DY0Z0'],'Q03UDV')[0] if reviewed_direct_access_output_identity(p))
    assert parameter['range']['index_modification'] is True,parameter
    assert direct_access_output_reference('DY0000','Q03UDV')['text']=='DY0'
    assert direct_access_output_reference('DY0','FX3U') is None
    for wrong in ['Y0','DY0ZZ0','DY-1']:
        assert direct_access_output_reference(wrong,'Q03UDV') is None,wrong
    assert parse_device_address('DY0','Q03UDV') is None
    assert not reviewed_direct_access_output_identity({**parameter,'device_identity':{}})
    assert not reviewed_direct_access_output_identity({**parameter,'range':{'device_prefixes':['Y']}})
    assert analyze_instruction_access('DELTA',['DY0Z0'],plc_model='Q03UDV')==(['Z0'],['DY0Z0'])
    for ir,result in _public('Q03UDV','DELTA',['DY0Z0']):
        assert set(ir['networks'][0]['reads'])=={'X0','Z0'},ir
        assert ir['networks'][0]['writes']==['DY0Z0'],ir
        region=next(r for r in ir['networks'][0]['access_regions'] if r.get('address_space')=='direct_access_output')
        assert 'addresses' not in region and 'physical_bit_region' not in region,region
        assert {'indexed_effective_address','CPU_module_output_range_not_established','installed_output_configuration_not_established'} <= set(region['requires']),region
        assert result['valid'] is False and result['status']=='unverified' and result['counts']['error']==0,result
        assert any(f['code']=='SOURCE_DIRECT_ACCESS_OUTPUT_UNVERIFIED' for f in result['findings']),result
    for _,result in _public('Q03UDV','MOV',['D0','DY0']):
        assert result['valid'] is False and result['counts']['error']>0,result
    coil=dict(device_comments={},rungs=[dict(rung_id=1,branches=[dict(branch_id=1,y_offset_level=0,inputs=[dict(type='NO',address='X0')],outputs=[dict(type='OUT',address='DY0')])])])
    result=public(PLCCore().validate_project(build_plc_ir(coil,plc_model='Q03UDV')))
    assert result['valid'] is False and result['counts']['error']>0,result


@pytest.mark.parametrize('operand', ['DY0', 'DY0Z0'])
def test_QL_delta_source_output_comments_survive_ir_roundtrip(monkeypatch, operand):
    from plc.ir import build_plc_ir, ir_to_ladder
    registry = _registry('Q03UDV', 'DELTA', 'ql-delta-native-contract-repair')
    _bind(monkeypatch, registry)
    for rung in _rungs('DELTA', [operand]):
        ladder = dict(device_comments={operand: '模块输出命令', 'DY1': '未绑定声明'}, rungs=[rung])
        ir = build_plc_ir(ladder, plc_model='Q03UDV')
        assert ir['devices'][operand]['comment'] == '模块输出命令'
        assert ir['devices'][operand]['comment_declared'] is True
        assert 'DY1' not in ir['devices']
        assert ir_to_ladder(ir)['device_comments'] == {operand: '模块输出命令'}
