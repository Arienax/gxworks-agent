"""Own X547 Native DABCD source proposal; no executable/ABI grant."""
import json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-dabcd-native-contract-repair')
def entry(model):return next(e for e in json.loads((HERE/'source-facts.json').read_text())['entries'] if e['target_model']==model)
@pytest.mark.parametrize('model,extent_error',[('Q00U',True),('L02',True),('Q02',False)])
def test_native_fixed_four_ascii_layout_and_own_error_cpu_columns(model,extent_error):
 fs={f['id']:f for f in entry(model)['facts']};f=fs['authored:ql:native_arguments_s_d:parameters.typed_operands'];s,d=f['value']['operands']
 assert f['conditions']==[dict(context='project_mode',equals='native_ladder_instruction')]
 assert f['status']=='source_verified' and s['type']=='STRING' and s['direction']=='input' and d['type']=='ANY16' and d['direction']=='output'
 assert s['word_device_layout']==dict(kind='fixed_character_sequence',device_unit_bits=16,word_count=2,element_bits=8,element_count=4,single_scalar=False)
 assert s['string_layout']['fixed_character_count']==4 and s['string_layout']['zero_byte_ends_conversion'] is False
 assert s['range']['literal_prefixes']==['$'] and s['range']['address_forms']==[] and s['range']['index_register_operand'] is False
 assert d['word_device_layout']['word_count']==1 and d['decimal_digits']==4 and d['encoding']=='BCD'
 assert d['range']['address_forms']==['buffer_memory','link_direct'] and d['range']['index_register_operand'] is True
 errors=fs['authored:ql:error:effects.error_response:SM0|SD0']['value'];assert errors['invalid_character_error']['CPU_error_row_applicable'] is True
 assert errors['source_extent_error']['CPU_error_row_applicable'] is extent_error and errors['destination_on_error_behavior_established'] is False

@pytest.mark.parametrize('source_words,expected', [([0x3738,0x3536],0x8765),([0x3420,0x3439],0x0494),([0x3231,0x3300],0x1203)])
def test_documented_byte_and_nibble_positions_match_manual_examples_and_per_digit_zero(source_words,expected):
 value=next(f for f in entry('Q00U')['facts'] if f['id']=='authored:ql:operation:effects.conversion')['value']
 result=0
 for p in value['character_positions']:
  byte=(source_words[p['source_word_offset']]>>(8*p['source_byte_offset']))&255
  digit=0 if byte in value['zero_digit_ASCII_codes'] else byte-48
  assert 0<=digit<=9;result|=digit<<p['destination_low_bit']
 assert result==expected and value['string_terminator_stops_conversion'] is False
 assert value['short_literal_padding_behavior_established'] is False and value['source_destination_overlap_behavior_established'] is False

def test_exact_50_scope_full_namespace_and_other_interfaces_preserved():
 rows=json.loads((HERE/'source-facts.json').read_text())['entries'];assert len(rows)==50 and sum(len(e['facts']) for e in rows)==1350
 old={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'baseline-source.json').read_text())['entries']}
 changes=json.loads((HERE/'source-corrections.json').read_text())['changes'];assert len(changes)==200 and sum(c['before'] is not None for c in changes)==150
 changed={(c['target_model'],c['opcode'],c['id']) for c in changes}
 extent=0
 for e in rows:
  m,o=e['target_model'],e['opcode'];assert o=='DABCD';fs={f['id']:f for f in e['facts']}
  for f in old[m,o]['facts']:
   if (m,o,f['id']) not in changed:assert fs[f['id']]==f
   if any(s['manual_id']=='sh080783engp' for s in f['sources']):assert fs[f['id']]==f
  extent+=fs['authored:ql:error:effects.error_response:SM0|SD0']['value']['source_extent_error']['CPU_error_row_applicable']
 assert extent==39
 assert all(e['metadata']['call_available'] is False for e in json.loads((HERE/'baseline-metadata.json').read_text()))
 contracts=json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts'];assert len(contracts)==1
 assert set(contracts[0]['cpus'])=={e['target_model'] for e in rows}
 assert contracts[0]['instruction']['mnemonic']=='DABCD' and contracts[0]['native_order']==['S','D']
