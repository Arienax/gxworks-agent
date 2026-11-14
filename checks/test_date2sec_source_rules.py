"""X699-700 owns DATE2SEC(P)'s six-word input and selective CPU condition."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-date2sec-native-contract-repair')
def load(name):return json.loads((HERE/name).read_text(encoding='utf-8'))
def rows():return load('source-facts.json')['entries']
def fs(e):return {f['id']:f for f in e['facts']}
@pytest.mark.parametrize('op',['DATE2SEC','DATE2SECP'])
def test_date2sec_own_native_six_word_record_and_binary_seconds(op):
 for e in [e for e in rows() if e['opcode']==op]:
  facts=fs(e);f=facts['authored:ql:native_arguments_s_d:parameters.typed_operands'];s,d=f['value']['operands']
  assert f['status']=='source_verified' and f['value']['native_form']==dict(operand_order=['S','D'],minimum_operands=2,maximum_operands=2)
  assert {p for ref in f['sources'] for p in ref['pdf_pages']} >= {133,699}
  assert s['type']=='ANY16' and s['direction']=='input' and s['word_device_layout']['word_count']==6 and s['word_device_layout']['single_scalar'] is False
  assert [(v['role'],v['minimum'],v['maximum']) for v in s['referent_fields']]==[('year',2000,2067),('month',1,12),('day',1,31),('hour',0,23),('minute',0,59),('second',0,59)]
  assert d['type']=='ANY32' and d['encoding']=='binary' and d['direction']=='output' and d['word_device_layout']['word_count']==2
  assert d['range']['minimum']==0 and d['range']['maximum']==2145916799
  gate=facts['authored:ql:date2sec_execution_gate:execution.operation_condition'];assert gate['status']=='source_verified'
  assert gate['value']['enabled_when']==('execution_condition_rising_edge' if op.endswith('P') else 'execution_condition_ON')
  assert set(gate['depends_on'])=={'authored:ql:native_arguments_s_d:parameters.typed_operands','authored:ql:cpu_applicability:constraints.cpu_availability'}

def test_date2sec_cpu_own_nine_guarded_twenty_unconditional_no_stale_negative():
 for op in ['DATE2SEC','DATE2SECP']:
  ordinary=guarded=0
  for e in [e for e in rows() if e['opcode']==op]:
   f=fs(e);cpu=f['authored:ql:cpu_applicability:constraints.cpu_availability']
   assert cpu['status']=='source_verified' and cpu['value']['available'] is True
   conditional='UDV' in e['target_model'] or 'UDPV' in e['target_model']
   negatives=[v for v in e['facts'] if v['dimension']=='constraints.cpu_availability' and v['value'].get('available') is False]
   if conditional:
    guarded+=1;assert '19042' in json.dumps(cpu['conditions']) and len(negatives)==1
   else:
    ordinary+=1;assert not cpu.get('conditions') and not negatives
    assert '19042' not in json.dumps(e['facts'])
  assert (ordinary,guarded)==(20,9)

def test_date2sec_own_device_table_not_adjacent_sec2date_or_seven_word_clock():
 for e in rows():
  s,d=fs(e)['authored:ql:native_arguments_s_d:parameters.typed_operands']['value']['operands']
  assert s['range']['device_prefixes']==['T','ST','C','D','W','SD','SW','FD','R','ZR']
  assert s['range']['address_forms']==[] and s['range']['index_register_operand'] is False
  assert d['range']['device_prefixes']==s['range']['device_prefixes']+['Z']
  assert d['range']['address_forms']==['buffer_memory','link_direct']
  assert d['range']['address_form_limits']['link_direct']['allowed_device_prefixes']==['W','SW']
  assert all(not p['range']['literal_prefixes'] and p['range']['bit_device_operand'] is False for p in [s,d])
  value=fs(e)['authored:ql:operation:effects.conversion']['value']
  assert value['epoch']==dict(year=2000,month=1,day=1,hour=0,minute=0,second=0)
  assert value['weekday_input_read'] is False and value['published_example']==dict(source_words=[2016,11,27,20,51,48],seconds=533595108)
  err=fs(e)['authored:ql:error:effects.error_response:SM0|SD0']['value']
  assert err['documented_codes']=={'4100':['date_time_field_out_of_range','nonexistent_date_time'],'4101':['source_or_destination_device_range_exceeded']}
  assert err['invalid_date_example']==dict(year=2016,month=2,day=30)

def test_date2sec_exact_original_namespace_and_history_preservation():
 entries=rows();assert len(entries)==58 and sum(len(e['facts']) for e in entries)==540
 assert {(e['target_model'],e['opcode']) for e in entries}=={(e['target_model'],e['opcode']) for e in load('original-owner-selectors.json')}
 history={(e['target_model'],e['opcode']):e for e in load('prior-owner-contracts.json')}
 for e in entries:
  facts=fs(e);assert all(facts[f['id']]==f for f in history[e['target_model'],e['opcode']]['preserved_facts'])
  assert all(set(f.get('depends_on',[]))<=facts.keys() for f in e['facts'])
 c=load('contract-additions.json')['cpu_contracts'];assert len(c)==2 and all(len(r['cpus'])==29 for r in c)
 assert [r['execution_form'] for r in c]==['continuous','pulse']
 assert all([p['word_count'] for p in r['instruction']['operands']]==[6,2] for r in c)

def test_date2sec_default_delivers_concrete_partial_boundaries():
 from knowledge.fact_selection import project_packet
 from shared.model_context import technical_context_units,technical_knowledge_text
 e=next(e for e in rows() if e['target_model']=='Q00U' and e['opcode']=='DATE2SECP')
 packet=project_packet(dict(kind='instruction',target=e['opcode'],scope=dict(models=['Q00U']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q00U',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
 record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=e['opcode'],_knowledge_context=dict(model='Q00U',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
 rendered=json.dumps(technical_context_units(technical_knowledge_text(record)))
 for field in ['calendar_algorithm_established','leap_year_algorithm_established','timezone_established','leap_second_policy_established','output_word_order_established','source_destination_overlap_behavior_established','command_FALSE_output_behavior_established','core_execution_established','native_execution_verified']:
  assert fs(e)['authored:ql:operation:effects.conversion']['value'][field] is False
  assert field in rendered
 assert 'execution_condition_rising_edge' in rendered and 'date_time_record_to_seconds' in rendered
