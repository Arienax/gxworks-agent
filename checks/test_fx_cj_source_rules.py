"""Own FX CJ/CJP source rules, independently of CALL and indexed token ABI."""
import copy,json
from pathlib import Path
import pytest
from tools import fact_applied_review as p
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-cj-native-contract-repair')


def _r_record():
 def w(t,x,y):return dict(text=t,x0=x,x1=x+8,top=y,bottom=y+7)
 text='Pointer number (P) indicating the label number for the jump destination n=0 to 255 n=0 to 2047 n=0 to 4095 P63 jumps to END instruction.'
 page=dict(page=223,layout=text,words=[w('Pn',120,310),w('Pointer',483,309),w('number',509,309),w('CJ',270,520),w('Pn',320,516),w('CJP',270,670),w('Pn',320,666),w('Modify',407,390),w('P',530,390),w('',411,404),w('',530,404)])
 return dict(manual_id='fx3_programming_r',revision='R',opcode='CJ',parameter_pages=[page])


def test_R223_parameter_recovery_needs_own_Pn_range_diagrams_and_Modify_cells():
 r=_r_record();before=copy.deepcopy(r)
 row=p._own_r_cj_parameter_rows(r,16)[0]
 assert row['name']=='Pn' and row['type']=='POINTER' and row['direction']=='input' and row['role']=='branch_target'
 assert p._slot_symbol('Pn') is None and r==before
 for damaged in ['range','type','diagram','Modify','manual']:
  b=copy.deepcopy(r)
  if damaged=='range':b['parameter_pages'][0]['layout']=b['parameter_pages'][0]['layout'].replace('n=0 to 2047','')
  if damaged=='type':b['parameter_pages'][0]['words'][1]['text']='Word'
  if damaged=='diagram':b['parameter_pages'][0]['words']=[w for w in b['parameter_pages'][0]['words'] if w['text']!='CJP']
  if damaged=='Modify':b['parameter_pages'][0]['words'].pop()
  if damaged=='manual':b['manual_id']='jy992d48301j'
  assert p._own_r_cj_parameter_rows(b,16)==[]
 assert p._own_r_cj_parameter_rows(r,32)==[]


@pytest.mark.parametrize('model,maximum,minimum',[('FX3S',255,'1.00'),('FX3G',2047,'1.00'),('FX3GC',2047,'1.40'),('FX3U',4095,'2.20'),('FX3UC',4095,'1.00')])
@pytest.mark.parametrize('opcode',['CJ','CJP'])
def test_actual_R_CJ_packet_keeps_P63_index_unknown_and_own_versions(model,maximum,minimum,opcode):
 entries=json.loads((HERE/'source-facts.json').read_text())['entries'];e=next(e for e in entries if e['target_model']==model and e['opcode']==opcode)
 rows={f['id']:f for f in e['facts']};f=rows['authored:applied:cj_documented_pointer_contract@fx3_programming_r'];v=f['value']
 assert v['pointer_number_intervals']==[[0,maximum]] and v['P63']==dict(target='END',label_required=False)
 assert v['P63_label_definition_prohibited'] and v['P63_label_definition_error_code']==6507
 assert v['index_modification_allowed'] is True and v['full_source_relation_complete'] is False
 for field in ['indexed_pointer_codec_verified','indexed_pointer_native_step_width_verified','effective_indexed_target_verified','project_target_binding_verified','contract_admission_granted']:assert v[field] is False
 assert {'context':'cpu_version','minimum':minimum} in f['conditions']
 assert {'context':'project_mode','equals':'native_ladder_instruction'} in f['conditions']
 jump=rows['authored:applied:cj_documented_jump@fx3_programming_r']['value']
 assert jump['trigger']==('command_OFF_to_ON_one_scan' if opcode=='CJP' else 'each_scan_while_command_ON')
 skip=rows['authored:applied:cj_documented_skipped_region@fx3_programming_r']['value']
 assert skip['routine_timers_T192_to_T199']=='continue_after_coil_driven'
 assert skip['high_speed_counters_C235_to_C255']=='continue_after_coil_driven'
 assert skip['full_source_relation_complete'] is False


def test_actual_CJ_scope_and_unchanged_M_are_preserved():
 entries=json.loads((HERE/'source-facts.json').read_text())['entries'];base={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'base-source-facts.json').read_text())['entries']}
 assert len(entries)==32
 for e in entries:
  m,o=e['target_model'],e['opcode'];rows={f['id']:f for f in e['facts']}
  for old in base[m,o]['facts']:
   if any(s.get('manual_id')=='fxcpu_basic_applied_m' for s in old['sources']):assert rows[old['id']]==old
  for f in rows.values():
   if f['id'].startswith('authored:applied:native_typed_operands@jy992d'):
    assert f['value']['operands'][0]['name']=='D' and f['value']['operands'][0]['direction']=='input'
   if m=='FX1NC' and f['id'].startswith('authored:applied:') and all(s['manual_id']=='jy992d88101e' for s in f['sources']):
    assert f['status']==('unknown' if 'cj_native_scope_review' in f['id'] else 'candidate_evidence')
    assert f['value']['source_scope_confirmed'] is False
  if m in {'FX0','FX0S','FX0N'} and o=='CJP':
   assert any(f['dimension']=='constraints.cpu_availability' and f['value'].get('available') is False for f in rows.values())
   assert not any(f['dimension']=='parameters.documented_pointer_contract' for f in rows.values())


@pytest.mark.parametrize('model,version,below',[('FX3S','1.00','0.99'),('FX3G','1.00','0.99'),('FX3GC','1.40','1.39'),('FX3U','2.20','2.19'),('FX3UC','1.00','0.99')])
def test_CJ_default_projection_applies_own_version_and_preserves_unknown_ABI(model,version,below):
 from knowledge.fact_selection import project_packet
 from shared.model_context import technical_context_units,technical_knowledge_text
 e=next(e for e in json.loads((HERE/'source-facts.json').read_text())['entries'] if e['target_model']==model and e['opcode']=='CJ')
 def walk(x):
  if isinstance(x,dict):
   yield x
   for v in x.values():yield from walk(v)
  elif isinstance(x,list):
   for v in x:yield from walk(v)
 for current,mode,applicable in [(version,'native_ladder_instruction',True),(below,'native_ladder_instruction',False),(version,'structured_ladder_block',False),(None,'native_ladder_instruction',None)]:
  packet=project_packet(dict(kind='instruction',target='CJ',scope={'models':[model]},content_origin='authored_fact',value=e),plc_model=model,language='ladder',project_mode=mode,instance_context={} if current is None else {'cpu_version':current},aspects=['programming_semantics'])
  typed=next((f for f in packet['value']['facts'] if f['id']=='authored:applied:native_typed_operands@fx3_programming_r'),None)
  if applicable is False:assert typed is None
  else:
   assert typed['selection_applicability']==('applicable' if applicable else 'unverified')
   record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target='CJ',_knowledge_context=dict(model=model,language='ladder',project_mode=mode,delivery_languages=['ladder']))
   nodes=list(walk(technical_context_units(technical_knowledge_text(record))))
   assert any(n.get('source_symbol')=='Pn' and n.get('P63')==dict(target='END',label_required=False) and n.get('indexed_pointer_codec_verified') is False and n.get('effective_indexed_target_verified') is False for n in nodes)
