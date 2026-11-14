"""CCD source heads, modes and physical result words remain distinct."""
import copy,json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-ccd-native-contract-repair')
VERSIONS={'FX3S':'1.00','FX3G':'1.00','FX3GC':'1.40','FX3U':'2.20','FX3UC':'1.00'}


def entries():return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']


def test_CCD_own_modern_heads_do_not_assert_source_span_or_full_algorithm():
    count=0
    for e in entries():
        if e['target_model'] not in VERSIONS:continue
        by={f['id']:f for f in e['facts']};typed=by['authored:applied:native_typed_operands@fx3_programming_r']
        assert typed['status']=='declared_unverified'
        assert {'context':'cpu_version','minimum':VERSIONS[e['target_model']]} in typed['conditions']
        s,d,n=typed['value']['operands']
        assert [s['name'],d['name'],n['name']]==['S','D','n']
        assert s['source_region_complete'] is False and s['source_head_width_is_complete_span'] is False
        assert 'word_device_layout' not in s
        assert d['word_device_layout']['word_count']==2 and d['word_device_layout']['single_scalar'] is False
        assert (n['range']['minimum'],n['range']['maximum'])==(1,256)
        doc=by['authored:applied:ccd_documented_processing@fx3_programming_r']['value']
        off,on=doc['mode_layouts']
        assert off['example']['n']==10 and len(off['example']['source_words'])==5
        assert off['general_exact_source_span_established'] is False and off['odd_n_byte_selection_established'] is False
        assert off['checked_byte_algorithm_established'] is False and doc['sum_raw_word_encoding_established'] is False
        assert doc['sum_encoding_conflict']['displayed_bits']=='0000010001000011'
        assert on['when']=={'M8161':True} and on['source_word_count']['name']=='n'
        assert on['unconditional_typed_source_span'] is False and doc['full_source_relation_complete'] is False
        count+=1
    assert count==10


def test_CCD_legacy_own_headers_and_NC_scope_are_separate_from_M():
    before={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'base-source-facts.json').read_text(encoding='utf-8'))['entries']}
    legacy_docs=0
    for e in entries():
        rows={f['id']:f for f in e['facts']}
        for old in before[e['target_model'],e['opcode']]['facts']:
            if any(r['manual_id']=='fxcpu_basic_applied_m' for r in old['sources']):assert rows[old['id']]==old
        for f in rows.values():
            if f['id'].startswith('authored:applied:ccd_documented_processing@jy992d'):
                legacy_docs+=1;off=f['value']['mode_layouts'][0]
                assert off['processing']=='consecutive_bytes' and off['general_exact_source_span_requires']==['aligned_16bit_word_head']
                assert off['checked_byte_algorithm_established'] is False and off['odd_n_byte_selection_established'] is False
                assert off['example']['n']==6 and len(off['example']['source_words'])==3
            if e['target_model']=='FX1NC' and f['sources'] and all(r['manual_id']=='jy992d88101e' for r in f['sources']):
                assert f['status']=='candidate_evidence' and f['verification']=='not_performed' and f['value']['source_scope_confirmed'] is False
        if e['target_model']=='FX0N' and e['opcode']=='CCDP':
            negative=rows['authored:applied:cpu_availability@jy992d48301j']
            assert negative['value']['available'] is False
            for f in rows.values():
                if f['sources'] and all(r['manual_id']=='jy992d48301j' for r in f['sources']) and f is not negative:
                    assert f['status']=='candidate_evidence' and f['verification']=='not_performed' and f['value']['source_form_supported'] is False
        if e['target_model']=='FX1':
            assert rows['authored:applied:cpu_availability@jy992d48301j']['value']['available'] is False
    assert legacy_docs==15


def test_CCD_partial_source_does_not_create_a_registry_storage_contract():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.device_identity import instruction_operand_regions
    e=next(e for e in entries() if (e['target_model'],e['opcode'])==('FX3U','CCD'))
    typed=next(f for f in e['facts'] if f['id']=='authored:applied:native_typed_operands@fx3_programming_r')
    spec=registry.resolve('CCD',cpu='FX3U',operand_count=3)
    pins=typed['value']['operands']
    for count in [1,2,10,255,256]:
        regions=instruction_operand_regions(spec,'CCD',['D100','D50','K'+str(count)],'FX3U',parameters=[[p] for p in pins])
        assert not any(r['operand_index']==0 for r in regions)
        assert spec is None or not spec.operands
        assert regions==[]
        assert pins[1]['word_device_layout']['word_count']==2


def test_CCD_default_projection_keeps_partial_modes_and_scope_boundaries():
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    def walk(x):
        if isinstance(x,dict):
            yield x
            for v in x.values():yield from walk(v)
        elif isinstance(x,list):
            for v in x:yield from walk(v)
    seen=0
    for e in entries():
        model,op=e['target_model'],e['opcode']
        packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=[model]),content_origin='authored_fact',value=e),plc_model=model,language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
        record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model=model,language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
        nodes=list(walk(technical_context_units(technical_knowledge_text(record))))
        docs=[f for f in e['facts'] if f['id'].startswith('authored:applied:ccd_documented_processing@')]
        if docs:
            assert any(n.get('source_head_width_is_complete_span') is False and n.get('current_selector_value_verified') is False and n.get('full_source_relation_complete') is False for n in nodes)
            assert any(n.get('odd_n_byte_selection_established') is False and n.get('checked_byte_algorithm_established') is False for n in nodes)
            seen+=len(docs)
        if model=='FX1NC':assert any(n.get('source_scope_confirmed') is False for n in nodes)
        if (model,op)==('FX0N','CCDP'):assert any(n.get('source_form_supported') is False for n in nodes)
    assert seen==25


def test_CCD_own_unavailable_pulse_withdrawal_keeps_negative_and_M():
    from tools.fact_applied_review import _qualify_fx0n_ccdp_j_scope
    rows=[dict(dimension='parameters.typed_operands',value={'operands':[{'name':'S'}]},sources=[{'manual_id':'jy992d48301j'}],status='declared_unverified',verification='source_checked'),
          dict(dimension='constraints.cpu_availability',value={'available':False},sources=[{'manual_id':'jy992d48301j'}],status='source_verified',verification='source_checked'),
          dict(dimension='parameters.typed_operands',value={'operands':[{'name':'s'}]},sources=[{'manual_id':'fxcpu_basic_applied_m'}],status='source_verified',verification='source_checked')]
    before=copy.deepcopy(rows)
    _qualify_fx0n_ccdp_j_scope(rows)
    assert rows[0]['status']=='candidate_evidence' and rows[0]['value']['source_form_supported'] is False
    assert rows[1:]==before[1:]
    once=copy.deepcopy(rows);_qualify_fx0n_ccdp_j_scope(rows);assert rows==once
