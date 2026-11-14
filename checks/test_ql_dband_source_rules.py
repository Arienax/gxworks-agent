"""DBAND/P own X669-670 signed two-word facts, preserving structured interfaces."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-dband-native-contract-repair')
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
def entries():return read('source-facts.json')['entries']
def facts(e):return {f['id']:f for f in e['facts']}
@pytest.mark.parametrize('op',['DBAND','DBANDP'])
def test_dband_own_four_signed32_slots_two_words_and_own_trigger(op):
 for e in [e for e in entries() if e['opcode']==op]:
  fs=facts(e);f=fs['authored:ql:native_arguments_s1_s2_s3_d:parameters.typed_operands']
  assert f['status']=='source_verified' and f['value']['native_form']==dict(operand_order=['S1','S2','S3','D'],minimum_operands=4,maximum_operands=4)
  assert {p for s in f['sources'] for p in s['pdf_pages']}=={669,670}
  for p in f['value']['operands']:
   output=p['name']=='D'
   assert p['type']=='ANY32' and p['encoding']=='binary' and p['form_width_bits']==32
   assert p['direction']==('output' if output else 'input') and p['semantic_access']==('write' if output else 'read')
   assert p['word_device_layout']==dict(device_unit_bits=16,word_count=2,value_bits=32,signed=True)
   assert p['range']==dict(minimum=-2147483648,maximum=2147483647,literal_prefixes=[] if output else ['K','H'])
   assert p['documented_device_categories']==['internal_bit','internal_word','file_register','link_direct_bit','link_direct_word','buffer_memory','index_register']
  gate=fs['authored:ql:dband_execution_gate:execution.operation_condition']
  assert gate['value']['enabled_when']==('execution_condition_rising_edge' if op.endswith('P') else 'execution_condition_ON')
  assert set(gate['depends_on'])=={'authored:ql:native_arguments_s1_s2_s3_d:parameters.typed_operands','authored:ql:cpu_applicability:constraints.cpu_availability'}

def test_dband_own_inclusive_dead_band_and_specific_overflow_example_not_algorithm():
 for e in entries():
  fs=facts(e);v=fs['authored:ql:operation:effects.data_control']['value']
  assert v['operation']=='signed_32bit_dead_band_control' and v['value_bits']==32 and v['signed'] is True
  assert v['cases']==[dict(condition='S3_less_than_S1',result_operation='subtract',left='S3',right='S1'),dict(condition='S3_greater_than_S2',result_operation='subtract',left='S3',right='S2'),dict(condition='S1_less_than_or_equal_S3_less_than_or_equal_S2',result_value=0)]
  assert v['published_overflow_example']==dict(lower_limit=1000,input=-2147483648,output=2147482648)
  assert v['general_overflow_algorithm_established'] is False
  err=fs['authored:ql:error:effects.error_response:SM0|SD0']['value'];assert err['documented_codes']=={'4100':'lower_limit_greater_than_upper_limit'}
  assert err['error_flags']==['SM0'] and err['error_code_registers']==['SD0'] and len(err['error_CPU_columns'])==6

def test_dband_exact_original_scope_complete_namespace_and_unchanged_structured_interfaces():
 rows=entries();assert len(rows)==106 and sum(len(e['facts']) for e in rows)==2862
 assert {(e['target_model'],e['opcode']) for e in rows}=={(e['target_model'],e['opcode']) for e in read('original-owner-selectors.json')}
 assert read('contract-additions.json')=={'cpu_contracts':[]}
 prior={(e['target_model'],e['opcode']):e for e in read('prior-owner-contracts.json')}
 for e in rows:
  fs=facts(e);old=prior[e['target_model'],e['opcode']]
  assert all(fs[f['id']]==f for f in old['preserved_facts'])
  assert all(set(f.get('depends_on',[]))<=fs.keys() for f in e['facts'])
  for f in old['preserved_facts']:
   if any(s['manual_id']=='sh080783engp' for s in f.get('sources',[])):assert fs[f['id']]==f
  cpu=fs['authored:ql:cpu_applicability:constraints.cpu_availability'];assert cpu['status']=='source_verified' and cpu['value']['available'] is True and not cpu.get('conditions')

def test_dband_default_new_concrete_behavior_and_unknown_boundaries():
 from knowledge.fact_selection import project_packet
 from shared.model_context import technical_context_units,technical_knowledge_text
 e=next(e for e in entries() if e['target_model']=='Q00' and e['opcode']=='DBANDP')
 packet=project_packet(dict(kind='instruction',target=e['opcode'],scope=dict(models=['Q00']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q00',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
 record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=e['opcode'],_knowledge_context=dict(model='Q00',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
 def walk(v):
  if isinstance(v,dict):
   yield v
   for x in v.values():yield from walk(x)
  elif isinstance(v,list):
   for x in v:yield from walk(x)
 units=technical_context_units(technical_knowledge_text(record))
 assert any(v.get('operation')=='signed_32bit_dead_band_control' for v in walk(units))
 assert any(v.get('enabled_when')=='execution_condition_rising_edge' for v in walk(units))
 for key in ['general_overflow_algorithm_established','source_destination_overlap_behavior_established','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:
  assert any(v.get(key) is False for v in walk(units)),key
 assert any(v.get('word_count')==2 and v.get('value_bits')==32 and v.get('signed') is True for v in walk(units))
