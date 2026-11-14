"""Own X263/P195 DBK- forms, BIN16 n and signed32 elements."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-dbkm-native-contract-repair')
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
def rows():return read('source-facts.json')['entries']
def facts(e):return {f['id']:f for f in e['facts']}
@pytest.mark.parametrize('op',['DBK-','DBK-P'])
def test_dbkm_native_signed32_head_bin16_count_and_two_n_word_payload(op):
 for e in [e for e in rows() if e['opcode']==op]:
  f=facts(e)['authored:ql:native_arguments_s1_s2_d_n:parameters.typed_operands'];assert f['status']=='source_verified'
  assert f['value']['native_form']==dict(operand_order=['S1','S2','D','n'],minimum_operands=4,maximum_operands=4)
  assert {p for s in f['sources'] for p in s['pdf_pages']} >= {133,263,264}
  for p in f['value']['operands'][:3]:
   assert p['type']=='ANY32' and p['encoding']=='binary'
   assert p['word_device_layout']==dict(device_unit_bits=16,word_count=2,value_bits=32,signed=True,word_order='low_word_first',head_storage_only=True)
   assert p['region_span']==dict(unit='word',count_expression=dict(operator='multiply',operands=[2,{'parameter':'n'}]),element_bits=32,element_count_operand='n',stride_words=2)
   assert p['range']['device_prefixes']==['T','ST','C','D','W','SD','SW','FD','R','ZR'] and p['range']['address_forms']==[]
  s1,s2,d,n=f['value']['operands']
  assert s1['passing']==d['passing']=='device_reference' and 'passing' not in s2
  assert s2['range']['literal_prefixes']==['K','H'] and s2['range']['minimum']==-2147483648 and s2['range']['maximum']==2147483647
  assert s2['literal_has_no_device_payload'] is True and s2['constant_source']=='same_value_for_every_element'
  assert n['type']=='ANY16' and n['word_device_layout']==dict(device_unit_bits=16,word_count=1,value_bits=16,signed=True)
  assert n['range']['minimum']==0 and 'maximum' not in n['range'] and n['maximum_count_explicitly_stated'] is False
  gate=facts(e)['authored:ql:dbkm_execution_gate:execution.operation_condition'];assert gate['value']['enabled_when']==('execution_condition_rising_edge' if op.endswith('P') else 'execution_condition_ON')
  assert gate['value']['zero_n']=='not_processed'

def test_dbkm_own_cpu_explicit_supported_list_and_seventeen_serial_gates_each_form():
 for op in ['DBK-','DBK-P']:
  count=[0,0]
  for e in [e for e in rows() if e['opcode']==op]:
   f=facts(e);cpu=f['authored:ql:cpu_applicability:constraints.cpu_availability'];structured=f['authored:ql:cpu_applicability:constraints.cpu_availability:variant:2']
   assert cpu['status']==structured['status']=='source_verified' and cpu['value']['available'] is structured['value']['available'] is True
   assert any(s['manual_id']=='sh080809engx' and 263 in s['pdf_pages'] for s in cpu['sources'])
   assert any(s['manual_id']=='sh080783engp' and 195 in s['pdf_pages'] for s in structured['sources'])
   guarded=e['target_model'].startswith('Q') and e['target_model'] not in {'Q00U','Q00UJ','Q01U'} and not ('UDV' in e['target_model'] or 'UDPV' in e['target_model'])
   count[guarded]+=1
   if guarded:assert '10102' in json.dumps(cpu['conditions']) and '10102' in json.dumps(structured['conditions'])
   else:assert not cpu.get('conditions') and not structured.get('conditions') and '10102' not in json.dumps(e['facts'])
  assert count==[22,17]

def test_dbkm_element_relation_is_not_scalar_add_count_and_alias_rules_are_preserved():
 for e in rows():
  f=facts(e);v=f['authored:ql:literal_arithmetic:effects.operation']['value']
  assert v['operation']=='block_subtraction' and v['inputs']==['S1','S2'] and v['count_operand']=='n' and v['element_bits']==32
  assert v['result_relation']==dict(element_index='0_through_n_minus_1',left='S1_at_element_index',operator='subtract',right='S2_at_element_index_or_repeated_constant',result='D_at_element_index',element_device_word_stride=2,element_bits=32)
  assert v['source_element_relation_complete'] is True and v['zero_n']=='not_processed' and v['overflow_sets_carry_flag'] is False
  assert v['overflow_examples']==[dict(left=2147483647,right=-2,result=-2147483647),dict(left=-2147483647,right=2,result=2147483647)]
  assert v['general_overflow_algorithm_established'] is False and v['SM700_clear_behavior_established'] is False
  p=f['authored:ql:literal_arithmetic:effects.operation:variant:2']['value']
  assert p['operation']=='block_subtraction_with_documented_direction_conflict' and p['element_bits']==32
  assert p['direction_established'] is p['cross_manual_direction_resolution_performed'] is False and 'result_relation' not in p
  assert p['source_conflict']['processing']['result_relation']['left']=='S2_at_element_index'
  assert p['source_conflict']['diagram_and_program']['result_relation']['left']=='S1_at_element_index'
  assert p['source_conflict']['processing']['source']==dict(manual_id='sh080783engp',revision='P',pdf_pages=[196],section='DBK_minus_processing')
  assert p['source_conflict']['diagram_and_program']['source']==dict(manual_id='sh080783engp',revision='P',pdf_pages=[196,197],section='DBK_minus_diagram_and_program')
  assert f['authored:ql:dbkm_nonnegative_count:constraints.numeric_ranges:n']['value']['ranges']==[dict(operand_index=3,minimum=0)]
  for i,name in enumerate(['s1','s2']):
   r=f['authored:ql:dbkm_overlap_'+name+':constraints.disjoint_regions']['value']
   assert r['source_operand_index']==i and r['destination_operand_index']==2 and r['source_length_operand_index']==r['destination_length_operand_index']==3
   assert r['source_length_multiplier']==r['destination_length_multiplier']==2 and 'allow_identical' not in r
   assert r['identical_region_policy_established'] is False
   conflict=r['identical_region_source_conflict'];assert set(conflict)=={'processing','error_table'}
   assert conflict['processing']['claim']=='destination_outside_source_blocks'
   assert conflict['error_table']['claim']=='identical_source_destination_heads_excepted_from_overlap_error'
   assert all(v['source']['manual_id']=='sh080809engx' and v['source']['pdf_pages']==[264] for v in conflict.values())
  error=f['authored:ql:error:effects.error_response:SM0|SD0']['value']
  assert error['identical_region_policy_established'] is False and error['partial_overlap_prohibited'] is True
  assert error['source_source_same_head_allowed'] is True and 'identical_source_destination_head_allowed' not in error

def test_dbkm_complete_exact_original_namespace_history_and_structured_interface_values():
 entries=rows();assert len(entries)==78 and sum(len(e['facts']) for e in entries)==2174
 assert {(e['target_model'],e['opcode']) for e in entries}=={(e['target_model'],e['opcode']) for e in read('original-owner-selectors.json')}
 history={(e['target_model'],e['opcode']):e for e in read('prior-owner-contracts.json')}
 changes=read('source-corrections.json')['changes'];assert len(changes)==1394
 assert sum(c['before'] is None for c in changes)==312 and sum(c['after'] is None for c in changes)==30
 assert sum(c['before'] is not None and c['after'] is not None for c in changes)==1052
 for e in entries:
  fs=facts(e);old=history[e['target_model'],e['opcode']]
  assert all(fs[f['id']]==f for f in old['preserved_facts']) and all(set(f.get('depends_on',[]))<=fs.keys() for f in e['facts'])
  for f in old['replaced_facts']:
   if 'argument_table_' not in f['id']:continue
   before=copy.deepcopy(f['value']);after=copy.deepcopy(fs[f['id']]['value']);before.pop('required_context',None);after.pop('required_context',None);assert before==after
 c=read('contract-additions.json')['cpu_contracts'];assert len(c)==2 and all(len(r['cpus'])==39 for r in c)
 assert all(r['native_order']==['S1','S2','D','n'] and [p['word_count'] for p in r['instruction']['operands']]==[2,2,2,1] for r in c)

def test_dbkm_default_keeps_block_relation_zero_count_alias_and_concrete_boundaries():
 from knowledge.fact_selection import project_packet
 from shared.model_context import technical_context_units,technical_knowledge_text
 def walk(v):
  if isinstance(v,dict):
   yield v
   for x in v.values():yield from walk(x)
  elif isinstance(v,list):
   for x in v:yield from walk(x)
 for op in ['DBK-','DBK-P']:
  e=next(e for e in read('composed-own-source.json')['entries'] if e['target_model']=='Q03UDV' and e['opcode']==op)
  packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
  record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
  units=technical_context_units(technical_knowledge_text(record));assert any(v.get('operation')=='block_subtraction' and v.get('element_bits')==32 and 'result_relation' in v for v in walk(units))
  assert any(v.get('enabled_when')==('execution_condition_rising_edge' if op.endswith('P') else 'execution_condition_ON') for v in walk(units))
  assert any(v.get('zero_n')=='not_processed' for v in walk(units))
  for key in ['general_overflow_algorithm_established','SM700_clear_behavior_established','maximum_count_explicitly_stated','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:
   assert any(v.get(key) is False for v in walk(units)),key
  assert '10102' not in json.dumps(units)
  assert any(v.get('identical_region_policy_established') is False and 'identical_region_source_conflict' in v for v in walk(units))
  packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode='structured_ladder_block',aspects=['programming_semantics'])
  record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='structured_ladder_block',delivery_languages=['ladder']))
  structured=technical_context_units(technical_knowledge_text(record))
  assert any(v.get('operation')=='block_subtraction_with_documented_direction_conflict' and v.get('direction_established') is False for v in walk(structured))
  assert not any(v.get('operation')=='block_subtraction' and v.get('source_element_relation_complete') is True for v in walk(structured))
