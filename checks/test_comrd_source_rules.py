"""COMRD selects comment metadata and writes a state-dependent output region."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-comrd-native-contract-repair')
def entries():return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']

@pytest.mark.parametrize('op',['COMRD','COMRDP'])
@pytest.mark.parametrize('model',['Q02H','Q12PRH','Q03UDV','L02'])
def test_comrd_own_native_metadata_selector_and_separate_output_tail(model,op):
    rows=entries();assert len(rows)==100
    e=next(e for e in rows if e['target_model']==model and e['opcode']==op);facts={f['id']:f for f in e['facts']}
    own=[f for f in e['facts'] if any(s['manual_id']=='sh080809engx' for s in f['sources'])]
    assert all(p in {132,133,550,551,552} for f in own for s in f['sources'] for p in s['pdf_pages'])
    typed=facts['authored:ql:native_arguments_s_d:parameters.typed_operands'];assert typed['status']=='source_verified'
    assert typed['conditions']==[dict(context='project_mode',equals='native_ladder_instruction')]
    assert typed['value']['native_form']==dict(operand_order=['S','D'],minimum_operands=2,maximum_operands=2)
    s,d=typed['value']['operands']
    assert s['type']=='DEVICE_REFERENCE' and s['role']=='comment_device_selector' and s['direction']=='input'
    assert s['passing']=='device_reference' and s['device_value_read'] is False and s['selector_kind']=='device_comment_identity'
    assert 'word_device_layout' not in s and 'occupied_bit_count' not in s and 'bits' not in s
    assert 'index_modification' not in s['range'] and 'device_prefixes' not in s['range']
    assert d['type']=='STRING' and d['direction']=='output'
    assert d['range']['device_prefixes']==['T','ST','C','D','W','SD','SW','FD','R','ZR']
    assert any(133 in src['pdf_pages'] for src in typed['sources'])
    assert d['word_device_layout']==dict(device_unit_bits=16,word_count=16,element_bits=8,element_count=32)
    assert d['conditional_tail_write']==dict(selector='SM701',word_offset=16,ON=dict(word_count=1,value=0),OFF='unchanged',current_selector_value_verified=False)
    assert d['string_layout']==dict(encoding='ASCII',maximum_characters=32,characters_per_word=2,byte_order='low_byte_first',padding_byte=32)
    v=facts['authored:ql:comrd_comment_read:effects.documented_processing']['value']
    assert v['source_device_value_read'] is False and v['base_written_words']==16
    assert v['termination']['OFF']=='tail_word_unchanged' and v['termination']['ON']==dict(word_offset=16,word_count=1,value=0)
    assert v['completion']==dict(device='SM720',duration_scans=1,phase='after_completion')
    assert v['busy']==dict(device='SM721',while_ON='no_processing') and v['completion_timing']=='several_scans'
    assert v['shared_busy_instructions']==['SP.FREAD','SP.FWRITE','PRC','COMRD']
    assert v['interrupt_program_execution']=='no_processing' and v['simultaneous_comment_file_access'] is False
    assert v['program_memory_comment_file_allowed'] is False and v['PLC_file_setting_required'] is True
    assert v['SD_card_forced_disable']['CPU_groups']==['High_speed_Universal_QCPU','Universal_Process_QCPU','LCPU']
    for key in ['current_SM701_verified','current_SM721_verified','current_SM606_verified','current_comment_file_configuration_verified','current_comment_range_verified','current_program_task_type_verified','core_execution_established','native_execution_verified']:assert v[key] is False,key
    gate=facts['authored:ql:comrd_execution_gate:execution.operation_condition']['value']
    assert gate['enabled_when']==('execution_condition_rising_edge' if op=='COMRDP' else 'execution_condition_ON')
    err=facts['authored:ql:comrd_documented_errors:effects.documented_processing'];assert err['status']=='candidate_evidence'
    assert err['value']['CPU_error_row_applicability_established'] is False
    assert all(set(f.get('depends_on',[]))<=facts.keys() for f in e['facts'])


def test_comrd_exact_original_native_cpu_members_and_proposed_selector_contract():
    rows=entries();models={e['target_model'] for e in rows};assert len(models)==50
    assert not models&{'Q00','Q00J','Q01'}
    notes=json.loads((HERE/'source-notes.json').read_text());assert models==set(notes['header']['supported_models'])
    assert notes['own_selector']['numeric_domains_established'] is False and notes['own_selector']['index_modification_policy_established'] is False
    contracts=json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts'];assert len(contracts)==2
    for c in contracts:
        assert set(c['cpus'])==models and c['native_order']==['S','D']
        assert c['instruction']['operands'][0]['data_type']=='any'
        assert c['execution_form']==('pulse' if c['instruction']['mnemonic']=='COMRDP' else 'continuous')
    prior={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'prior-owner-contracts.json').read_text())}
    for e in rows:assert all(f in e['facts'] for f in prior[e['target_model'],e['opcode']]['preserved_facts'])


def test_comrd_default_projection_preserves_metadata_access_busy_and_conditional_tail():
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    e=next(e for e in entries() if e['target_model']=='Q03UDV' and e['opcode']=='COMRD')
    packet=project_packet(dict(kind='instruction',target='COMRD',scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target='COMRD',_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    rendered=json.dumps(technical_context_units(technical_knowledge_text(record)))
    for item in ['comment_device_selector','device_value_read','conditional_tail_write','SM701','tail_word_unchanged','SM720','SM721','SM606','current_comment_file_configuration_verified','CPU_error_row_applicability_established']:
        assert item in rendered,item
