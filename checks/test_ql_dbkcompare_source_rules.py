"""Own X206–208 DBKCMP six relations and pulse forms; no execution proof."""
import copy,json
from pathlib import Path
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-dbkcompare-native-contract-repair')
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
def rows():return read('source-facts.json')['entries']
def facts(e):return {f['id']:f for f in e['facts']}
def test_dbkcompare_exact_12_original_units_and_full_namespace_history():
 es=rows();assert len(es)==468
 assert {(e['target_model'],e['opcode']) for e in es}=={(s['target_model'],s['opcode']) for s in read('original-owner-selectors.json')}
 old={(e['target_model'],e['opcode']):e for e in read('prior-owner-contracts.json')}
 for e in es:
  fs=facts(e);assert all(fs[f['id']]==f for f in old[e['target_model'],e['opcode']]['preserved_facts'])
  assert all(set(f.get('depends_on',[]))<=fs.keys() for f in e['facts'])
 cs=read('contract-additions.json')['cpu_contracts'];assert len(cs)==12 and sum(len(c['cpus']) for c in cs)==468
 assert all(c['native_order']==['S1','S2','D','n'] and [p['data_type'] for p in c['instruction']['operands']]==['dword','dword','bool','word'] for c in cs)
def test_dbkcompare_own_signed32_head_and_two_n_word_inputs_bit_output_bin16_count():
 for e in rows():
  f=facts(e)['authored:ql:native_arguments_s1_s2_d_n:parameters.typed_operands'];assert f['status']=='source_verified'
  assert {p for s in f['sources'] for p in s['pdf_pages']}>={133,206,207,208}
  s1,s2,d,n=f['value']['operands']
  for p in [s1,s2]:
   assert p['type']=='ANY32' and p['word_device_layout']==dict(device_unit_bits=16,word_count=2,value_bits=32,signed=True,word_order='low_word_first',head_storage_only=True)
   assert p['region_span']==dict(unit='word',count_expression=dict(operator='multiply',operands=[2,{'parameter':'n'}]),element_bits=32,element_count_operand='n',stride_words=2)
  assert 'passing' not in s1 and s1['constant_source']=='same_value_for_every_element' and s1['literal_has_no_device_payload'] is True
  assert s1['range']['minimum']==-2147483648 and s1['range']['maximum']==2147483647 and s2['range']['literal_prefixes']==[]
  assert d['type']=='BOOL' and 'word_device_layout' not in d and d['occupied_bit_count']['name']=='n'
  assert d['word_device_requires_bit_designation'] is True and d['cross_word_sequence_established'] is False
  assert n['type']=='ANY16' and n['word_device_layout']==dict(device_unit_bits=16,word_count=1,value_bits=16,signed=True)
  assert n['range']['minimum']==0 and 'maximum' not in n['range']
def test_dbkcompare_six_literal_relations_and_trigger_are_individually_bound():
 for e in rows():
  op=e['opcode'];fs=facts(e);relation=op[6:-1] if op.endswith('P') else op[6:]
  v=fs['authored:ql:literal_comparison:effects.operation']['value']
  assert v['comparison']==relation and v['result_relation']['operator']==relation
  assert v['result_relation']['left']=='S1_at_element_index_or_repeated_constant' and v['result_relation']['right']=='S2_at_element_index'
  assert v['result_relation']['true_value']==1 and v['result_relation']['false_value']==0 and v['source_element_relation_complete'] is True
  assert v['zero_n']=='not_processed' and v['unselected_word_device_bits']=='unchanged'
  assert v['cross_word_sequence_established'] is v['printed_processing_constant_S2_phrase_resolved'] is v['core_execution_established'] is v['command_FALSE_output_behavior_established'] is False
  gate=fs['authored:ql:dbkcompare_execution_gate:execution.operation_condition']['value']
  assert gate['enabled_when']==('execution_condition_rising_edge' if op.endswith('P') else 'execution_condition_ON')
def test_dbkcompare_own_cpu_header_17_conditional_and_22_unconditional_each_form():
 for op in {e['opcode'] for e in rows()}:
  groups=[0,0]
  for e in [e for e in rows() if e['opcode']==op]:
   fs=facts(e);a=fs['authored:ql:cpu_applicability:constraints.cpu_availability'];b=fs['authored:ql:cpu_applicability:constraints.cpu_availability:variant:2']
   assert a['status']==b['status']=='source_verified' and a['value']['available'] is b['value']['available'] is True
   assert any(s['manual_id']=='sh080809engx' and 206 in s['pdf_pages'] for s in a['sources'])
   assert any(s['manual_id']=='sh080783engp' and 161 in s['pdf_pages'] for s in b['sources'])
   guarded=e['target_model'].startswith('Q') and e['target_model'] not in {'Q00U','Q00UJ','Q01U'} and not ('UDV' in e['target_model'] or 'UDPV' in e['target_model']);groups[guarded]+=1
   if guarded:assert '10102' in json.dumps(a['conditions']) and '10102' in json.dumps(b['conditions'])
   else:assert '10102' not in json.dumps(e['facts'])
  assert groups==[22,17]
def test_dbkcompare_overlap_error_flags_and_old_structured_values_stay_separate():
 old={(e['target_model'],e['opcode']):e for e in read('baseline-source.json')['entries']}
 for e in rows():
  fs=facts(e)
  for i,name in enumerate(['s1','s2']):
   v=fs['authored:ql:dbkcompare_overlap_'+name+':constraints.disjoint_regions']['value']
   assert v['source_operand_index']==i and v['destination_operand_index']==2 and v['source_length_multiplier']==2 and v['source_unit']=='words' and v['destination_unit']=='bits' and 'allow_identical' not in v
  v=fs['authored:ql:error:effects.error_response:SM0|SD0']['value'];assert v['documented_codes']['4100']=='negative_n' and v['identical_head_exception_stated'] is False
  flags=fs['authored:ql:dbkcompare_result_flags:effects.special_devices']['value'];assert flags['program_specific']['SM718']==['interrupt_I45'] and flags['n_zero_flag_behavior_established'] is False
  for f in old[e['target_model'],e['opcode']]['facts']:
   if not f['id'].startswith('authored:ql:argument_table_'):continue
   before=copy.deepcopy(f['value']);after=copy.deepcopy(fs[f['id']]['value']);before.pop('required_context',None);after.pop('required_context',None);assert before==after

def test_dbkcompare_actual_composition_namespace_and_default_relations_preserve_partial_boundaries():
 from knowledge.fact_selection import project_packet
 from shared.model_context import technical_context_units,technical_knowledge_text
 def walk(v):
  if isinstance(v,dict):
   yield v
   for x in v.values():yield from walk(x)
  elif isinstance(v,list):
   for x in v:yield from walk(x)
 cs={(e['target_model'],e['opcode']):e for e in read('composed-own-source.json')['entries']}
 for e in rows():
  active=facts(cs[e['target_model'],e['opcode']]);assert all(active[f['id']]==f for f in e['facts'])
 for op in {e['opcode'] for e in rows()}:
  e=cs['Q03UDV',op];relation=op[6:-1] if op.endswith('P') else op[6:]
  for mode in ['native_ladder_instruction','structured_ladder_block']:
   packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode=mode,aspects=['programming_semantics'])
   record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode=mode,delivery_languages=['ladder']))
   units=technical_context_units(technical_knowledge_text(record));assert any(v.get('operation')=='compare_block_elements' and v.get('comparison')==relation and v.get('width_bits')==32 for v in walk(units))
   if mode=='native_ladder_instruction':
    for key in ['cross_word_sequence_established','printed_processing_constant_S2_phrase_resolved','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:
     assert any(v.get(key) is False for v in walk(units)),(op,key)
    assert any(v.get('zero_n')=='not_processed' for v in walk(units))
   assert '10102' not in json.dumps(units)
