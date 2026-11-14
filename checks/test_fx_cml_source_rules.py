"""CML exclusions use their own Native headers, not structured absence or SMOV."""
import copy,json
from pathlib import Path
from tools import fact_applied_review as p
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-cml-native-contract-repair')


def test_J_CML_negative_binding_cannot_consume_preceding_SMOV_header(monkeypatch):
 record=dict(id='own',manual_id='jy992d48301j',manual_number='JY992D48301',revision='J',opcode='CML',section='5.2.5 CML (FNC 14)',pdf_pages=[145],source_geometry_text=True,applicability={'model_marks':[{'model':m,'state':'unsupported'} for m in ['FX0','FX0N','FX0S']]})
 monkeypatch.setattr(p,'_parameter_source_pages',lambda *_:[])
 monkeypatch.setattr(p,'_source_table_groups',lambda *_:[])
 monkeypatch.setattr(p,'_legacy_table_data',lambda *_:dict(symbols=['S','D'],function='Copies and inverts the source bit pattern to a specified destination'))
 monkeypatch.setattr(p,'_legacy_variants',lambda *_:[dict(opcode=o,width_bits='unknown',execution_form='pulse' if o.endswith('P') else 'continuous') for o in ['CML','CMLP','DCML','DCMLP']])
 facts={};p._repair_fx_cml_negative_sources(None,[record],facts)
 assert len(facts)==12 and all(len(fs)==2 for fs in facts.values())
 for mutate in ['section','pages','geometry','positive_header']:
  bad=copy.deepcopy(record)
  if mutate=='section':bad['section']='5.2.4 SMOV (FNC 13)'
  if mutate=='pages':bad['pdf_pages']=[144,145]
  if mutate=='geometry':bad['source_geometry_text']=False
  if mutate=='positive_header':bad['applicability']['model_marks'][0]['state']='supported'
  rejected={};p._repair_fx_cml_negative_sources(None,[bad],rejected);assert not rejected


def test_actual_CML_21_negative_facts_require_own_form_identity_and_native_context():
 from plc.instruction_constraints import check_fact_constraints
 entries=json.loads((HERE/'source-facts.json').read_text())['entries'];assert len(entries)==21
 for e in entries:
  m,o=e['target_model'],e['opcode'];facts=e['facts'];assert len(facts)==2
  identity=next(f for f in facts if f['dimension']=='parameters.variant_family')
  negative=next(f for f in facts if f['dimension']=='constraints.cpu_availability')
  assert negative['value']['available'] is False and negative['value']['model_states']=={m:'unsupported'}
  assert negative['depends_on']==[identity['id']]
  assert all(f['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'}] for f in facts)
  assert identity['value']['literal_form']==o and identity['value']['identity_only_not_CPU_support'] is True
  if m.startswith('FX0'):
   assert all(f['sources'][0]['pdf_pages']==[145] for f in facts)
   assert identity['value']['width_bits']=='unknown' # no D-width guess from a name
  else:
   assert negative['sources'][0]['pdf_pages']==[76,256,257]
   assert identity['sources'][0]['pdf_pages']==[256,257]
   assert identity['value']['width_bits']==(32 if o.startswith('D') else 16)
  result=check_fact_constraints(facts,{},target=o,plc_model=m,language='ladder',context={'project_mode':'native_ladder_instruction'})
  assert result['status']=='failed'
  assert any(c['fact_id']==negative['id'] and c['status']=='failed' for c in result['checks'])
  wrong=check_fact_constraints(facts,{},target=o,plc_model=m,language='ladder',context={'project_mode':'structured_ladder_block'})
  assert not any(c['status']=='failed' for c in wrong['checks'])
  missing=check_fact_constraints([negative],{},target=o,plc_model=m,language='ladder',context={'project_mode':'native_ladder_instruction'})
  assert missing['status']!='failed'


def test_CML_original_24_scope_and_reused_3_are_exact():
 original=json.loads((HERE/'original-owner-selectors.json').read_text());selected=json.loads((HERE/'owner-selectors.json').read_text());reused=json.loads((HERE/'reused-source-facts.json').read_text())['entries']
 assert len(original)==24 and len(selected)==21 and len(reused)==3
 assert {(e['target_model'],e['opcode']) for e in selected}|{(e['target_model'],e['opcode']) for e in reused}=={(e['target_model'],e['opcode']) for e in original}
 assert all(e['opcode']=='CML' and e['target_model'] in {'FX1N','FX1NC','FX1S'} for e in reused)
 assert all(e['fact']['id']=='source:instruction_catalogue:fx3_programming_r' and e['fact']['value']['available'] is False for e in reused)
 assert json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts']==[]
