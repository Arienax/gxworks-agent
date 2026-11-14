"""Own X532-539 DWORD to ASCII Native forms; no execution grant."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-bcd-ascii-native-contract-repair')
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
def rows():return read('source-facts.json')['entries']
def fs(e):return {f['id']:f for f in e['facts']}
@pytest.mark.parametrize('base,page,chars,words',[('DBCDDA',538,8,4),('DBINDA',532,11,6),('DBINHA',535,8,4)])
def test_dword_ascii_own_native_width_byte_layout_and_gates(base,page,chars,words):
 for e in [e for e in rows() if e['opcode'] in [base,base+'P']]:
  f=fs(e)['authored:ql:native_arguments_s_d:parameters.typed_operands'];s,d=f['value']['operands']
  assert f['status']=='source_verified' and f['value']['native_form']==dict(operand_order=['S','D'],minimum_operands=2,maximum_operands=2)
  assert {p for ref in f['sources'] for p in ref['pdf_pages']}=={133,page,page+1,page+2}
  assert s['type']=='ANY32' and s['word_device_layout']['word_count']==2 and s['word_device_layout']['value_bits']==32
  assert s['word_device_layout']['word_order']=='low_word_first' and s['word_device_layout']['signed'] is (base=='DBINDA')
  if base=='DBCDDA':assert s['encoding']=='BCD' and s['decimal_digits']==8 and s['decimal_value_range']==dict(minimum=0,maximum=99999999)
  else:assert s['encoding']=='binary' and 'decimal_digits' not in s
  assert d['type']=='STRING' and d['direction']=='output' and d['passing']=='device_reference'
  assert d['string_layout']['fixed_character_count']==chars and d['string_layout']['byte_order']=='low_byte_first'
  assert d['word_device_layout']==dict(device_unit_bits=16,word_count=words,element_bits=8,element_count=chars)
  assert d['range']==dict(literal_prefixes=[],address_forms=[],index_register_operand=False,bit_device_operand=False,device_prefixes=['T','ST','C','D','W','SD','SW','FD','R','ZR'])
  gate=fs(e)['authored:ql:dword_ascii_execution_gate:execution.operation_condition']
  assert gate['value']['enabled_when']==('execution_condition_rising_edge' if e['opcode'].endswith('P') else 'execution_condition_ON')
  assert set(gate['depends_on'])=={'authored:ql:native_arguments_s_d:parameters.typed_operands','authored:ql:cpu_applicability:constraints.cpu_availability'}

def test_dword_ascii_conditional_tail_word_versus_high_byte_and_printed_conflicts():
 for e in rows():
  op=e['opcode'];v=fs(e)['authored:ql:operation:effects.conversion']['value'];d=fs(e)['authored:ql:native_arguments_s_d:parameters.typed_operands']['value']['operands'][1]
  if op.startswith('DBINDA'):
   tail=dict(selector='SM701',word_offset=5,bit_offset=8,value_bits=8,OFF=dict(value=0),ON=dict(value=32),current_selector_value_verified=False)
   assert v['conditional_byte_write']==d['conditional_byte_write']==tail and 'conditional_tail_write' not in d
   assert v['sign_bytes']=={'positive':32,'negative':45} and v['published_example']['output_data_bytes']==[45,32,32,49,50,51,52,53,54,55,56]
   assert v['printed_example_final_word_label_matches_own_diagram'] is False
  else:
   tail=dict(selector='SM701',OFF=dict(word_count=1,value=0),ON='unchanged',current_selector_value_verified=False)
   if op.startswith('DBINHA'):
    tail.update(word_offset_established=False,source_conflict=dict(printed_prose_word_offset=2,own_diagram_word_offset=4))
    assert 'word_offset' not in d['conditional_tail_write']
   else:tail['word_offset']=4
   assert v['conditional_tail_write']==d['conditional_tail_write']==tail
   assert [p['source_low_bit'] for p in v['character_positions']]==[28,24,20,16,12,8,4,0]
   if op.startswith('DBINHA'):
    assert v['printed_tail_word_offset']==2 and v['own_diagram_tail_word_offset']==4 and v['printed_tail_offset_matches_own_diagram'] is False
    assert v['published_example']['source']==int('03AC625E',16) and v['published_example']['output_data_bytes']==[48,51,65,67,54,50,53,69]
    assert v['zero_suppression'] is False
   else:assert v['published_example']['output_data_bytes']==[32,49,50,51,52,48,53,54] and v['zero_suppression'] is True

def test_dword_ascii_own_error_cpu_columns_not_instruction_exclusion():
 for op in ['DBCDDA','DBCDDAP','DBINDA','DBINDAP','DBINHA','DBINHAP']:
  group=[e for e in rows() if e['opcode']==op];assert len(group)==50
  applicable=0
  for e in group:
   f=fs(e);err=f['authored:ql:error:effects.error_response:SM0|SD0']['value']
   yes=e['target_model'].startswith('L') or 'U' in e['target_model'];assert err['destination_extent_error']['CPU_error_row_applicable'] is yes;applicable+=yes
   assert ('4100' in err['documented_codes']) is op.startswith('DBCDDA')
   cpu=f['authored:ql:cpu_applicability:constraints.cpu_availability'];assert cpu['value']['available'] is True and not cpu.get('conditions')
  assert applicable==39

def test_dword_ascii_exact300_full_namespace_prior_structured_preserved():
 entries=rows();assert len(entries)==300 and sum(len(e['facts']) for e in entries)==8000
 assert {(e['target_model'],e['opcode']) for e in entries}=={(e['target_model'],e['opcode']) for e in read('original-owner-selectors.json')}
 history={(e['target_model'],e['opcode']):e for e in read('prior-owner-contracts.json')}
 for e in entries:
  f=fs(e);assert all(f[v['id']]==v for v in history[e['target_model'],e['opcode']]['preserved_facts'])
  assert all(set(v.get('depends_on',[]))<=f.keys() for v in e['facts'])
 proposal=read('contract-additions.json')['cpu_contracts'];assert len(proposal)==6 and sum(len(c['cpus']) for c in proposal)==300
 assert all(c['native_order']==['S','D'] and [p['word_count'] for p in c['instruction']['operands']]==[2,1] for c in proposal)

def test_dword_ascii_default_keeps_sm701_concrete_unknowns_and_own_layout():
 from knowledge.fact_selection import project_packet
 from shared.model_context import technical_context_units,technical_knowledge_text
 def walk(v):
  if isinstance(v,dict):
   yield v
   for x in v.values():yield from walk(x)
  elif isinstance(v,list):
   for x in v:yield from walk(x)
 for op in ['DBCDDA','DBCDDAP','DBINDA','DBINDAP','DBINHA','DBINHAP']:
  e=next(e for e in rows() if e['target_model']=='Q03UDV' and e['opcode']==op)
  packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
  record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
  units=technical_context_units(technical_knowledge_text(record))
  for key in ['current_SM701_verified','zero_value_character_policy_established','source_destination_overlap_behavior_established','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:
   assert any(v.get(key) is False for v in walk(units)),(op,key)
  assert any(v.get('enabled_when')==('execution_condition_rising_edge' if op.endswith('P') else 'execution_condition_ON') for v in walk(units))
  assert any(v.get('selector')=='SM701' and v.get('current_selector_value_verified') is False for v in walk(units))
  if op.startswith('DBINHA'):
   tails=[v for v in walk(units) if v.get('selector')=='SM701' and v.get('word_offset_established') is False]
   assert tails and all('word_offset' not in v for v in tails)
   assert all(v.get('source_conflict')==dict(printed_prose_word_offset=2,own_diagram_word_offset=4) for v in tails)
