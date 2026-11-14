"""Exact DFRO and DDVIT source consumers; interface, storage and hardware boundaries remain distinct."""
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _registry(cpu, opcode, package, *, admitted=True):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_FOLLOWUP80_TEST_SOURCE_OVERLAY') != '1':
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








NO_ADAPTER={'positioning_module_quantity':0,'positioning_module_model':'','modules':[]}
DDVIT_INVALID_HARDWARE=[
 ('relay_without_adapter','FX3U',['K1','K100','Y0','M0'],{'output_type':'relay',**NO_ADAPTER}),
 ('UC_adapter','FX3UC',['K1','K100','Y0','Y4'],{'output_type':'transistor','positioning_module_model':'FX3U-2HSY-ADP','positioning_module_quantity':1}),
 ('main_above_100k','FX3U',['K1','K100001','Y0','M0'],{'output_type':'transistor',**NO_ADAPTER}),
 ('adapter_pair_mismatch','FX3U',['K1','K100','Y0','Y5'],{'output_type':'transistor','positioning_module_model':'FX3U-2HSY-ADP','positioning_module_quantity':1}),
]


@pytest.mark.parametrize('name,cpu,args,profile',DDVIT_INVALID_HARDWARE,ids=[c[0] for c in DDVIT_INVALID_HARDWARE])
def test_DDVIT_own_known_hardware_contradictions_reach_APP_and_ordered(monkeypatch,name,cpu,args,profile):
    from plc.instruction_constraints import check_instruction_constraints,literal_operand_values
    registry=_registry(cpu,'DDVIT','fx-ddvit-native-contract-repair')
    _bind(monkeypatch,registry)
    version='2.20' if cpu=='FX3U' else '1.00'
    confirmed={'cpu_version':version,'hardware_profile':profile}
    spec=registry.resolve('DDVIT',cpu=cpu,operand_count=4)
    values=literal_operand_values(spec,args,plc_model=cpu,opcode='DDVIT')
    report=check_instruction_constraints('DDVIT',values,plc_model=cpu,language='ladder',context={'project_mode':'native_ladder_instruction',**confirmed})
    errors=[f for f in report['findings'] if f.get('severity')=='error' and f.get('dimension')=='constraints.documented_requirements']
    assert errors,(name,cpu,args,profile,report)
    assert any(any(s.get('manual_id')=='fx3_programming_r' and s.get('revision')=='R' for s in error.get('sources',[])) for error in errors),(name,errors)
    reasons={f['reason'] for f in errors}
    for _,result in _public(cpu,'DDVIT',args,confirmed):
        assert result['valid'] is False and result.get('status')!='unverified' and result['counts']['error']>0,(name,cpu,args,profile,result)
        assert any(reason in str(result) for reason in reasons),(name,cpu,reasons,result)


@pytest.mark.parametrize('cpu',['FX3U','FX3UC'])
def test_DDVIT_own_signature_storage_and_unbound_hardware_remain_separate(monkeypatch,cpu):
    from plc.instructions import reviewed_native_operand_parameters,reviewed_native_fact_groups
    from plc.ir import analyze_instruction_access
    registry=_registry(cpu,'DDVIT','fx-ddvit-native-contract-repair')
    _bind(monkeypatch,registry)
    args=['D0','D10','Y0','M0']
    spec=registry.resolve('DDVIT',cpu=cpu,operand_count=4)
    parameters=reviewed_native_operand_parameters(spec,'DDVIT',args,cpu)
    assert spec.native_operand_order==('S1','S2','D1','D2'),(cpu,spec)
    for descriptions in parameters[:2]:
        assert any(p['type']=='ANY32' and p.get('word_device_layout',{}).get('word_count')==2 and p['word_device_layout'].get('signed') is True for p in descriptions),(cpu,parameters)
    operation=next(f.value for f in reviewed_native_fact_groups(spec,'DDVIT',args,cpu,dimensions={'effects.documented_processing'}) if f.value.get('operation')=='one_speed_interrupt_constant_quantity_feed')
    assert operation['feature_versions_are_not_whole_instruction_minimums'] is True
    assert operation['Core_execution_verified'] is False and operation['Native_execution_verified'] is False and operation['full_source_relation_complete'] is False
    reads,writes=analyze_instruction_access('DDVIT',args,plc_model=cpu)
    assert {'D0','D1','D10','D11'} <= set(reads) and {'Y0','M0'} <= set(writes),(cpu,reads,writes)
    version='2.20' if cpu=='FX3U' else '1.00'
    for direction in ['M0','D0.0']:
        for _,result in _public(cpu,'DDVIT',['K1','K100','Y0',direction],{'cpu_version':version}):
            assert result['valid'] is False and result['status']=='unverified' and result['counts']['error']==0,(cpu,direction,result)
            assert result['counts'].get('unverified',0)>0,(cpu,direction,result)


@pytest.mark.parametrize('cpu',['FX3U','FX3UC'])
def test_DDVIT_nonzero_count_frequency_and_direction_slot_boundaries(monkeypatch,cpu):
    from plc.instruction_constraints import check_instruction_constraints,literal_operand_values
    registry=_registry(cpu,'DDVIT','fx-ddvit-native-contract-repair')
    _bind(monkeypatch,registry)
    version='2.20' if cpu=='FX3U' else '1.00'
    maximum=200000 if cpu=='FX3U' else 100000
    spec=registry.resolve('DDVIT',cpu=cpu,operand_count=4)
    for count,frequency in [('K-999999','K10'),('K999999',f'K{maximum}')]:
        args=[count,frequency,'Y0','M0']
        report=check_instruction_constraints('DDVIT',literal_operand_values(spec,args,plc_model=cpu,opcode='DDVIT'),plc_model=cpu,language='ladder',context={'project_mode':'native_ladder_instruction','cpu_version':version})
        numeric=[c for c in report['checks'] if c.get('dimension')=='constraints.numeric_ranges']
        assert numeric and all(c['status']=='passed' for c in numeric),(cpu,args,report)
    for args in [['K0','K100','Y0','M0'],['K1000000','K100','Y0','M0'],['K1','K9','Y0','M0'],['K1',f'K{maximum+1}','Y0','M0'],['K1','K100','M0','Y0'],['K1','K100','Y0','D0'],['K1','K100','Y0','D0.0Z0']]:
        for _,result in _public(cpu,'DDVIT',args,{'cpu_version':version}):
            assert result['valid'] is False and result['counts']['error']>0,(cpu,args,result)



def test_DDVIT_existing_FX3U_codec_and_source_csv_preserve_documented_17_steps(monkeypatch,tmp_path):
    import csv
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxworks2.csv_manager import GXWORKS2_HEADER,GXWORKS2_COMMENT_HEADER
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from plc.instruction_steps import instruction_step_width
    from plc.ir import ir_to_ladder
    _bind(monkeypatch,_registry('FX3U','DDVIT','fx-ddvit-native-contract-repair'))
    args=['K1','K100','Y0','M0']
    assert instruction_step_width('DDVIT',args,plc_model='FX3U').steps==17
    raw=encode_instruction('DDVIT',args,profile='fx3u',plc_model='FX3U',text_encoding='cp932')
    listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile='fx3u',text_encoding='cp932')
    assert not listing.gaps and listing.reconstruct()==raw,listing
    assert [(r.mnemonic,list(r.args),r.step_width) for r in listing.instructions]==[('DDVIT',args,17)]
    program,comments=tmp_path/'MAIN.csv',tmp_path/'COMMENT.csv'
    with program.open('w',encoding='utf-16',newline='') as out:
        csv.writer(out,delimiter='\t',lineterminator='\r\n').writerows([['MAIN'],['PLC信息:','FX3U'],GXWORKS2_HEADER,['','','LD','X0','','',''],['','','DDVIT','K1','','',''],['','','','K100','','',''],['','','','Y0','','',''],['','','','M0','','',''],['','','END','','','','']])
    with comments.open('w',encoding='utf-16',newline='') as out:
        csv.writer(out,delimiter='\t',lineterminator='\r\n').writerows([['COMMENT'],GXWORKS2_COMMENT_HEADER])
    source=inspect_gxworks2_csv(program,comments,plc_model='FX3U',program_name='MAIN')
    assert source['program_ir'] is not None,source['coverage']
    target,target_comments=tmp_path/'saved.csv',tmp_path/'saved_comments.csv'
    write_gxworks2_csv_from_source(source,target,target_comments,plc_model='FX3U')
    reread=inspect_gxworks2_csv(target,target_comments,plc_model='FX3U',program_name='MAIN')
    for snapshot in [source,reread]:
        assert snapshot['program_ir'] is not None,snapshot['coverage']
        assert [(r['op'],r['args']) for rung in ir_to_ladder(snapshot['program_ir'])['rungs'] for r in rung['ordered_instructions']]==[('LD',['X0']),('DDVIT',args),('END',[])]
        assert snapshot['coverage']['runtime_verified'] is False


@pytest.mark.parametrize('cpu',['Q00UJ','Q03UDV','L02'])
@pytest.mark.parametrize('opcode',['DFRO','DFROP'])
def test_DFRO_Native_ordinary_source_interface_and_storage_stay_separate_from_hardware(monkeypatch,cpu,opcode):
    from plc.instructions import reviewed_native_operand_parameters,reviewed_native_fact_groups
    registry=_registry(cpu,opcode,'ql-dfro-native-contract-repair')
    _bind(monkeypatch,registry)
    args=['H4','K0','D100','K3']
    spec=registry.resolve(opcode,cpu=cpu,operand_count=4)
    assert spec.native_operand_order==('n1','n2','D','n3'),(cpu,opcode,spec)
    parameters=reviewed_native_operand_parameters(spec,opcode,args,cpu)
    for index in [0,1,3]:
        assert any(p.get('word_device_layout',{}).get('word_count')==1 and p['word_device_layout'].get('value_bits')==16 for p in parameters[index]),(cpu,opcode,index,parameters)
    assert any(p.get('module_selector_role')=='intelligent_module_IO_selector_or_value' and p.get('range',{}).get('module_selector_prefixes')==['U'] for p in parameters[0]),(cpu,opcode,parameters)
    gate=next(g.value for g in reviewed_native_fact_groups(spec,opcode,args,cpu,dimensions={'execution.operation_condition'}) if g.value.get('execution_input')=='Native_ladder_condition')
    assert gate['enabled_when']==('execution_condition_rising_edge' if opcode.endswith('P') else 'execution_condition_ON'),(cpu,opcode,gate)
    assert gate['command_FALSE_output_behavior_established'] is False
    for ir,result in _public(cpu,opcode,args,{'selected_cpu_shared_memory_target':False}):
        assert result['valid'] is False and result['status']=='unverified' and result['counts']['error']==0,(cpu,opcode,result)
        assert ir['networks'][0]['writes']==[f'D{i}' for i in range(100,106)],(cpu,opcode,ir)
        assert any('dfro_actual_hardware_requirements' in f.get('fact_id','') and f['severity']=='unverified' for f in result['findings']),(cpu,opcode,result)


@pytest.mark.parametrize('opcode',['DFRO','DFROP'])
def test_DFRO_ordinary_and_CPU_shared_heads_keep_same_2n_word_payload(monkeypatch,opcode):
    from plc.instructions import reviewed_native_operand_parameters
    registry=_registry('Q03UDV',opcode,'ql-dfro-native-contract-repair')
    _bind(monkeypatch,registry)
    spec=registry.resolve(opcode,cpu='Q03UDV',operand_count=4)
    for n1,shared,bits,head_words in [('H4',False,32,2),('H3E0',True,16,1)]:
        args=[n1,'K0','D100','K3']
        parameters=reviewed_native_operand_parameters(spec,opcode,args,'Q03UDV')
        destinations=[p for p in parameters[2] if p.get('word_device_layout',{}).get('head_storage_only') is True]
        assert destinations and all(p['word_device_layout']['word_count']==head_words and p['word_device_layout']['value_bits']==bits and p['word_device_layout']['single_scalar'] is False for p in destinations),(opcode,n1,destinations)
        assert all(p['region_span']['unit']=='word' and p['region_span']['count_expression']==dict(operator='multiply',operands=[2,dict(parameter='n3')]) for p in destinations),(opcode,n1,destinations)
        for ir,result in _public('Q03UDV',opcode,args,{'selected_cpu_shared_memory_target':shared}):
            assert result['counts']['error']==0 and result['status']=='unverified',(opcode,n1,result)
            regions=[r for r in ir['networks'][0]['access_regions'] if r.get('operand_index')==2]
            assert regions and all(r.get('resolved_count')==6 and r.get('addresses')==[f'D{i}' for i in range(100,106)] for r in regions),(opcode,n1,regions)


@pytest.mark.parametrize('opcode',['DFRO','DFROP'])
def test_DFRO_target_consistency_is_public_and_unknown_is_not_false(monkeypatch,opcode):
    from plc.instruction_constraints import check_instruction_constraints,literal_operand_values
    registry=_registry('Q03UDV',opcode,'ql-dfro-native-contract-repair')
    _bind(monkeypatch,registry)
    spec=registry.resolve(opcode,cpu='Q03UDV',operand_count=4)
    for n1,context,expected in [('H4',{},'unverified'),('H4',{'selected_cpu_shared_memory_target':False},'passed'),('H3E0',{'selected_cpu_shared_memory_target':True},'passed'),('H4',{'selected_cpu_shared_memory_target':True},'failed'),('H3E0',{'selected_cpu_shared_memory_target':False},'failed')]:
        args=[n1,'K0','D100','K3']
        report=check_instruction_constraints(opcode,literal_operand_values(spec,args,plc_model='Q03UDV',opcode=opcode),plc_model='Q03UDV',language='ladder',context={'project_mode':'native_ladder_instruction',**context})
        checks=[c for c in report['checks'] if c.get('fact_id')=='authored:ql:dfro_target_consistency:constraints.documented_requirements']
        assert checks and all(c['status']==expected for c in checks),(opcode,n1,context,report)
        structured=[c for c in report['checks'] if 'dfro_target_consistency' in c.get('fact_id','') and c not in checks]
        assert all(c['status']=='not_applicable' for c in structured),(opcode,n1,context,structured)
        for _,result in _public('Q03UDV',opcode,args,context):
            if expected=='failed':
                assert result['counts']['error']>0 and result['status']=='failed' and any('dfro_target_consistency' in f.get('fact_id','') and f['severity']=='error' for f in result['findings']),(opcode,n1,context,result)
            else:
                assert result['counts']['error']==0 and result['status']=='unverified',(opcode,n1,context,result)


@pytest.mark.parametrize('opcode',['DFRO','DFROP'])
def test_DFRO_symbolic_count_range_and_zero_transfer_keep_word_units(monkeypatch,opcode):
    from plc.ir import analyze_instruction_access
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch,_registry('Q03UDV',opcode,'ql-dfro-native-contract-repair'))
    args=['D0','D10','D100','D20']
    reads,_=analyze_instruction_access(opcode,args,plc_model='Q03UDV')
    assert reads==['D0','D10','D20'],(opcode,reads)
    for ir,result in _public('Q03UDV',opcode,args,{}):
        assert result['counts']['error']==0 and result['status']=='unverified',(opcode,result)
        regions=[r for r in ir['networks'][0]['access_regions'] if r.get('operand_index')==2]
        assert regions and all('resolved_count' not in r and 'addresses' not in r and any('n3' in reason for reason in r.get('requires',[])) for r in regions),(opcode,regions)
    assert analyze_instruction_access(opcode,['H4','K0','D100','K0'],plc_model='Q03UDV')==([],[]),(opcode,'documented zero count has no data transfer')
    maximum=DEVICE_LIMITS['Q03UDV']['D']
    for _,result in _public('Q03UDV',opcode,['H4','K0',f'D{maximum-5}','K3'],{'selected_cpu_shared_memory_target':False}):
        assert result['counts']['error']==0,(opcode,result)
    for _,result in _public('Q03UDV',opcode,['H4','K0',f'D{maximum-4}','K3'],{'selected_cpu_shared_memory_target':False}):
        assert result['counts']['error']>0,(opcode,result)


@pytest.mark.parametrize('opcode',['DFRO','DFROP'])
def test_DFRO_U_selector_is_not_a_memory_or_runtime_integer_alias(monkeypatch,opcode):
    from plc.instructions import reviewed_module_selector_value
    from plc.instruction_constraints import literal_operand_values
    from plc.validation import parse_device_address
    registry=_registry('Q03UDV',opcode,'ql-dfro-native-contract-repair')
    _bind(monkeypatch,registry)
    args=['U4','K0','D100','K3']
    spec=registry.resolve(opcode,cpu='Q03UDV',operand_count=4)
    assert reviewed_module_selector_value(spec,0,'U4','Q03UDV')==4
    values=literal_operand_values(spec,args,plc_model='Q03UDV',opcode=opcode)
    assert not isinstance(values.get('n1'),(int,float)),(opcode,values)
    assert reviewed_module_selector_value(spec,1,'U4','Q03UDV') is None
    assert reviewed_module_selector_value(spec,0,'U4','FX3U') is None
    assert parse_device_address('U4','Q03UDV') is None
    for ir,result in _public('Q03UDV',opcode,args,{'selected_cpu_shared_memory_target':False}):
        assert ir['networks'][0]['reads']==['X0'],(opcode,ir)
        assert not any(r.get('head')=='U4' and r.get('access') in {'read','write'} for r in ir['networks'][0].get('access_regions',[])),(opcode,ir)
        assert result['counts']['error']==0 and result['status']=='unverified' and any('dfro_target_consistency' in f.get('fact_id','') and f['severity']=='unverified' for f in result['findings']),(opcode,result)
    for operands in [['H4','U4','D100','K3'],['H4','K0','U4','K3'],['H4','K0','D100','U4']]:
        for _,result in _public('Q03UDV',opcode,operands,{'selected_cpu_shared_memory_target':False}):
            assert result['counts']['error']>0,(opcode,operands,result)
    for _,result in _public('Q03UDV','MOV',['U4','D100']):
        assert result['counts']['error']>0,(opcode,'MOV has no source-qualified module slot',result)


@pytest.mark.parametrize('cpu,profile',[('Q02H','q02'),('Q03UDV','q03udv'),('L02','l02')])
@pytest.mark.parametrize('opcode',['DFRO','DFROP'])
def test_DFRO_existing_codec_and_source_csv_keep_literal_and_U_selectors(monkeypatch,tmp_path,cpu,profile,opcode):
    import csv
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxworks2.csv_manager import GXWORKS2_HEADER,GXWORKS2_COMMENT_HEADER
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from plc.ir import ir_to_ladder
    _bind(monkeypatch,_registry(cpu,opcode,'ql-dfro-native-contract-repair'))
    for selector in ['H4','U4']:
        args=[selector,'K0','D100','K3']
        raw=encode_instruction(opcode,args,profile=profile,plc_model=cpu,text_encoding='cp932')
        listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
        assert not listing.gaps and listing.reconstruct()==raw,(cpu,opcode,selector,listing)
        assert [(r.mnemonic,list(r.args)) for r in listing.instructions]==[(opcode,args)]
    program,comments=tmp_path/'MAIN.csv',tmp_path/'COMMENT.csv'
    with program.open('w',encoding='utf-16',newline='') as out:
        csv.writer(out,delimiter='\t',lineterminator='\r\n').writerows([['MAIN'],['PLC信息:',cpu],GXWORKS2_HEADER,['','','LD','X0','','',''],['','',opcode,'U4','','',''],['','','','K0','','',''],['','','','D100','','',''],['','','','K3','','',''],['','','END','','','','']])
    with comments.open('w',encoding='utf-16',newline='') as out:
        csv.writer(out,delimiter='\t',lineterminator='\r\n').writerows([['COMMENT'],GXWORKS2_COMMENT_HEADER])
    source=inspect_gxworks2_csv(program,comments,plc_model=cpu,program_name='MAIN')
    assert source['program_ir'] is not None,(cpu,opcode,source['coverage'])
    target,target_comments=tmp_path/'saved.csv',tmp_path/'saved_comments.csv'
    write_gxworks2_csv_from_source(source,target,target_comments,plc_model=cpu)
    reread=inspect_gxworks2_csv(target,target_comments,plc_model=cpu,program_name='MAIN')
    for snapshot in [source,reread]:
        assert snapshot['program_ir'] is not None,(cpu,opcode,snapshot['coverage'])
        assert [(r['op'],r['args']) for rung in ir_to_ladder(snapshot['program_ir'])['rungs'] for r in rung['ordered_instructions']]==[('LD',['X0']),(opcode,['U4','K0','D100','K3']),('END',[])]
        assert snapshot['coverage']['runtime_verified'] is False
