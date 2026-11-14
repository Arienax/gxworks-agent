"""The original six low-FX pulse variants have their own J143 exclusions."""
import json
from pathlib import Path
import pytest
from tools import fact_applied_review as p
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-cmp-native-scope-repair')


@pytest.mark.parametrize('model',['FX0','FX0N','FX0S'])
@pytest.mark.parametrize('opcode,width',[('CMPP',16),('DCMPP',32)])
def test_existing_legacy_parser_uses_own_pulse_cell_instead_of_supported_header(model,opcode,width):
 record={'manual_models':[model], 'applicability':{'model_marks':[{'model':model,'state':'supported'}]},'source_form_models':{'16':{model:True},'32':{model:model=='FX0N'},'pulse':{model:False}}}
 assert p._legacy_form_states(record,dict(opcode=opcode,width_bits=width,execution_form='pulse'))[model]=='unsupported'
 unknown={**record,'source_form_models':{**record['source_form_models'],'pulse':{model:None}}}
 # The 32-bit cell can independently exclude a form; no unknown pulse may
 # turn its blank width cell into positive support.
 assert p._legacy_form_states(unknown,dict(opcode=opcode,width_bits=width,execution_form='pulse'))[model] in {'unknown','unsupported'}


def test_actual_six_CMP_P_source_adds_only_own_J_negative_and_preserves_other_interfaces():
 from plc.instruction_constraints import check_fact_constraints
 entries=json.loads((HERE/'source-facts.json').read_text())['entries'];base={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'base-source-facts.json').read_text())['entries']}
 assert {(e['target_model'],e['opcode']) for e in entries}=={(m,o) for m in ['FX0','FX0N','FX0S'] for o in ['CMPP','DCMPP']}
 for e in entries:
  m,o=e['target_model'],e['opcode'];old=base[m,o]['facts'];new={f['id']:f for f in e['facts']}
  for f in old:assert new[f['id']]==f
  assert len(new)==len(old)+1
  f=new['authored:applied:cpu_availability@jy992d48301j']
  assert f['value']['available'] is False and f['value']['model_states']=={m:'unsupported'}
  assert f['status']=='source_verified' and f['verification']=='source_checked'
  assert f['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'}]
  assert f['sources'][0]['pdf_pages']==[143] and '5.2.1' in f['sources'][0]['id'] # not same-page ZCP
  result=check_fact_constraints([f],{},target=o,plc_model=m,language='ladder',context={'project_mode':'native_ladder_instruction'})
  assert result['status']=='failed' and any(c['fact_id']==f['id'] and c['status']=='failed' for c in result['checks'])
  wrong=check_fact_constraints([f],{},target=o,plc_model=m,language='ladder',context={'project_mode':'structured_ladder_block'})
  assert wrong['status']!='failed'
 assert json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts']==[]
