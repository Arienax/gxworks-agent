"""CCOM has a sourced zero-slot signature, not a verified live refresh."""
import copy,json
from pathlib import Path
import pytest

HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-ccom-native-contract-repair')
OPS=('CCOM','CCOMP')

@pytest.fixture(scope='module')
def source_registry():
    import plc.instructions as instructions
    import plc.validation as validation
    import plc.ir as ir
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    registry=instructions.DEFAULT_INSTRUCTION_REGISTRY;local=copy.copy(registry)
    for name in ('_cpu_contracts','_definition_contract_bases','_definition_owners'):
        setattr(local,name,dict(getattr(registry,name)))
    local._loaded_definition_contracts=set(registry._loaded_definition_contracts);local._clear_definition_contracts()
    for e in json.loads((HERE/'source-facts.json').read_text())['entries']:
        model,op=e['target_model'],e['opcode'];entry=copy.deepcopy(registry.definition_entry(op,cpu=model))
        entry['facts']=[f for f in entry['facts'] if not f['id'].startswith(('authored:ql:','authored:applied:'))]+e['facts']
        assert refresh_reviewed_call_admission(entry,local) and entry['call_available']
        local._definition_owners['mitsubishi',op,model]=entry
    mp=pytest.MonkeyPatch()
    for module in (instructions,validation,ir):mp.setattr(module,'DEFAULT_INSTRUCTION_REGISTRY',local)
    yield local
    mp.undo()

def app(op,args=(),model='Q03UDV'):
    from plc.ir import build_plc_ir,validate_plc_ir
    from checks.test_bklogic_native_contract import app_program
    validate_plc_ir(build_plc_ir(app_program(op,args),plc_model=model))

def ordered(op,args=(),model='Q03UDV'):
    from plc.ir import build_plc_ir,validate_plc_ir
    payload=dict(device_comments={},rungs=[dict(rung_id=1,ordered_instructions=[dict(op='LD',args=['X0']),dict(op=op,args=list(args))])])
    validate_plc_ir(build_plc_ir(payload,plc_model=model))

def test_ccom_undeclared_modifier_spellings_rejected(source_registry):
    for op in ('DCCOM','CCOMD','CCOMPP'):
        assert source_registry.resolve(op,cpu='Q03UDV',operand_count=0) is None
        with pytest.raises(ValueError):
            app(op,model='Q03UDV')


@pytest.mark.parametrize('op',OPS)
def test_ccom_strict_source_admission_and_public_zero_slot_calls(source_registry,op):
    from plc.instruction_relations import _operands
    for model in ['Q00U','Q03UDV','L02']:
        spec=source_registry.resolve(op,cpu=model,operand_count=0)
        assert spec.native_operand_order==() and spec.operands==() and spec.min_operands==spec.max_operands==0
        assert spec.execution_form==('pulse' if op=='CCOMP' else 'continuous') and spec.st_call is None
        typed=next(f for f in source_registry.definition_entry(op,cpu=model)['facts'] if f['id']=='authored:ql:native_arguments_zero:parameters.typed_operands')
        assert _operands(typed)==([],[],{})
        for check in (app,ordered):
            check(op,model=model)
            with pytest.raises(ValueError):check(op,['K0'],model)
    form=source_registry.resolve_catalog_form(op,cpu='FX3U')
    assert form is None or not form.spec.supports_cpu('FX3U')
    with pytest.raises(ValueError):app(op,model='FX3U')

@pytest.mark.parametrize('op',OPS)
def test_ccom_own_CPU_groups_and_public_constraint_boundaries(source_registry,op):
    from plc.instruction_constraints import check_instruction_constraints
    serial=direct=0
    for e in json.loads((HERE/'source-facts.json').read_text())['entries']:
        if e['opcode']!=op:continue
        cpu=[f for f in e['facts'] if f['dimension']=='constraints.cpu_availability' and f['value'].get('available') is True]
        assert len(cpu)==2
        minima=[next((c['minimum'] for c in f.get('conditions',[]) if c.get('kind')=='serial_number_minimum'),None) for f in cpu]
        assert minima[0]==minima[1]
        if minima[0] is not None:serial+=1;assert minima[0]=='10102'
        else:direct+=1
    assert (serial,direct)==(17,22)
    for model,needs in [('Q03UD',True),('Q03UDE',True),('Q00U',False),('Q03UDV',False),('L02',False)]:
        for number,expected in [(None,'unverified' if needs else 'passed'),(10101,'failed' if needs else 'passed'),(10102,'passed')]:
            context={'project_mode':'native_ladder_instruction'}
            if number is not None:context['cpu_serial_first_five']=number
            result=check_instruction_constraints(op,{},plc_model=model,language='ladder',context=context,registry=source_registry)
            assert result['status']==expected,result

@pytest.mark.parametrize('op',OPS)
def test_ccom_referenced_selector_targets_and_default_unknown_configuration(source_registry,op):
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    entry=source_registry.definition_entry(op,cpu='Q03UDV')
    effect=next(f for f in entry['facts'] if f['id']=='authored:ql:ccom_refresh_configuration:effects.documented_processing')
    value=effect['value'];assert value['mode_device']=='SM775' and value['selection_word']=='SD778'
    assert value['OFF']==dict(IO_refresh='not_executed',other_documented_eligible_targets='executed')
    assert value['ON']['Q']['active_high_bits']==[0,1,2,3,4,5,6,13]
    assert value['ON']['L']['active_high_bits']==[0,1,3,6,13,14]
    assert value['ON']['Q']['service_executed_value']==value['ON']['L']['service_executed_value']==0
    targets={t['target']:t for t in value['targets']};field=targets['CC_Link_IE_Field_Network_refresh']
    assert field['Q']['requirements']==[{'cpu_serial_first_five_minimum':12012}] and field['L']['requirements']==[{'cpu_serial_first_five_minimum':13012}]
    basic=targets['CC_Link_IE_Field_Network_Basic_refresh']
    assert basic['Q']['requirements']==[{'CPU_group_any':['QnUDVCPU','QnUDPVCPU']},{'cpu_serial_first_five_minimum':18112}]
    assert basic['L']['requirements']==[{'hardware':'built_in_Ethernet_port_LCPU'},{'cpu_serial_first_five_minimum':18112}]
    assert targets['display_unit_communication']['Q']['documented_available'] is False
    assert targets['display_unit_communication']['L']['requirements']==[{'hardware':'built_in_Ethernet_port_LCPU'}]
    assert all(t['current_project_target_configuration_verified'] is False for t in targets.values())
    gate=next(f['value'] for f in entry['facts'] if f['id']=='authored:ql:ccom_execution_gate:execution.operation_condition')
    assert gate['enabled_when']==('execution_condition_rising_edge' if op=='CCOMP' else 'execution_condition_ON')
    assert set(p for s in effect['sources'] for p in s['pdf_pages'])=={493,494,495,496}
    assert all(s['manual_id']=='sh080809engx' for s in effect['sources'])
    packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(entry)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    visible=json.dumps(technical_context_units(technical_knowledge_text(record)))
    for field in ['SM775','SD778','service_executed_value','18112','built_in_Ethernet_port_LCPU','program_types_prohibited']:
        assert field in visible,field
    for field in ['current_selector_values_verified','current_refresh_configuration_verified','actual_refresh_targets_verified','core_execution_established']:
        assert '"'+field+'": false' in visible,field

@pytest.mark.parametrize('op',OPS)
def test_ccom_structured_interface_keeps_own_P_mask_gaps_not_Native_zero_slots(source_registry,op):
    entry=source_registry.definition_entry(op,cpu='Q00U');facts={f['id']:f for f in entry['facts']}
    st=facts['authored:ql:argument_table_st:parameters.typed_operands']['value']['operands']
    assert [p['name'] for p in st]==['EN'] and st[0]['type']=='BOOL'
    effect=facts['authored:ql:ccom_refresh_configuration_st:effects.documented_processing'];value=effect['value']
    assert all(s['manual_id']=='sh080783engp' for s in effect['sources'])
    assert not any(t['target']=='CC_Link_IE_Field_Network_Basic_refresh' for t in value['targets'])
    assert value['source_scope_limits']['L_Field_Network_selector_bit_established'] is False and value['ON']['L']['service_bit_polarity_established'] is False

@pytest.mark.parametrize('op',OPS)
def test_ccom_existing_zero_operand_codec_is_lossless_but_not_Native_execution(op):
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    for model,profile in [('Q03UDV','q03udv'),('L02','l02')]:
        raw=encode_instruction(op,[],profile=profile,plc_model=model,text_encoding='cp932')
        listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
        assert not listing.gaps and [(i.mnemonic,i.args) for i in listing.instructions]==[(op,())] and listing.reconstruct()==raw

    from gxw.models import GXWFormatError
    with pytest.raises(GXWFormatError,match='no proven storage descriptor'):
        encode_instruction(op,[],profile='q02',plc_model='Q02H',text_encoding='cp932')
