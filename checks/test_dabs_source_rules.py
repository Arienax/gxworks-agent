"""FNC155 and FNC62 own Native facts, preserving old partial signatures."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-fx-dabs-native-review')
def entries():return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
def entry(m,o):return next(e for e in entries() if e['target_model']==m and e['opcode']==o)
@pytest.mark.parametrize('m,version',[('FX3S','1.00'),('FX3GC','1.40'),('FX3U','2.20'),('FX1NC',None)])
def test_dabs_own_three_bit_heads_and_per_cpu_header_guards(m,version):
 fs={f['id']:f for f in entry(m,'DABS')['facts']};suffix='@fx3_programming_r:fx1nc_native_catalogue' if m=='FX1NC' else '@fx3_programming_r'
 f=fs['authored:applied:native_typed_operands'+suffix];s,d1,d2=f['value']['operands']
 assert f['status']=='source_verified' and f['value']['native_form']==dict(operand_order=['S','D1','D2'],minimum_operands=3,maximum_operands=3)
 assert [p['type'] for p in [s,d1,d2]]==['BOOL','BOOL','ANY32'] and [p['direction'] for p in [s,d1,d2]]==['input','output','output']
 assert s['occupied_bit_count']==d1['occupied_bit_count']==3 and d2['word_device_layout']['word_count']==2
 if version:
  assert f['conditions']==[dict(context='project_mode',equals='native_ladder_instruction'),dict(context='cpu_version',minimum=version)]
  below=fs['authored:applied:cpu_availability:below_header_version@fx3_programming_r'];assert below['conditions']==[dict(context='project_mode',equals='native_ladder_instruction'),dict(context='cpu_version',maximum_exclusive=version)]
  assert below['value']['available'] is False
 else:
  assert f['conditions']==[dict(context='project_mode',equals='native_ladder_instruction')]
  assert {p for ref in f['sources'] for p in ref['pdf_pages']}=={81,555}
 assert fs['authored:applied:dabs_documented_interface'+suffix]['value']['positioning_sequence_fully_established'] is False
 assert 'R' not in d2['range']['device_prefixes'] if m in {'FX3S','FX1NC'} else 'R' in d2['range']['device_prefixes']
def test_dabsd_modern_literal_four_n_word_table_n_bits_and_constant_count():
 f=next(f for f in entry('FX3U','DABSD')['facts'] if f['id']=='authored:applied:native_typed_operands@fx3_programming_r');s1,s2,d,n=f['value']['operands']
 assert f['value']['native_form']==dict(operand_order=['S1','S2','D','n'],minimum_operands=4,maximum_operands=4)
 assert s1['region_span']==dict(count_expression=dict(operator='multiply',operands=[4,dict(parameter='n')]),unit='word')
 assert s1['word_device_layout']['head_storage_only'] is True and d['region_span']==dict(count_operand='n',unit='bit')
 assert s2['range']['device_prefixes']==['C'] and n['type']=='ANY16' and n['range']==dict(minimum=1,maximum=64,literal_prefixes=['K','H'],device_prefixes=[])
 assert s1['range']['digit_groups_allowed']==[8] and s1['range']['digit_group_start_bit_multiple']==16
 v=next(f['value'] for f in entry('FX3U','DABSD')['facts'] if f['id']=='authored:applied:dabsd_documented_interface@fx3_programming_r')
 assert v['fnc_number']==62 and v['command_OFF_output_state']=='unchanged' and v['high_speed_counter_S2_allowed'] is True
def test_old_eight_dabsd_n_width_and_all_unrelated_interfaces_preserved():
 prior=json.loads((HERE/'prior-owner-contracts.json').read_text(encoding='utf-8'));partial=json.loads((HERE/'partial-owner-qualifications.json').read_text(encoding='utf-8'));assert len(partial)==8
 for old in prior:
  fs={f['id']:f for f in entry(old['target_model'],old['opcode'])['facts']}
  assert all(fs[f['id']]==f for f in old['preserved_facts'])
  for f in old['preserved_facts']:
   if any(s['manual_id']=='fxcpu_basic_applied_m' for s in f['sources']):assert fs[f['id']]==f
 for p in partial:
  assert p['n_width_established'] is p['contract_proposed'] is False
  pins=[p for f in entry(p['target_model'],'DABSD')['facts'] if f['id'].startswith('authored:applied:native_typed_operands') for p in f['value'].get('operands',[]) if p['name']=='n'];assert all(p.get('width_bits')=='unknown' for p in pins)
def test_dabs_exact_original_twenty_three_and_only_fifteen_contracts():
 rows=entries();assert len(rows)==23 and sum(len(e['facts']) for e in rows)==636
 c=json.loads((HERE/'contract-additions.json').read_text(encoding='utf-8'))['cpu_contracts'];assert len(c)==4 and sum(len(r['cpus']) for r in c)==15
 assert {r['instruction']['mnemonic'] for r in c}=={'DABS','DABSD'}
 assert all(r['instruction']['mnemonic']==r['instruction']['canonical_op'] for r in c)
 assert all(r['execution_form']=='continuous' for r in c)
@pytest.mark.parametrize('op',['DABS','DABSD'])
def test_dabs_default_keeps_own_interface_and_unverified_execution(op):
 from knowledge.fact_selection import project_packet
 from shared.model_context import technical_context_units,technical_knowledge_text
 e=entry('FX3U',op)
 packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['FX3U']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='FX3U',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
 record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='FX3U',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
 text=json.dumps(technical_context_units(technical_knowledge_text(record)))
 for token in (['servo_absolute_position_read','positioning_sequence_fully_established','occupied_bit_count'] if op=='DABS' else ['absolute_drum_sequencer','table_word_count','output_bit_count','unchanged']):assert token in text,token
 assert 'Native_execution_verified' in text


def test_dabs_own_fnc_numbers_repair_only_modern_variant_metadata():
 changes=json.loads((HERE/'source-corrections.json').read_text(encoding='utf-8'))['changes']
 selected=[c for c in changes if c['id']=='authored:applied:variant_family@fx3_programming_r']
 assert len(selected)==10
 for c in selected:
  old,new=c['before'],c['after'];expected=155 if c['opcode']=='DABS' else 62
  assert old['value']['fnc_number']==(150 if c['opcode']=='DABS' else 60) and new['value']['fnc_number']==expected
  value=copy.deepcopy(old['value']);value['fnc_number']=expected;assert new['value']==value
  assert new['status']==old['status'] and new['conditions']==old['conditions']
  refs=lambda f:{(v['manual_id'],v['revision'],tuple(v['pdf_pages'])) for v in f['sources']}
  assert refs(new)==refs(old)
