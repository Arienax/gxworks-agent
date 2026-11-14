"""Own R679-681 DBINDA/P source boundaries, not Q/L SM701."""
import json
from pathlib import Path
import pytest
BASE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-dbinda-native-contract-repair')
ROWS=json.loads((BASE/'source-facts.json').read_text(encoding='utf-8'))['entries']
HEADER='authored:applied:source_cpu_header@fx3_programming_r'
VERSION='authored:applied:cpu_version:header_minimum@fx3_programming_r'
AVAILABILITY='authored:applied:cpu_availability:header@fx3_programming_r'
CATALOGUE='source:instruction_catalogue:fx3_programming_r'
NATIVE={'context':'project_mode','equals':'native_ladder_instruction'}
MINIMUM={'context':'cpu_version','minimum':'2.20'}

@pytest.mark.parametrize('entry',ROWS,ids=lambda e:e['target_model']+'-'+e['opcode'])
def test_r679_conditional_header_is_bound_to_own_version_fact(entry):
 m,o=entry['target_model'],entry['opcode'];facts={f['id']:f for f in entry['facts']}
 header=facts[HEADER];version=facts[VERSION];availability=facts[AVAILABILITY];catalogue=facts[CATALOGUE]
 assert header['value']['model']==m and header['value']['minimum_version']=='2.20'
 assert version['dimension']=='constraints.cpu_version'
 assert version['scope']=={'models':[m],'forms':[o],'languages':['ladder']}
 assert version['value']['models']==[m] and version['value']['minimum_series_version']=='Ver.2.20'
 assert version['depends_on']==[HEADER] and version['sources'][0]['pdf_pages']==[679]
 owner_witness=next(source for source in version['sources'] if source.get('representation')=='instruction_header_cpu_version_badges')
 assert owner_witness['opcode']==o and owner_witness['target_model']==m
 assert owner_witness['source_ref']=='fx3_programming_r:R:pages=679'
 assert version['id'] in availability['depends_on']
 assert availability['conditions']==[NATIVE,MINIMUM]
 assert catalogue['value']['model_states']=={m:'conditional'} and 'available' not in catalogue['value']
 assert catalogue['depends_on'].count(version['id'])==1
 assert catalogue['conditions'].count(MINIMUM)==1
 assert all(dependency in facts for fact in entry['facts'] for dependency in fact.get('depends_on',[]))

@pytest.mark.parametrize('entry',[e for e in ROWS if e['opcode'].startswith('D')],ids=lambda e:e['target_model']+'-'+e['opcode'])
def test_own_32bit_record_and_source_preservation(entry):
 m,o=entry['target_model'],entry['opcode'];facts={f['id']:f for f in entry['facts']}
 suffix='@fx3_programming_r';t=facts['authored:applied:native_typed_operands'+suffix]
 assert t['status']=='source_verified' and t['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'},{'context':'cpu_version','minimum':'2.20'}]
 s,d=t['value']['operands'];assert s['type']=='ANY32' and s['signed'] and s['word_device_layout']['word_count']==2
 assert s['range']['literal_prefixes']==['K','H'] and s['range']['device_prefixes']==['T','C','D','R','V','Z']
 assert s['range']['digit_groups_max']==8 and s['range']['address_forms']==['buffer_memory']
 assert d['conditional_byte_write']=={'selector':'M8091','word_offset':5,'bit_offset':8,'value_bits':8,'OFF':{'value':0},'ON':{'value':32},'current_selector_value_verified':False}
 assert d['type']=='STRING' and d['passing']=='device_reference' and d['word_device_layout']['word_count']==6 and d['word_device_layout']['element_count']==12
 assert d['range']['device_prefixes']==['T','C','D','R'] and d['range']['address_forms']==[] and d['range']['literal_prefixes']==[]
 op=facts['authored:applied:dbinda_documented_conversion'+suffix]['value']
 assert op['final_byte']=={'selector':'M8091','destination_word_offset':5,'destination_byte_offset':1,'OFF':0,'ON':32}
 assert op['destination_word_count']==6 and op['M8091_changes_destination_word_count'] is False
 assert op['source_range']==[-2147483648,2147483647] and len(op['decimal_digit_positions'])==10
 assert op['published_example']['destination_words_OFF']==[0x202d,0x3120,0x3332,0x3534,0x3736,0x0038]
 for key in ['current_source_data_verified','current_selector_value_verified','command_FALSE_output_behavior_established','indexed_effective_address_verified','Core_execution_verified','Native_execution_verified','full_source_relation_complete']:assert op[key] is False
 identity=facts['authored:applied:variant_family'+suffix]['value']
 assert identity['fnc_number']==261 and identity['base_opcode']=='BINDA'
 assert identity['variant_bindings']=={'BINDA':{'double':False,'pulse':False,'data_bits':16},'BINDAP':{'double':False,'pulse':True,'data_bits':16},'DBINDA':{'double':True,'pulse':False,'data_bits':32},'DBINDAP':{'double':True,'pulse':True,'data_bits':32}}
 assert facts['authored:applied:program_steps'+suffix]['value']['steps']==9
 assert facts['authored:applied:dbinda_execution_gate'+suffix]['value']['condition']==('rising_edge' if o.endswith('P') else 'execution_condition_ON')
 prior=next(e for e in json.loads((BASE/'baseline-source.json').read_text(encoding='utf-8'))['entries'] if e['target_model']==m and e['opcode']==o)
 for f in prior['facts']:
  if f['id'].startswith(('authored:applied:','authored:ql:')) and not f['id'].endswith(suffix):assert facts[f['id']]==f
 assert all(dep in facts for f in entry['facts'] for dep in f.get('depends_on',[]))

@pytest.mark.parametrize('entry',[e for e in ROWS if not e['opcode'].startswith('D')],ids=lambda e:e['opcode'])
def test_neighbor_own_header_only_preserves_incomplete_interface(entry):
 facts={f['id']:f for f in entry['facts']}
 header=facts['authored:applied:source_cpu_header@fx3_programming_r']
 assert header['value']['model']=='FX3U' and header['value']['minimum_version']=='2.20'
 assert facts['authored:applied:variant_family@fx3_programming_r']['value']['fnc_number']==261
 assert 'authored:applied:dbinda_documented_conversion@fx3_programming_r' not in facts
 catalogue=facts['source:instruction_catalogue:fx3_programming_r']
 assert {'context':'cpu_version','minimum':'2.20'} in catalogue['conditions']
 assert header['id'] in catalogue['depends_on']
 old=next(e for e in json.loads((BASE/'baseline-source.json').read_text(encoding='utf-8'))['entries'] if e['target_model']=='FX3U' and e['opcode']==entry['opcode'])
 for f in old['facts']:
  if f['id'].startswith(('authored:applied:','authored:ql:')) and f['id']!='authored:applied:variant_family@fx3_programming_r':assert facts[f['id']]==f
