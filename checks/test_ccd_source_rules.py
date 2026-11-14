"""Own Native CCD modes retain unresolved physical and write boundaries."""
import copy,json
from pathlib import Path
import pytest

HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-ccd-native-contract-repair')

def entries():return json.loads((HERE/'source-facts.json').read_text())['entries']

@pytest.mark.parametrize('op',['CCD','CCDP'])
def test_ccd_original_scope_and_own_conditional_layouts(op):
    rows=[e for e in entries() if e['opcode']==op];assert len(rows)==(53 if op=='CCD' else 39)
    positive=0
    for e in rows:
        own=[f for f in e['facts'] if any(s['manual_id']=='sh080809engx' for s in f['sources'])]
        assert all(all(456<=p<=458 for p in s['pdf_pages']) for f in own for s in f['sources'])
        byid={f['id']:f for f in own};availability=byid['authored:ql:cpu_applicability:constraints.cpu_availability']
        typed=byid.get('authored:ql:native_arguments_s_d_n:parameters.typed_operands')
        if availability['value']['available'] is False:
            assert typed is None
            continue
        positive+=1;assert typed['status']=='source_verified'
        s,d,n=typed['value']['operands'];assert [p['name'] for p in [s,d,n]]==['S','D','n']
        assert all(p['type']=='ANY16' for p in [s,d,n])
        assert 'word_device_layout' not in s and s['scalar_head_is_complete_span'] is False and s['complete_referent_span_established'] is False
        layout=s['conditional_word_device_layout'];assert layout['selector_device']=='SM772' and layout['selector_value_verified'] is False
        assert layout['ON']['word_device_layout']['word_count']=={'op':'parameter','name':'n','type':{'kind':'int','bits':32,'signed':True}}
        assert layout['OFF']['general_exact_span_established'] is False and layout['OFF']['odd_count_final_byte_selection_established'] is False
        assert d['word_device_layout']['word_count']==2 and d['word_device_layout']['single_scalar'] is False and d['parity_write_target_established'] is False
        assert n['word_device_layout']['word_count']==1 and n['range']['minimum']==1 and n['range']['maximum']==256 and 'passing' not in n
        assert all(p['range']['address_forms']==[] and 'Z' not in p['range']['device_prefixes'] for p in [s,d,n])
        assert s['range']['literal_prefixes']==d['range']['literal_prefixes']==[] and n['range']['literal_prefixes']==['K','H']
        condition=next((c for c in typed.get('conditions',[]) if c.get('kind')=='serial_number_minimum'),None)
        restricted=e['target_model'].startswith('L') or 'UDV' in e['target_model'] or 'UDPV' in e['target_model']
        assert bool(condition)==restricted
        if condition:assert condition['minimum']=='16112'
    assert positive==39

@pytest.mark.parametrize('op',['CCD','CCDP'])
def test_ccd_source_numeric_constraint_and_serial_boundaries(op):
    from plc.instruction_constraints import check_fact_constraints
    for model in ['Q00U','Q03UDV','L02']:
        facts=next(e['facts'] for e in entries() if e['target_model']==model and e['opcode']==op)
        root=next(f['id'] for f in facts if f['id'].startswith('authored:ql:native_ccd_count:'))
        for n,status in [(0,'failed'),(1,'passed'),(256,'passed'),(257,'failed')]:
            result=check_fact_constraints(facts,{'$2':n,'N':n},target=op,plc_model=model,language='ladder',context={'project_mode':'native_ladder_instruction','cpu_serial_first_five':16112},root_ids=[root])
            assert result['status']==status,result
        if model!='Q00U':
            availability=[f['id'] for f in facts if f['dimension']=='constraints.cpu_availability']
            for context,status in [({'project_mode':'native_ladder_instruction'},'unverified'),({'project_mode':'native_ladder_instruction','cpu_serial_first_five':16111},'failed')]:
                # Below the CPU condition a numeric root is not applicable, not a failed numeric range.
                result=check_fact_constraints(facts,{},target=op,plc_model=model,language='ladder',context=context,root_ids=availability)
                assert result['status']==status,result

@pytest.mark.parametrize('op',['CCD','CCDP'])
def test_ccd_native_default_delivers_partial_mode_and_parity_boundaries(op):
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    e=next(e for e in entries() if e['target_model']=='Q00U' and e['opcode']==op)
    packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q00U']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q00U',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q00U',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    visible=json.dumps(technical_context_units(technical_knowledge_text(record)))
    for field in ['SM772','conditional_word_device_layout','mode8','mode16','own_even_count_example','parity_write_target_conflict','general_exact_source_extent_established','odd_count_final_byte_selection_established']:
        assert field in visible,field
    for field in ['full_source_relation_complete','mode_value_verified','general_packed_byte_algorithm_established','low_first_byte_order_established','parity_write_target_established']:
        assert '"'+field+'": false' in visible,field
    assert 'ceil' not in visible and 'floor' not in visible

def test_ccd_layout_helper_requires_own_literal_page_and_all_mode_clauses():
    from types import SimpleNamespace
    from tools.fact_ql_behavior_review import _native_ccd_witnesses
    section=SimpleNamespace(manual='sh080809engx',pages=[456,457,458],bind=lambda w:[('own',456)])
    assert _native_ccd_witnesses(section,'CCD') and not _native_ccd_witnesses(section,'CCD_E')
    section.pages=[485];assert not _native_ccd_witnesses(section,'CCD')
    section.pages=[456];section.bind=lambda w:[] if '8-bit conversion mode' in w else [('own',456)]
    assert not _native_ccd_witnesses(section,'CCD')
