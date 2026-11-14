"""Own multiple-CPU tables establish identities, records and partial boundaries."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-ddrd-ddwr-native-contract-repair')
def entries():return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
def facts(op,model='Q03UDV'):return {f['id']:f for e in entries() if e['target_model']==model and e['opcode']==op for f in e['facts']}
@pytest.mark.parametrize('op',['D.DDRD','D.DDWR'])
def test_dd_own_five_slots_control_remote_identity_and_completion(op):
 f=facts(op);cpu=f['authored:ql:cpu_applicability:constraints.cpu_availability']
 assert cpu['status']=='source_verified' and cpu['value']['available'] is True
 assert cpu['conditions'][0]['minimum']=='10012'
 typed=f['authored:ql:native_arguments_n_s1_s2_d1_d2:parameters.typed_operands'];assert typed['status']=='source_verified'
 v=typed['value'];assert v['native_form']==dict(operand_order=['n','S1','S2','D1','D2'],minimum_operands=5,maximum_operands=5)
 pins={p['name']:p for p in v['operands']};assert all(p['index_modification_allowed'] is False for p in pins.values())
 assert pins['n']['range']['minimum']==992 and pins['n']['range']['maximum']==995
 control=pins['S1'];assert control['direction']=='in_out' and control['word_device_layout']['word_count']==2
 assert [(c['offset'],c['direction'],c['semantic_access']) for c in control['components']]==[(0,'output','write'),(1,'input','read')]
 assert control['components'][1]['range']==dict(minimum=1,maximum=100)
 remote=pins['S2' if op=='D.DDRD' else 'D1'];assert remote['selector_CPU_parameter']=='n'
 assert remote['selector_kind']=='remote_CPU_device_identity' and remote['device_value_read'] is False and remote['local_memory_access'] is False
 assert remote['remote_access']==('read' if op=='D.DDRD' else 'write')
 assert remote['accepted_source_forms']==['device_name','quoted_device_name']
 assert remote['direct_file_register_outside_host_range_allowed'] is True and remote['direct_file_register_prefixes']==['R','ZR']
 assert remote['quoted_device_outside_host_range_allowed'] is True
 assert remote['range']['digit_groups_allowed']==[4] and remote['range']['digit_group_start_bit_multiple']==16
 assert remote['range']['device_prefixes']==['T','ST','C','D','W','SD','SW','R','ZR']
 assert remote['range']['digit_group_device_prefixes']==['X','Y','M','L','SM','F','B','SB','FX','FY']
 assert 'Z' not in remote['range']['device_prefixes']
 assert pins['n']['range']['device_prefixes']==control['range']['device_prefixes']==remote['range']['device_prefixes']
 assert pins['D2']['range']['device_prefixes']==['X','Y','M','L','SM','F','B','SB','R','ZR']
 assert {s['pdf_pages'][0] for s in typed['sources']} >= ({885,888} if op=='D.DDRD' else {881,884})
 assert 'word_device_layout' not in remote and remote['remote_device_range_verified'] is False and remote['quoted_device_grammar_verified'] is False
 local=pins['D1' if op=='D.DDRD' else 'S2'];assert local['word_device_layout']['head_storage_only'] is True
 assert local['transfer_layout']['word_count_source']==dict(source_parameter='S1',offset=1)
 assert pins['D2']['type']=='BOOL' and pins['D2']['occupied_bit_count']==2
 assert pins['D2']['forbidden_device_prefixes']==['FX','FY']
 effect=f['authored:ql:dd_transfer_interface:effects.documented_processing']['value']
 for k in ['core_execution_established','native_execution_verified','remote_CPU_context_verified','remote_device_range_verified','processing_prose_label_consistency_verified','operation_error_count_boundary_consistency_verified']:assert effect[k] is False,k
 completion=f['authored:ql:dd_completion:effects.documented_processing']['value']
 assert completion['set_timing']=='END_of_completion_scan' and completion['clear_timing']=='next_END' and completion['normal_error_bit'] is False
 blocks=f['authored:ql:dd_transmission_blocks:effects.documented_processing']['value']['blocks_by_word_count']
 assert blocks==([dict(minimum=1,maximum=100,blocks=1)] if op=='D.DDRD' else [dict(minimum=a,maximum=b,blocks=i+1) for i,(a,b) in enumerate([(1,4),(5,20),(21,36),(37,52),(53,68),(69,84),(85,100)])])
 assert all(set(x.get('depends_on',[]))<=f.keys() for x in f.values())
@pytest.mark.parametrize('op',['D.DDRD','D.DDWR'])
def test_dd_four_explicit_cpu_exclusions_are_not_serial_conditional(op):
 for m in ['Q00U','Q00UJ','Q01U','Q02U']:
  f=facts(op,m);assert len(f)==1
  cpu=next(iter(f.values()));assert cpu['value']['available'] is False and cpu['value']['model_states']=={m:'unsupported'}
  assert not cpu.get('conditions') and not cpu['value'].get('required_context')
def test_dd_exact_original58_and_positive50_contract_scope_and_history_input():
 rows=entries();assert len(rows)==58
 selectors=json.loads((HERE/'original-owner-selectors.json').read_text())
 assert {(e['target_model'],e['opcode']) for e in rows}=={(e['target_model'],e['opcode']) for e in selectors}
 contracts=json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts'];assert len(contracts)==2
 for c in contracts:
  assert len(c['cpus'])==25 and not set(c['cpus'])&{'Q00U','Q00UJ','Q01U','Q02U'}
  assert c['instruction']['cpu_support']==c['cpus'] and c['native_order']==['n','S1','S2','D1','D2']
  assert c['instruction']['operands'][1]['role']=='read_write'
  local='D1' if c['instruction']['mnemonic']=='D.DDRD' else 'S2'
  assert next(p for p in c['instruction']['operands'] if p['name']==local)['digit_groups_max']==4
  assert 'digit_groups_max' not in c['instruction']['operands'][0]
 prior={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'prior-owner-contracts.json').read_text())}
 for e in rows:assert all(f in e['facts'] for f in prior[e['target_model'],e['opcode']]['preserved_facts'])
@pytest.mark.parametrize('op',['D.DDRD','D.DDWR'])
def test_dd_default_projection_delivers_real_identity_and_unverified_boundaries(op):
 from knowledge.fact_selection import project_packet
 from shared.model_context import technical_context_units,technical_knowledge_text
 e=next(e for e in entries() if e['target_model']=='Q03UDV' and e['opcode']==op)
 packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
 record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
 rendered=json.dumps(technical_context_units(technical_knowledge_text(record)))
 for token in ['remote_CPU_device_identity','device_value_read','transfer_word_count','occupied_bit_count','processing_prose_label_consistency_verified','operation_error_count_boundary_consistency_verified','core_execution_established']:
  assert token in rendered,token
