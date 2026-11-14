"""Own FROM-family DFRO/P source facts, distinct interfaces and honest hardware boundary."""
import json
from pathlib import Path
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-dfro-native-contract-repair')
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
ROWS=read('source-facts.json')['entries']
def fs(e):return {f['id']:f for f in e['facts']}
def native(e):return [f for f in e['facts'] if f['dimension']=='parameters.typed_operands' and f['value'].get('native_form')]
def test_dfro_exact_original106_complete_namespace_and_native_contracts():
 assert len(ROWS)==106 and sum(len(e['facts']) for e in ROWS)==5294
 assert {(e['target_model'],e['opcode']) for e in ROWS}=={(e['target_model'],e['opcode']) for e in read('owner-selectors.json')}
 cs=read('contract-additions.json')['cpu_contracts'];assert len(cs)==2 and all(len(c['cpus'])==53 and c['native_order']==['n1','n2','D','n3'] for c in cs)
 for c in cs:
  assert all(s['manual_id']=='sh080809engx' and isinstance(s['pdf_page'],int) for s in c['sources'])
 assert sum(len(e['replaced_facts']) for e in read('prior-owner-contracts.json'))==4400
 assert all(set(f.get('depends_on',[]))<=fs(e).keys() for e in ROWS for f in e['facts'])
def test_dfro_native_head_and_dynamic_payload_are_not_conflated():
 for e in ROWS:
  for f in native(e):
   shared='CPU_shared_memory' in f['id'];ps=f['value']['operands'];assert [p['name'] for p in ps]==['n1','n2','D','n3']
   assert [p['type'] for p in ps]==['ANY16','ANY16','ANY16' if shared else 'ANY32','ANY16']
   d=ps[2];assert d['semantic_access']=='write' and d['word_device_layout']['head_storage_only'] is True and d['word_device_layout']['single_scalar'] is False
   assert d['region_span']==dict(unit='word',count_expression=dict(operator='multiply',operands=[2,{'parameter':'n3'}]),element_bits=16,word_stride=1)
   assert ps[0]['range']['module_selector_prefixes']==['U'] and ps[0]['module_number_numeric_mapping_established'] is False
   assert all('module_selector_prefixes' not in p['range'] for p in ps[1:])
   assert all(p['index_modification_applicability_established'] is False for p in ps)
def test_dfro_case_predicates_and_hardware_validity_remain_distinct():
 for e in ROWS:
  facts=fs(e)
  for f in native(e):
   conditions=json.dumps(f['conditions']);assert 'selected_cpu_shared_memory_target' in conditions and '992' in conditions and '995' in conditions
  requirements=[f for f in e['facts'] if f['dimension']=='constraints.documented_requirements']
  assert any(f['value'].get('selection_domain_not_module_existence_proof') is True for f in requirements)
  assert any(f['value'].get('actual_module_and_memory_configuration_verified') is False for f in requirements)
  assert not any(f['dimension']=='constraints.serial_number_minimum' for f in e['facts'])
  expected_mode={'any_of':[{'context':'$LANGUAGE','equals':'st'},{'context':'$LANGUAGE','equals':'fbd'},{'context':'project_mode','equals':'structured_ladder_block'}]}
  for f in e['facts']:
   if any(s.get('manual_id')=='sh080783engp' for s in f['sources']):assert expected_mode in f.get('conditions',[])
def test_dfro_cpu_scope_and_p_interface_negative_do_not_leak_native():
 for e in ROWS:
  model=e['target_model'];facts=fs(e);ordinary=facts['authored:ql:cpu_applicability:constraints.cpu_availability']
  assert ordinary['value']['available'] is True and ordinary['value']['interface_mode']=='Native'
  structured=[f for f in e['facts'] if f['dimension']=='constraints.cpu_availability' and f['value'].get('interface_mode')=='structured' and f['value'].get('interface')=='intelligent_module_buffer']
  assert len(structured)==1 and structured[0]['value']['available'] is (model!='Q00UJ')
  if model=='Q00UJ':assert 'authored:ql:argument_table_st:parameters.typed_operands' not in facts
  shared=[f for f in e['facts'] if f['dimension']=='constraints.cpu_availability' and f['value'].get('interface')=='CPU_shared_memory']
  if model in {'Q00','Q01'}:assert any('04122' in json.dumps(f['conditions']) and f['value'].get('available') is True for f in shared)
  if model in {'Q02H','Q02PH'}:assert all('available' not in f['value'] and f['value']['DFRO_shared_CPU_eligibility_established'] is False for f in shared)
def test_dfro_own_conflicts_and_partial_relation_are_explicit():
 for e in ROWS:
  for f in e['facts']:
   if f['dimension']=='effects.operation':
    v=f['value'];assert v['word_count_multiplier']==2 and v['full_source_relation_complete'] is False and v['core_execution_established'] is False
    assert v['target_module_presence_verified'] is v['target_module_configuration_verified'] is False
    if v['operation']=='read_CPU_shared_memory':assert v['zero_count_no_processing'] is True and v['printed_error_extent_multiplier_established'] is False and v['source_conflict'] and v['unresolved_items']
  for f in native(e):
   if 'CPU_shared_memory' in f['id']:
    n1,n2,d,n3=f['value']['operands'];assert n1['documented_CPU_class_selector_ranges']['Basic_QCPU']==dict(minimum_hex='3E0',maximum_hex='3E2')
    assert n2['documented_CPU_class_address_ranges']['Universal_QCPU']['intervals']==[[0,4095],[10000,24335]] and n2['Basic_address_upper_bound_established'] is False
    assert n3['documented_positive_count_ranges']==dict(Basic_QCPU=[1,256],Universal_QCPU=[1,7168]) and n3['zero_count_behavior']=='no_processing'
  for f in e['facts']:
   if e['opcode']=='DFROP' and f['dimension']=='execution.operation_condition' and f['scope']['languages']==['st','fbd','ladder'] and f['value'].get('source_conflict'):
    assert f['value']['printed_execution_row_literal_matches_header'] is False
  assert all(f['scope']['languages']==['st','fbd','ladder'] for f in e['facts'] if f['dimension'] in {'effects.operation','execution.operation_condition','constraints.documented_requirements','effects.error_response'} and any(s.get('manual_id')=='sh080783engp' for s in f['sources']))
def test_dfro_actual_compose_strict_and_default_delivery():
 r=read('local-admission-check.json');assert r['passed'] and len(r['owners'])==106 and all(e['all_source_active'] and e['dependency_closed'] for e in r['owners'])
 r=read('default-projection-check.json');assert r['passed'] and len(r['checks'])==212
