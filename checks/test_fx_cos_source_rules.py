"""COS family headings must not grant the absent Native COS literal."""
import copy,json
from pathlib import Path
from tools import fact_applied_review as p
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-cos-native-scope-repair')
def test_cos_literal_negative_requires_own_dash_and_exact_32bit_forms(monkeypatch):
 record=dict(id=625,manual_id='fx3_programming_r',revision='R',opcode='COS',pdf_pages=[522],section='18.19 FNC131 – COS / Floating Point Cosine',text='',manual_number='JY997D16601')
 forms=[dict(opcode='DCOS',width_bits=32),dict(opcode='DCOSP',width_bits=32)]
 monkeypatch.setattr(p,'_programming_manual_variants',lambda r:forms)
 table=[['16-bit Instruction Mnemonic Operation Condition\n[GLYPH-F02D]','32-bit Instruction DCOS Continuous DCOSP Pulse (Single)']]
 monkeypatch.setattr(p,'_source_table_groups',lambda *a:[table])
 facts={};p._repair_fx_cos_literal_scope(None,[record],facts)
 assert set(facts)=={('FX3U','COS'),('FX3UC','COS')}
 assert all(len(v)==3 for v in facts.values())
 table[0][0]='16-bit Instruction COS Continuous'
 facts={};p._repair_fx_cos_literal_scope(None,[record],facts);assert not facts
 table[0][0]='16-bit Instruction [GLYPH-F02D]';forms.append(dict(opcode='COS',width_bits=16))
 facts={};p._repair_fx_cos_literal_scope(None,[record],facts);assert not facts

def test_actual_two_cos_sources_retract_catalogue_literal_and_preserve_other_interfaces():
 from plc.instruction_constraints import check_fact_constraints
 entries=json.loads((HERE/'source-facts.json').read_text())['entries'];assert len(entries)==2
 base={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'base-source-facts.json').read_text())['entries']}
 for e in entries:
  m=e['target_model'];fs={f['id']:f for f in e['facts']}
  for f in base[m,'COS']['facts']:
   if f['id']!='source:instruction_catalogue:fx3_programming_r':assert fs[f['id']]==f
  for identity in ['source:instruction_catalogue:fx3_programming_r','authored:applied:cos_native_unavailable@fx3_programming_r']:
   f=fs[identity];assert f['value']['available'] is False and f['status']=='source_verified'
   assert f['scope']['models']==[m] and f['scope']['forms']==['COS'] and f['sources'][0]['pdf_pages']==[522]
   result=check_fact_constraints([fs['authored:applied:cos_literal_form_identity@fx3_programming_r'],f],{},target='COS',plc_model=m,language='ladder',context={'project_mode':'native_ladder_instruction'})
   assert result['status']=='failed'
   other=check_fact_constraints([f],{},target='COS',plc_model=m,language='st',context={'project_mode':'st_function_call'})
   assert other['status']!='failed'


def test_dcos_own_record_guard_does_not_promote_other_pages_or_16bit_family_name(monkeypatch):
 record=dict(id=625,manual_id='fx3_programming_r',revision='R',opcode='COS',pdf_pages=[522],
             text='DCOS DCOSP Real number (binary) radians',manual_number='JY997D16601')
 monkeypatch.setattr(p,'_source_table_groups',lambda *a:[])
 monkeypatch.setattr(p,'_parameter_source_pages',lambda *a:[])
 forms=[dict(opcode='DCOS',width_bits=32,execution_form='continuous'),dict(opcode='DCOSP',width_bits=32,execution_form='pulse')]
 monkeypatch.setattr(p,'_programming_manual_variants',lambda r:forms)
 facts={};p._repair_fx_dcos_source(None,[record],facts)
 assert set(facts)=={(m,o) for m in ('FX3U','FX3UC') for o in ('DCOS','DCOSP')}
 for mutation in ('page','manual','form'):
  bad=copy.deepcopy(record)
  if mutation=='page':bad['pdf_pages']=[523]
  if mutation=='manual':bad['manual_id']='jy992d48301j'
  if mutation=='form':forms[0]['width_bits']=16
  untouched={};p._repair_fx_dcos_source(None,[bad],untouched);assert not untouched


def test_dcos_actual_author_keeps_own_word_layout_versions_and_no_inverse_domain():
 root=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-dbon-dcos-native-contract-repair')
 entries=json.loads((root/'fresh-author-output.json').read_text(encoding='utf-8'))['entries']
 assert len(entries)==8
 for e in entries:
  m,o=e['target_model'],e['opcode'];manual='fx3_programming_r' if m.startswith('FX3') else 'jy992d88101e'
  fs={f['id']:f for f in e['facts']};typed=fs['authored:applied:native_typed_operands@'+manual]
  assert typed['status']=='source_verified' and typed['verification']=='source_checked'
  assert {'context':'project_mode','equals':'native_ladder_instruction'} in typed['conditions']
  for pin in typed['value']['operands']:
   assert pin['type']=='REAL' and pin['word_device_layout']['word_count']==2
   assert 'minimum' not in pin['range'] and 'maximum' not in pin['range']
   assert pin['range']['device_prefixes']==(['D','R'] if m.startswith('FX3') else ['D'])
   assert pin['range']['literal_prefixes']==(['E'] if m.startswith('FX3') and pin['name']=='S' else [])
  if m.startswith('FX3'):
   assert {'context':'cpu_version','minimum':'2.20' if m=='FX3U' else '1.00'} in typed['conditions']
  else:
   assert not any(c.get('context')=='cpu_version' for c in typed['conditions'])
  v=fs['authored:applied:variant_family@'+manual]['value']
  assert v['fnc_number']==131 and v['base_opcode']=='COS'
  assert v['variant_bindings'][o]['pulse']==o.endswith('P')
  operation=fs['authored:applied:cosine_documented_processing@'+manual]['value']
  assert operation['input_angle_unit']=='radian' and operation['full_source_relation_complete'] is False
  assert operation['native_execution_verified'] is False and operation['core_execution_established'] is False
  gate=fs['authored:applied:cosine_execution_gate@'+manual]['value']
  assert gate['condition']==('rising_edge' if o.endswith('P') else 'execution_condition_ON')


def test_dcos_E_availability_requires_own_header_and_32bit_pulse_cells(monkeypatch):
 root=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-dbon-dcos-native-contract-repair')
 records=json.loads((root/'E268-author-record.json').read_text(encoding='utf-8'))
 monkeypatch.setattr(p,'_source_table_groups',lambda s,r:r['table_groups'])
 monkeypatch.setattr(p,'_parameter_source_pages',lambda s,r:r['parameter_pages'])
 facts={};p._repair_fx_dcos_source(None,records,facts)
 assert set(facts)=={(m,o) for m in ('FX2N','FX2NC') for o in ('DCOS','DCOSP')}
 for rows in facts.values():
  positive=next(f for f in rows if f['id']=='authored:applied:cpu_availability:header@jy992d88101e')
  assert positive['status']=='source_verified' and positive['value']['available'] is True
  assert positive['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'}]
  assert all(s['pdf_pages']==[268] for s in positive['sources'])
 bad=copy.deepcopy(records)
 for r in bad:
  for cells in r['source_form_models'].values():
   cells['FX2N']=False;cells['FX2NC']=False
 result={};p._repair_fx_dcos_source(None,bad,result);assert not result
