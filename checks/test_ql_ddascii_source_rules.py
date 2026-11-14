"""Own 32-bit DDABCD/DDABIN Native and P sub-sections, not D-prefix inference."""
import copy,json
from pathlib import Path
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-ddabcd-native-contract-repair')
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
def rows():return read('source-facts.json')['entries']
def facts(e):return {f['id']:f for f in e['facts']}
def test_ddascii_exact_original_QL200_complete_namespace_and_history():
 es=rows();assert len(es)==200 and sum(len(e['facts']) for e in es)==5500
 assert {(e['target_model'],e['opcode']) for e in es}=={(s['target_model'],s['opcode']) for s in read('original-owner-selectors.json')}
 assert all(e['target_model'].startswith(('Q','L')) for e in es)
 hist={(e['target_model'],e['opcode']):e for e in read('prior-owner-contracts.json')}
 for e in es:
  fs=facts(e);assert all(fs[f['id']]==f for f in hist[e['target_model'],e['opcode']]['preserved_facts'])
  assert all(set(f.get('depends_on',[]))<=fs.keys() for f in e['facts'])
 cs=read('contract-additions.json')['cpu_contracts'];assert len(cs)==4 and all(len(c['cpus'])==50 and c['native_order']==['S','D'] for c in cs)
 assert all([p['data_type'] for p in c['instruction']['operands']]==['word','dword'] and [p['word_count'] for p in c['instruction']['operands']]==[1,2] for c in cs)
 for c in cs:
  bcd=c['instruction']['mnemonic'].startswith('DDABCD');src,dst=c['instruction']['operands']
  assert src['semantic_name']==('fixed_eight_decimal_ASCII_storage_head_or_character_literal' if bcd else 'sign_and_ten_decimal_ASCII_storage_head_or_character_literal')
  assert dst['semantic_name']==('packed_eight_BCD_digit_result_head' if bcd else 'signed32_binary_result_head')
def test_ddascii_native_self_proven_distinct_payloads_and_device_tables():
 for e in rows():
  bcd=e['opcode'].startswith('DDABCD');f=facts(e)['authored:ql:native_arguments_s_d:parameters.typed_operands'];assert f['status']=='source_verified'
  src,dst=f['value']['operands'];assert src['type']=='STRING' and dst['type']=='ANY32'
  assert src['string_layout']['fixed_character_count']==(8 if bcd else 11)
  assert src['word_device_layout']==dict(kind='fixed_character_sequence',device_unit_bits=16,word_count=4 if bcd else 6,element_bits=8,element_count=8 if bcd else 11,single_scalar=False)
  assert dst['word_device_layout']==dict(device_unit_bits=16,word_count=2,value_bits=32,signed=not bcd,word_order='low_word_first')
  assert src['range']['literal_prefixes']==['$'] and src['range']['address_forms']==[] and src['range']['index_register_operand'] is False
  assert src['range']['device_prefixes']==['T','ST','C','D','W','SD','SW','FD','R','ZR']
  assert dst['range']['digit_groups_max']==8 and dst['range']['literal_prefixes']==[] and dst['range']['address_forms']==['buffer_memory','link_direct']
  assert {p for s in f['sources'] for p in s['pdf_pages']}>={88,133,547 if bcd else 541,548 if bcd else 542}
  if not bcd:assert src['ignored_source_byte']==dict(word_offset=5,byte_offset=1)
def test_ddabcd_own_eight_digit_nibble_mapping_does_not_resolve_printed_zero_interval():
 for e in [e for e in rows() if e['opcode'].startswith('DDABCD')]:
  fs=facts(e)
  for suffix,manual in [('', 'sh080809engx'),(':variant:2','sh080783engp')]:
   f=fs['authored:ql:operation:effects.conversion'+suffix];v=f['value'];assert f['status']=='source_verified' and any(s['manual_id']==manual for s in f['sources'])
   assert v['operation']=='fixed_eight_ASCII_to_BCD8' and v['source_character_count']==8 and v['source_word_count']==4
   assert v['character_positions']==[dict(source_word_offset=i//2,source_byte_offset=i%2,destination_nibble_bit_offset=4*(7-i)) for i in range(8)]
   assert v['destination_signed'] is False and v['valid_decimal_range']==[0,99999999]
   assert v['known_space_ASCII_code']==32 and v['space_digit_value']==0
   assert v['printed_zero_normalization']==dict(from_ASCII_code=32,to_ASCII_code=0,result_ASCII_code=48)
   assert v['printed_normalization_interval_established'] is v['zero_byte_handling_established'] is v['full_source_relation_complete'] is False
   assert v['published_example']==dict(source_words=[0x3738,0x3536,0x3334,0x3132],destination_BCD_hex='87654321')
def test_ddabin_own_sign_ten_digits_ignored_high_byte_and_signed32_range():
 for e in [e for e in rows() if e['opcode'].startswith('DDABIN')]:
  fs=facts(e)
  for suffix,manual in [('', 'sh080809engx'),(':variant:2','sh080783engp')]:
   f=fs['authored:ql:operation:effects.conversion'+suffix];v=f['value'];assert f['status']=='source_verified' and any(s['manual_id']==manual for s in f['sources'])
   assert v['source_character_count']==11 and v['source_word_count']==6 and v['destination_signed'] is True
   assert v['sign_position']==dict(source_word_offset=0,source_byte_offset=0) and v['ignored_source_byte']==dict(source_word_offset=5,source_byte_offset=1)
   assert v['character_positions']==[dict(source_word_offset=i//2,source_byte_offset=i%2,decimal_multiplier=10**(10-i)) for i in range(1,11)]
   assert v['valid_decimal_range']==[-2147483648,2147483647] and v['negative_sign_ASCII_code']==45 and v['positive_sign_ASCII_code']==32 and v['other_sign_ASCII_codes_processed_as']=='positive'
   assert v['zero_digit_ASCII_codes']==[0,32] and v['string_terminator_stops_conversion'] is False
   assert v['character_error_predicate_including_sign_and_ignored_byte_complete'] is v['full_source_relation_complete'] is False
   assert v['published_example']['destination_value']==-1234543210
def test_ddascii_trigger_cpu_error_columns_and_structured_interfaces_preserved():
 baseline={(e['target_model'],e['opcode']):e for e in read('baseline-source.json')['entries']}
 for e in rows():
  fs=facts(e);assert fs['authored:ql:ddascii_execution_gate:execution.operation_condition']['value']['enabled_when']==('execution_condition_rising_edge' if e['opcode'].endswith('P') else 'execution_condition_ON')
  err=fs['authored:ql:error:effects.error_response:SM0|SD0']['value'];applies=e['target_model'].startswith('L') or 'U' in e['target_model']
  assert err['numeric_or_character_error']['code']==4100 and err['numeric_or_character_error']['CPU_error_row_applicable'] is True
  assert err['source_extent_error']['code']==4101 and err['source_extent_error']['CPU_error_row_applicable']==applies
  assert err['printed_character_error_predicate_complete'] is False
  for suffix in ['',':variant:2']:
   c=fs['authored:ql:cpu_applicability:constraints.cpu_availability'+suffix];assert c['value']['available'] is True and not c.get('conditions')
  for old in baseline[e['target_model'],e['opcode']]['facts']:
   if old['id'].startswith('authored:ql:argument_table_'):assert old==fs[old['id']]
  for suffix in ['',':variant:2']:
   v=fs['authored:ql:operation:effects.conversion'+suffix]['value']
   for key in ['short_literal_padding_behavior_established','source_destination_overlap_behavior_established','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:assert v[key] is False

def test_ddascii_actual_composed_namespace_and_default_known_mapping_and_partial_boundaries():
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
 for op in ['DDABCD','DDABCDP','DDABIN','DDABINP']:
  e=cs['Q03UDV',op];bcd=op.startswith('DDABCD')
  for mode in ['native_ladder_instruction','structured_ladder_block']:
   packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode=mode,aspects=['programming_semantics'])
   record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode=mode,delivery_languages=['ladder']))
   units=technical_context_units(technical_knowledge_text(record));v=next(v for v in walk(units) if v.get('operation')==('fixed_eight_ASCII_to_BCD8' if bcd else 'fixed_sign_and_ten_decimal_ASCII_to_signed_BIN32'))
   assert v['source_character_count']==(8 if bcd else 11) and v['source_word_count']==(4 if bcd else 6) and v['destination_word_count']==2
   assert v['valid_digit_position_mapping_established'] is True and v['full_source_relation_complete'] is False
   for key in ['short_literal_padding_behavior_established','source_destination_overlap_behavior_established','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:assert v[key] is False
   if bcd:assert v['printed_normalization_interval_established'] is v['zero_byte_handling_established'] is False and v['printed_zero_normalization']==dict(from_ASCII_code=32,to_ASCII_code=0,result_ASCII_code=48)
   else:assert v['ignored_source_byte']==dict(source_word_offset=5,source_byte_offset=1) and v['character_error_predicate_including_sign_and_ignored_byte_complete'] is False
   if mode=='native_ladder_instruction':assert any(v.get('enabled_when')==('execution_condition_rising_edge' if op.endswith('P') else 'execution_condition_ON') for v in walk(units))
