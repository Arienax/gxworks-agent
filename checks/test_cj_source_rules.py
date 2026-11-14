"""CJ owns one Native P operand and same-program jump semantics."""
import json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-cj-native-contract-repair')

def test_cj_original_selectors_and_exact_cpu_contract():
    original=json.loads((HERE/'original-owner-selectors.json').read_text())
    assert len([e for e in original if e['original_id']=='B-00138'])==69
    assert len([e for e in original if e['original_id']=='B-00139'])==16
    assert all(e['target_model'].startswith('FX') for e in original if e['opcode']=='CJP')
    selectors=json.loads((HERE/'owner-selectors.json').read_text())
    contract=json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts'][0]
    assert len(selectors)==53 and set(contract['cpus'])=={e['target_model'] for e in selectors}
    assert contract['instruction']['cpu_support']==contract['sources'][0]['models']==contract['cpus']
    assert contract['native_order']==['P'] and contract['instruction']['arity']=={'min':1,'max':1}
    assert contract['instruction']['operands'][0]['data_type']=='pointer'

@pytest.mark.parametrize('model',['Q00J','Q03UDV','Q12PRH','L02','L26-PBT'])
def test_cj_own_source_preserves_skip_scan_and_unknown_program_context(model):
    entries=json.loads((HERE/'source-facts.json').read_text())['entries']
    assert len(entries)==53
    e=next(e for e in entries if e['target_model']==model)
    own=[f for f in e['facts'] if any(s['manual_id']=='sh080809engx' for s in f['sources'])]
    assert all(325<=p<=327 for f in own for s in f['sources'] for p in s['pdf_pages'])
    facts={f['id']:f for f in e['facts']}
    typed=facts['authored:ql:native_arguments_p:parameters.typed_operands']
    assert typed['status']=='source_verified'
    assert typed['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'}]
    assert typed['value']['native_form']==dict(operand_order=['P'],minimum_operands=1,maximum_operands=1)
    pin=typed['value']['operands'][0]
    assert pin['type']=='POINTER' and pin['device_class']=='P' and pin['direction']=='input'
    assert 'word_device_layout' not in pin and 'bits' not in pin
    jump=facts['authored:ql:cj:execution.jump']['value']
    assert jump['target_scope']=='same_program_file' and jump['effective_scan']=='current_scan'
    assert jump['command_OFF']=='execute_next_step' and jump['core_execution_established'] is False
    skip=facts['authored:ql:cj_skipped_program:effects.documented_processing']['value']
    assert skip['skipped_devices']=='state_unchanged'
    assert skip['already_ON_timer_skipped']=='accurate_measurement_not_guaranteed'
    assert skip['forward_jump']=='shortens_scan_time' and skip['backward_loop_requires_exit_before_watchdog_timeout'] is True
    assert skip['pointer_label_steps']==1 and skip['native_execution_verified'] is False
    error=facts['authored:ql:cj_documented_error_conditions:effects.documented_processing']
    assert error['status']=='candidate_evidence' and error['value']['error_code']=='4210'
    assert error['value']['CPU_error_row_applicability_established'] is False
    pending=facts['authored:ql:cj_context_pending:execution.unresolved_source']
    assert pending['status']=='unknown'
    assert all(pending['value'][field] is False for field in ['pointer_numeric_domain_verified','special_pointer_values_verified','FEND_boundary_verified','target_definition_verified','dynamic_reachability_verified'])
    assert all(set(f.get('depends_on',[]))<=facts.keys() for f in e['facts'])

def test_cj_default_projection_keeps_semantic_context():
    import copy
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    e=next(e for e in json.loads((HERE/'source-facts.json').read_text())['entries'] if e['target_model']=='Q03UDV')
    packet=project_packet(dict(kind='instruction',target='CJ',scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target='CJ',_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    rendered=json.dumps(technical_context_units(technical_knowledge_text(record)))
    for value in ['same_program_file','state_unchanged','accurate_measurement_not_guaranteed','shortens_scan_time','4210','FEND_boundary_verified','pending_symbol_semantics']:
        assert value in rendered,value
