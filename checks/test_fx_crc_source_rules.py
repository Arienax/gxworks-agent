"""Own FX CRC/P heads, conditional payloads and partial OFF extent."""
import json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-crc-native-contract-repair')
@pytest.mark.parametrize('model,version',[('FX3U','2.20'),('FX3UC','1.00')])
@pytest.mark.parametrize('op,trigger',[('CRC','continuous'),('CRCP','pulse')])
def test_own_fx_crc_source_is_head_only_with_mode_qualified_payload(model,version,op,trigger):
 e=next(e for e in json.loads((HERE/'source-facts.json').read_text())['entries'] if (e['target_model'],e['opcode'])==(model,op));fs={f['id']:f for f in e['facts']}
 f=fs['authored:applied:native_typed_operands@fx3_programming_r'];assert f['status']=='source_verified'
 assert f['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'},{'context':'cpu_version','minimum':version}]
 s,d,n=f['value']['operands'];assert [p['name'] for p in (s,d,n)]==['S','D','n']
 for p in (s,d):assert p['passing']=='device_reference' and p['word_device_layout']==dict(device_unit_bits=16,word_count=1,head_storage_only=True)
 assert s['conditional_read_layout']['selector']=='M8161'
 assert s['conditional_read_layout']['OFF']['general_exact_span_verified'] is False and 'word_count' not in s['conditional_read_layout']['OFF']
 assert s['conditional_read_layout']['ON']['word_count']==dict(op='parameter',name='n',type=dict(kind='int',bits=32,signed=True))
 assert d['conditional_write_layout']['OFF']['word_count']==1 and d['conditional_write_layout']['ON']['word_count']==2
 assert n['range']==dict(minimum=1,maximum=256,device_prefixes=['D','R'],literal_prefixes=['K','H'],address_forms=[],index_modification=False)
 assert fs['authored:applied:execution_form@fx3_programming_r']['value']['form']==trigger
 digits=fs['authored:applied:crc_digit_width@fx3_programming_r'];assert digits['value']['positions']==[0,1] and digits['value']['digits']==[4]
 v=fs['authored:applied:crc_documented_modes@fx3_programming_r']['value']
 assert v['polynomial_exponents']==[16,15,2,0] and v['initial_value']==65535
 assert v['full_source_relation_complete'] is False and v['mode_OFF']['general_source_extent_established'] is False
 assert v['bit_order_and_reflection_algorithm_established'] is False and v['mode_ON']['destination_upper_byte_policy_established'] is False

def test_old_other_interfaces_and_eight_native_negative_owners_preserved():
 changed={(c['target_model'],c['opcode'],c['id']) for c in json.loads((HERE/'source-corrections.json').read_text())['changes']}
 new={(e['target_model'],e['opcode']):{f['id']:f for f in e['facts']} for e in json.loads((HERE/'source-facts.json').read_text())['entries']}
 for e in json.loads((HERE/'base-source-facts.json').read_text())['entries']:
  for f in e['facts']:
   if (e['target_model'],e['opcode'],f['id']) not in changed:assert new[e['target_model'],e['opcode']][f['id']]==f
 reused=json.loads((HERE/'reused-source-facts.json').read_text())['entries'];assert len(reused)==8
 assert all(e['opcode']=='CRC' and e['fact']['value']['available'] is False and e['fact']['sources'][0]['pdf_pages']==[75,82] for e in reused)
