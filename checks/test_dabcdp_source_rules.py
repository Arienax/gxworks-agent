"""Own X547 explicitly names the P form and its rising-edge diagram."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-dabcdp-native-contract-repair')
def entries():return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
def facts(model):return {f['id']:f for e in entries() if e['target_model']==model for f in e['facts']}
@pytest.mark.parametrize('model,extent',[('Q00U',True),('L02',True),('Q02',False)])
def test_dabcdp_own_header_native_layout_and_cpu_error_columns(model,extent):
 fs=facts(model);f=fs['authored:ql:native_arguments_s_d:parameters.typed_operands'];s,d=f['value']['operands']
 assert f['status']=='source_verified' and f['scope']==dict(models=[model],forms=['DABCDP'],languages=['ladder'])
 assert f['conditions']==[dict(context='project_mode',equals='native_ladder_instruction')]
 assert f['value']['native_form']==dict(operand_order=['S','D'],minimum_operands=2,maximum_operands=2)
 assert {p for ref in f['sources'] for p in ref['pdf_pages']} >= {133,547}
 assert s['type']=='STRING' and s['direction']=='input' and s['word_device_layout']['word_count']==2 and s['string_layout']['fixed_character_count']==4
 assert s['string_layout']['zero_byte_ends_conversion'] is False and d['type']=='ANY16' and d['direction']=='output' and d['word_device_layout']['word_count']==1
 assert d['encoding']=='BCD' and d['decimal_digits']==4
 cpu=fs['authored:ql:cpu_applicability:constraints.cpu_availability'];assert cpu['value']['available'] is True and not cpu.get('conditions')
 err=fs['authored:ql:error:effects.error_response:SM0|SD0']['value'];assert err['source_extent_error']['CPU_error_row_applicable'] is extent
 gate=fs['authored:ql:dabcdp_execution_gate:execution.operation_condition'];assert gate['status']=='source_verified'
 assert gate['value']['enabled_when']=='execution_condition_rising_edge'
 assert gate['value']['core_execution_established'] is gate['value']['native_execution_verified'] is False
 assert set(gate['depends_on'])=={'authored:ql:native_arguments_s_d:parameters.typed_operands','authored:ql:cpu_applicability:constraints.cpu_availability'}
def test_dabcdp_exact_fifty_original_full_namespace_and_p494_preserved():
 rows=entries();assert len(rows)==50 and sum(len(e['facts']) for e in rows)==1400
 original=json.loads((HERE/'original-unit.json').read_text());assert original['forms']==['DABCDP']
 assert {e['target_model'] for e in rows}==set(original['remaining'][0]['models'])
 prior={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'prior-owner-contracts.json').read_text())}
 assert sum(len(e['replaced_facts']) for e in prior.values())==150 and sum(len(e['preserved_facts']) for e in prior.values())==1150
 for e in rows:
  fs={f['id']:f for f in e['facts']}
  assert all(fs[f['id']]==f for f in prior[e['target_model'],e['opcode']]['preserved_facts'])
  assert all(set(f.get('depends_on',[]))<=fs.keys() for f in e['facts'])
 c=json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts'];assert len(c)==1 and len(c[0]['cpus'])==50
 assert c[0]['instruction']['mnemonic']==c[0]['instruction']['canonical_op']=='DABCDP'
 assert c[0]['execution_form']=='pulse' and c[0]['native_order']==['S','D']
def test_dabcdp_default_keeps_own_p_gate_and_specific_execution_limits():
 from knowledge.fact_selection import project_packet
 from shared.model_context import technical_context_units,technical_knowledge_text
 e=next(e for e in entries() if e['target_model']=='Q00U')
 packet=project_packet(dict(kind='instruction',target='DABCDP',scope=dict(models=['Q00U']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q00U',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
 record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target='DABCDP',_knowledge_context=dict(model='Q00U',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
 rendered=json.dumps(technical_context_units(technical_knowledge_text(record)))
 for token in ['execution_condition_rising_edge','short_literal_padding_behavior_established','source_destination_overlap_behavior_established','command_FALSE_output_behavior_established','native_execution_verified','fixed_four_decimal_ASCII_to_packed_BCD']:
  assert token in rendered,token
