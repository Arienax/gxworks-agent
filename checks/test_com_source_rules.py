"""COM's own zero-slot identity is distinct from both refresh generation and CCOM."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-com-native-contract-repair')

def rows():return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']

@pytest.mark.parametrize('model',['Q00J','Q02H','Q02PH','Q03UDV','Q12PRH','L02'])
def test_com_own_native_zero_slots_and_overall_cpu_scope(model):
    entries=rows();assert len(entries)==53
    e=next(e for e in entries if e['target_model']==model)
    own=[f for f in e['facts'] if any(s['manual_id']=='sh080809engx' for s in f['sources'])]
    assert all(491<=p<=495 for f in own for s in f['sources'] for p in s['pdf_pages'])
    cpu=[f for f in own if f['dimension']=='constraints.cpu_availability']
    assert len(cpu)==1 and cpu[0]['status']=='source_verified' and cpu[0]['value']['available'] is True
    assert cpu[0]['conditions']==[dict(context='project_mode',equals='native_ladder_instruction')]
    facts={f['id']:f for f in e['facts']};assert len(facts)==len(e['facts'])
    typed=facts['authored:ql:native_arguments_zero:parameters.typed_operands']
    assert typed['status']=='source_verified'
    assert typed['conditions']==cpu[0]['conditions']
    assert typed['value']['declaration_kind']=='instruction' and typed['value']['representation']=='native_ladder_instruction'
    assert typed['value']['operand_count']==0 and typed['value']['operands']==[]
    assert typed['value']['native_form']==dict(operand_order=[],minimum_operands=0,maximum_operands=0)
    gate=facts['authored:ql:com_execution_gate:execution.operation_condition']['value']
    assert gate['enabled_when']=='instruction_reached' and gate['execution_input']=='none'
    assert gate['ordinary_contact_execution'] is False and gate['conditional_refresh_instruction']=='CCOM(P)'
    legacy=facts['authored:ql:com_legacy_refresh:effects.documented_processing']['value']
    assert legacy['SM775_OFF']==['auto_refresh','external_device_communication']
    assert legacy['SM775_ON']==['external_device_communication']
    assert legacy['legacy_serial_boundary_established'] is False
    assert all(set(f.get('depends_on',[]))<=facts.keys() for f in e['facts'])


def test_com_select_refresh_keeps_own_selector_polarity_target_limits_and_unknowns():
    e=next(e for e in rows() if e['target_model']=='Q03UDV');f={f['id']:f for f in e['facts']}
    v=f['authored:ql:com_select_refresh:effects.documented_processing']['value']
    assert v['mode_device']=='SM775' and v['selection_word']=='SD778'
    assert v['OFF']['IO_refresh']=='not_executed'
    assert v['ON']['Q']['active_high_bits']==[0,1,2,3,4,5,6,13]
    assert v['ON']['L']['active_high_bits']==[0,1,3,6,13,14]
    assert v['ON']['Q']['service_executed_value']==v['ON']['L']['service_executed_value']==0
    assert v['generation_CPU_scope']['Basic_QCPU']['serial_first_five_minimum']==4122
    assert v['generation_CPU_scope']['High_Performance_QCPU']['serial_first_five_minimum']==4012
    assert v['generation_CPU_scope']['Process_CPU']['serial_first_five_minimum']==7032
    assert v['multiple_CPU_refresh']['receive_from_other_CPUs']==dict(SD778_bit=4,executed_value=1)
    assert v['multiple_CPU_refresh']['send_from_host_CPU']==dict(SD778_bit=15,executed_value=0)
    basic=next(t for t in v['targets'] if t['target']=='CC_Link_IE_Field_Network_Basic_refresh')
    assert basic['Q']['SD778_bit']==13 and basic['Q']['requirements']==[{'CPU_group_any':['QnUDVCPU','QnUDPVCPU']},{'cpu_serial_first_five_minimum':18112}]
    for key in ['current_selector_values_verified','current_refresh_configuration_verified','current_refresh_generation_verified','actual_refresh_targets_verified','current_program_task_type_verified','current_module_network_display_configuration_verified','current_redundant_system_restrictions_verified','core_execution_established','native_execution_verified','legacy_serial_boundary_established']:assert v[key] is False,key
    pending=f['authored:ql:com_refresh_generation_pending:execution.unresolved_source']
    assert pending['status']=='unknown' and pending['value']['source_legacy_reference_direction']=='or_later'
    assert pending['value']['manufacturer_erratum_claimed'] is False


def test_com_proposal_matches_original_53_and_preserves_structured_namespace():
    selectors=json.loads((HERE/'owner-selectors.json').read_text())
    contract=json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts'][0]
    assert len(selectors)==53 and set(contract['cpus'])=={e['target_model'] for e in selectors}
    assert contract['native_order']==[] and contract['instruction']['arity']==dict(min=0,max=0)
    assert contract['instruction']['operands']==[]
    prior={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'prior-owner-contracts.json').read_text())}
    for e in rows():
        old=prior[e['target_model'],e['opcode']]['preserved_facts']
        assert all(f in e['facts'] for f in old)
        assert all(not any(s['manual_id']=='sh080809engx' for s in f['sources']) for f in old)


def test_com_default_projection_keeps_refresh_generation_conflict_and_unconditional_gate():
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    e=next(e for e in rows() if e['target_model']=='Q03UDV')
    packet=project_packet(dict(kind='instruction',target='COM',scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target='COM',_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    rendered=json.dumps(technical_context_units(technical_knowledge_text(record)))
    for item in ['instruction_reached','ordinary_contact_execution','SD778','SM775','service_executed_value','legacy_serial_boundary_established','legacy_reference_says_or_later','current_redundant_system_restrictions_verified']:
        assert item in rendered,item
