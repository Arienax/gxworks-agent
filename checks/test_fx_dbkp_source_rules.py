"""Own R603-605 paired-block source; no Q/L count/domain assumptions."""
import json
from pathlib import Path
import pytest
BASE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-dbkp-native-contract-repair')
ROWS=json.loads((BASE/'source-facts.json').read_text(encoding='utf-8'))['entries']
@pytest.mark.parametrize('entry',ROWS,ids=lambda e:e['target_model']+'-'+e['opcode'])
def test_own_paired_block_and_complete_namespace(entry):
 m,o=entry['target_model'],entry['opcode'];facts={f['id']:f for f in entry['facts']};suffix='@fx3_programming_r'
 typed=facts['authored:applied:native_typed_operands'+suffix]
 assert typed['status']=='source_verified' and typed['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'},{'context':'cpu_version','minimum':'2.20'}]
 pins=typed['value']['operands'];assert [x['name'] for x in pins]==['S1','S2','D','n']
 for i,p in enumerate(pins):
  assert p['type']=='ANY32' and p['signed'] is True and p['word_device_layout']['word_count']==2
  assert p['range']['device_prefixes']==(['D','R'] if i==3 else ['T','C','D','R'])
  assert p['range']['literal_prefixes']==(['K','H'] if i in [1,3] else [])
  assert p['range']['address_forms']==[] and p['range']['index_modification'] is (i!=3)
  assert 'minimum' not in p['range'] and 'maximum' not in p['range']
  if i<3:assert p['region_span']=={'count_expression':{'operator':'multiply','operands':[2,{'parameter':'n'}]},'unit':'word','device_unit_bits':16}
  else:assert p['role']=='count' and 'passing' not in p
 assert 'passing' not in pins[1] and pins[0]['passing']==pins[2]['passing']=='device_reference'
 identity=facts['authored:applied:variant_family'+suffix]['value'];assert identity['fnc_number']==192 and identity['base_opcode']=='BK+'
 assert identity['variant_bindings'][o]=={'double':True,'pulse':o.endswith('P'),'data_bits':32}
 assert facts['authored:applied:program_steps'+suffix]['value']['steps']==17
 op=facts['authored:applied:dbkp_documented_operation'+suffix]['value']
 assert op['count_unit']=='32bit_elements' and op['element_word_stride']==2
 assert op['printed_clause_conflicts_with_n_paired_element_diagram'] is True
 for k in ['printed_clause_is_vendor_erratum','numeric_count_bounds_established','zero_count_execution_established','negative_count_execution_established','carry_flag_turns_ON_on_overflow','current_count_value_verified','current_source_data_verified','indexed_effective_address_verified','command_FALSE_output_behavior_established','Core_execution_verified','Native_execution_verified','full_source_relation_complete']:assert op[k] is False
 for i,name in enumerate(['s1','s2']):
  constraint=facts['authored:applied:dbkp_disjoint_'+name+suffix]['value']
  assert constraint['device_unit_bits']==16
  assert constraint['source_operand_index']==i and constraint['source_length_multiplier']==constraint['destination_length_multiplier']==2 and constraint.get('allow_identical',False) is False
 assert facts['authored:applied:dbkp_execution_gate'+suffix]['value']['condition']==('rising_edge' if o.endswith('P') else 'execution_condition_ON')
 prior=next(e for e in json.loads((BASE/'baseline-source.json').read_text(encoding='utf-8'))['entries'] if e['target_model']==m and e['opcode']==o)
 for f in prior['facts']:
  if f['id'].startswith(('authored:applied:','authored:ql:')) and not f['id'].endswith(suffix):assert facts[f['id']]==f
 assert all(dep in facts for f in entry['facts'] for dep in f.get('depends_on',[]))

def test_S2_repeated_constant_own_R604_rule_only():
 correction=json.loads((BASE/'s2-disjoint-correction.json').read_text(encoding='utf-8'))
 assert correction['unchanged_facts']==114 and len(correction['entries'])==4
 for entry in ROWS:
  facts={f['id']:f for f in entry['facts']};sid='authored:applied:dbkp_disjoint_s2@fx3_programming_r';s1=facts['authored:applied:dbkp_disjoint_s1@fx3_programming_r']
  assert facts[sid]['value']['repeated_constant_has_no_device_region'] is True
  assert 'repeated_constant_has_no_device_region' not in s1['value']
  change=next(c for c in correction['entries'] if c['target_model']==entry['target_model'] and c['opcode']==entry['opcode'])
  import copy
  expected=copy.deepcopy(change['before']);expected['value']['repeated_constant_has_no_device_region']=True
  assert expected==change['after']==facts[sid]
  assert any(s['manual_id']=='fx3_programming_r' and s['revision']=='R' and 604 in s['pdf_pages'] for s in facts[sid]['sources'])
