"""Own R589/590 COMRD metadata identity and conditional destination."""
import json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-comrd-native-contract-repair')

def assert_native_shape(f,model):
 assert f['status']=='source_verified' and f['verification']=='source_checked'
 assert f['scope']['models']==[model]
 assert f['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'},{'context':'cpu_version','minimum':'2.20'}]
 s,d=f['value']['operands']
 assert s['name']=='S' and s['type']=='DEVICE_REFERENCE' and s['direction']=='input'
 assert s['role']=='comment_device_selector' and s['selector_kind']=='device_comment_identity' and s['device_value_read'] is False
 assert not s.get('word_device_layout') and 'occupied_bit_count' not in s
 assert s['range']==dict(device_prefixes=['X','Y','M','S','T','C','D','R'],literal_prefixes=[],index_modification=True)
 assert d['type']=='STRING' and d['direction']=='output'
 assert d['word_device_layout']==dict(kind='character_string',device_unit_bits=16,element_bits=8,element_count=16,single_scalar=False,word_count=8,byte_order='low_byte_then_high_byte')
 assert d['conditional_tail_write']==dict(selector='M8091',word_offset=8,OFF=dict(word_count=1,value=0),ON='unchanged',current_selector_value_verified=False)
 assert d['range']==dict(device_prefixes=['T','C','D','R'],literal_prefixes=[],index_modification=True)

@pytest.mark.parametrize('model',['FX3U','FX3UC'])
@pytest.mark.parametrize('op',['COMRD','COMRDP'])
def test_own_comrd_source_preserves_identity_base_extent_and_conditional_tail(model,op):
 e=next(e for e in json.loads((HERE/'source-facts.json').read_text())['entries'] if (e['target_model'],e['opcode'])==(model,op))
 fs={f['id']:f for f in e['facts']};assert_native_shape(fs['authored:applied:native_typed_operands@fx3_programming_r'],model)
 v=fs['authored:applied:comrd_documented_comment_read@fx3_programming_r']['value']
 assert v['unregistered_comment']==dict(destination_characters='16_ASCII_spaces',ASCII=32,operation_error_code=6706)
 assert v['full_source_relation_complete'] is False and len(v['unresolved_items'])==3
 for key in ['current_comment_registration_verified', 'current_comment_data_verified', 'command_FALSE_output_behavior_established']:assert v[key] is False
 assert v['M8091_ON']['tail_word']=='unchanged' and v['M8091_OFF']['tail_word_write_value']==0
 for f in e['facts']:
  if f['dimension']=='constraints.cpu_availability' and f['id'].endswith('@fx3_programming_r'):
   assert f['scope']['models']==[model]
   versions=[c for c in f.get('conditions',[]) if c.get('context')=='cpu_version']
   assert not (any('minimum' in c for c in versions) and any('maximum_exclusive' in c for c in versions))

def test_unchanged_M_other_interfaces_and_all_eight_negative_owners_preserved():
 new={(e['target_model'],e['opcode']):{f['id']:f for f in e['facts']} for e in json.loads((HERE/'source-facts.json').read_text())['entries']}
 changes={(e['target_model'],e['opcode'],e['id']) for e in json.loads((HERE/'source-corrections.json').read_text())['changes']}
 for e in json.loads((HERE/'base-source-facts.json').read_text())['entries']:
  for f in e['facts']:
   if (e['target_model'],e['opcode'],f['id']) not in changes:assert new[e['target_model'],e['opcode']][f['id']]==f
 reused=json.loads((HERE/'reused-source-facts.json').read_text())['entries'];assert len(reused)==8
 assert all(e['fact']['value']['available'] is False for e in reused)
