"""Own R606-608 paired-block source; no Q/L count/domain assumptions."""
import json
from pathlib import Path
import pytest
BASE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-dbkm-native-contract-repair')
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
 identity=facts['authored:applied:variant_family'+suffix]['value'];assert identity['fnc_number']==193 and identity['base_opcode']=='BK-'
 assert identity['variant_bindings'][o]=={'double':True,'pulse':o.endswith('P'),'data_bits':32}
 assert facts['authored:applied:program_steps'+suffix]['value']['steps']==17
 op=facts['authored:applied:dbkm_documented_operation'+suffix]['value']
 assert 'overflow' not in op
 assert op['operation']=='paired_signed_BIN32_block_subtraction' and op['minuend']=='S1' and op['subtrahend']=='S2'
 assert op['published_overflow_examples']==[{'left':-2147483648,'right':2,'result':2147483646},{'left':2147483647,'right':-2,'result':-2147483647}]
 assert op['count_unit']=='32bit_elements' and op['element_word_stride']==2
 assert op['printed_clause_conflicts_with_n_paired_element_diagram'] is True
 for k in ['printed_clause_is_vendor_erratum','general_overflow_algorithm_established','numeric_count_bounds_established','zero_count_execution_established','negative_count_execution_established','carry_flag_turns_ON_on_overflow','current_count_value_verified','current_source_data_verified','indexed_effective_address_verified','command_FALSE_output_behavior_established','Core_execution_verified','Native_execution_verified','full_source_relation_complete']:assert op[k] is False
 for i,name in enumerate(['s1','s2']):
  constraint=facts['authored:applied:dbkm_disjoint_'+name+suffix]['value']
  assert constraint['device_unit_bits']==16
  assert constraint.get('repeated_constant_has_no_device_region',False) is (i==1)
  assert constraint['source_operand_index']==i and constraint['source_length_multiplier']==constraint['destination_length_multiplier']==2 and constraint.get('allow_identical',False) is False
 assert facts['authored:applied:dbkm_execution_gate'+suffix]['value']['condition']==('rising_edge' if o.endswith('P') else 'execution_condition_ON')
 prior=next(e for e in json.loads((BASE/'baseline-source.json').read_text(encoding='utf-8'))['entries'] if e['target_model']==m and e['opcode']==o)
 for f in prior['facts']:
  if f['id'].startswith(('authored:applied:','authored:ql:')) and not f['id'].endswith(suffix):assert facts[f['id']]==f
 assert all(dep in facts for f in entry['facts'] for dep in f.get('depends_on',[]))

def test_exact_printed_heading_recovery_is_own_R606_only():
 import sqlite3
 from tools.fact_applied_review import _chunk_records,MANUAL_LAYOUTS
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
 c.execute('CREATE TABLE chunks(id,manual_id,manual_number,revision,section,chapter,outline_path,pdf_page,source_pages_json,source_applicability_json,text)')
 heading='25.2 NFC193 – BK– / Block Data Subtraction'
 for i,(manual,revision,page,title) in enumerate([('fx3_programming_r','R',606,heading),('fx3_programming_r','R',609,heading),('fx3_programming_r','S',606,heading),('fx3_programming_r','R',606,heading+' extra')]):
  c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?)',(i,manual,'JY997D16601',revision,title,'','',page,'['+str(page)+']','{}','own fixture'))
 # Give distinct sections per call so a wrong-page row cannot share the valid group.
 # Only test the exact adapter via a one-row source wrapper; no manual scope table is needed.
 class Rows(list):
  def fetchall(self):return list(self)
  def fetchone(self):return self[0] if self else None
 class Source:
  def __init__(self,row):self.row=row
  def execute(self,sql,args=()):
   if 'FROM chunks c' in sql:return Rows([self.row])
   return Rows()
 rows=c.execute('SELECT *,source_pages_json AS pages,source_applicability_json AS applicability FROM chunks').fetchall()
 for row,expected in zip(rows,[True,False,False,False]):
  records=list(_chunk_records(Source(row),{'fx3_programming_r':MANUAL_LAYOUTS['fx3_programming_r']},set()))
  assert bool(records) is expected
  if expected:assert records[0]['opcode']=='BK-' and records[0]['section']==heading and records[0]['pdf_pages']==[606]
 c.close()
