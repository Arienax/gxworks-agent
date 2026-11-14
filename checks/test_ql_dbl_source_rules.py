"""DBL(P) own X285/P221–222 signed conversion; Native and structured distinct."""
import copy,json
from pathlib import Path
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-dbl-native-contract-repair')
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
def rows():return read('source-facts.json')['entries']
def facts(e):return {f['id']:f for f in e['facts']}
def test_dbl_original106_full_namespace_history_and_exact_contract_scope():
 es=rows();assert len(es)==106 and sum(len(e['facts']) for e in es)==2756
 assert {(e['target_model'],e['opcode']) for e in es}=={(e['target_model'],e['opcode']) for e in read('original-owner-selectors.json')}
 cs=read('contract-additions.json')['cpu_contracts'];assert len(cs)==2 and all(len(c['cpus'])==53 and c['native_order']==['S','D'] for c in cs)
 assert all([p['data_type'] for p in c['instruction']['operands']]==['word','dword'] and [p['word_count'] for p in c['instruction']['operands']]==[1,2] for c in cs)
 history={(e['target_model'],e['opcode']):e for e in read('prior-owner-contracts.json')}
 changes=read('source-corrections.json')['changes'];assert len(changes)==1166 and sum(c['before'] is None for c in changes)==318 and not any(c['after'] is None for c in changes)
 for e in es:
  fs=facts(e);assert all(fs[f['id']]==f for f in history[e['target_model'],e['opcode']]['preserved_facts'])
  assert all(set(f.get('depends_on',[]))<=fs.keys() for f in e['facts'])
def test_dbl_own_one_word_source_two_word_signed_result_layout_and_device_table():
 for e in rows():
  f=facts(e)['authored:ql:native_arguments_s_d:parameters.typed_operands'];assert f['status']=='source_verified'
  assert f['value']['native_form']==dict(operand_order=['S','D'],minimum_operands=2,maximum_operands=2)
  assert {p for s in f['sources'] for p in s['pdf_pages']}>={86,88,133,285}
  s,d=f['value']['operands'];assert s['type']=='ANY16' and d['type']=='ANY32'
  assert s['word_device_layout']==dict(device_unit_bits=16,word_count=1,value_bits=16,signed=True)
  assert d['word_device_layout']==dict(device_unit_bits=16,word_count=2,value_bits=32,signed=True,word_order='low_word_first')
  assert 'passing' not in s and d['passing']=='device_reference'
  assert s['range']['minimum']==-32768 and s['range']['maximum']==32767 and s['range']['literal_prefixes']==['K','H'] and d['range']['literal_prefixes']==[]
  assert s['range']['digit_groups_max']==4 and d['range']['digit_groups_max']==8
  assert s['range']['address_forms']==d['range']['address_forms']==['buffer_memory','link_direct']
  assert all(x in s['range']['device_prefixes'] and x in d['range']['device_prefixes'] for x in ['R','ZR','D','M','Z'])
def test_dbl_native_and_structured_own_signed_extension_full_relation_keep_unknowns():
 for e in rows():
  fs=facts(e)
  for suffix,manual,page,source,destination in [('', 'sh080809engx',285,'S','D'),(':variant:2','sh080783engp',222,'s','d')]:
   f=fs['authored:ql:operation:effects.conversion'+suffix];v=f['value'];assert f['status']=='source_verified'
   assert any(s['manual_id']==manual and page in s['pdf_pages'] for s in f['sources'])
   assert v['operation']=='signed_BIN16_to_signed_BIN32' and v['source']==source and v['destination']==destination
   assert v['result_relation']==dict(operator='sign_extend',source_bits=16,destination_bits=32,preserve_signed_integer_value=True)
   assert v['destination_word_layout']==dict(lower_16_bits_word_offset=0,upper_16_bits_word_offset=1)
   assert v['published_example']==dict(source_hex='FB2E',source_value=-1234,destination_hex='FFFFFB2E',destination_value=-1234)
   assert v['full_source_relation_complete'] is True
   for key in ['source_destination_overlap_behavior_established','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:assert v[key] is False
  assert fs['authored:ql:dbl_execution_gate:execution.operation_condition']['value']['enabled_when']==('execution_condition_rising_edge' if e['opcode']=='DBLP' else 'execution_condition_ON')
  assert fs['authored:ql:error:effects.error_response:none|none']['value']['operation_error']=='none'
  assert fs['authored:ql:error:effects.error_response:none|none:variant:2']['value']['operation_error']=='none'
  f=fs['authored:ql:dbl_structured_noerror:effects.error_response:none|none'];assert f['value']['operation_error']=='none' and f['conditions']==[dict(context='project_mode',equals='structured_ladder_block')]
def test_dbl_own_six_cpu_header_all53_supported_without_borrowed_version_guard():
 for e in rows():
  fs=facts(e)
  for suffix,page in [('',285),(':variant:2',221)]:
   f=fs['authored:ql:cpu_applicability:constraints.cpu_availability'+suffix];assert f['status']=='source_verified' and f['value']['available'] is True and not f.get('conditions')
   assert any(page in s['pdf_pages'] for s in f['sources'])
  assert not any(f.get('conditions') and any(c.get('kind')=='serial_number_minimum' for c in f['conditions']) for f in e['facts'])
def test_dbl_prior_structured_ST_FBD_ladder_pins_signatures_and_error_are_preserved():
 baseline={(e['target_model'],e['opcode']):e for e in read('baseline-source.json')['entries']}
 for e in rows():
  fs=facts(e)
  for old in baseline[e['target_model'],e['opcode']]['facts']:
   if old['id'].startswith('authored:ql:argument_table_'):assert old==fs[old['id']]
  st=fs['authored:ql:argument_table_st:parameters.st_signature']['value'];assert st['available'] is True and [p['type'] for p in st['parameters']]==['BOOL','ANY16','ANY32']

def test_dbl_actual_composed_namespace_and_default_native_structured_full_relation():
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
 for op in ['DBL','DBLP']:
  e=cs['Q03UDV',op]
  for mode in ['native_ladder_instruction','structured_ladder_block']:
   packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode=mode,aspects=['programming_semantics'])
   record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode=mode,delivery_languages=['ladder']))
   units=technical_context_units(technical_knowledge_text(record));v=next(v for v in walk(units) if v.get('operation')=='signed_BIN16_to_signed_BIN32')
   assert v['result_relation']['operator']=='sign_extend' and v['full_source_relation_complete'] is True
   assert v['destination_word_layout']==dict(lower_16_bits_word_offset=0,upper_16_bits_word_offset=1)
   for key in ['source_destination_overlap_behavior_established','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:assert v[key] is False
   assert any(v.get('operation_error')=='none' for v in walk(units))
   if mode=='native_ladder_instruction':assert any(v.get('enabled_when')==('execution_condition_rising_edge' if op=='DBLP' else 'execution_condition_ON') for v in walk(units))
