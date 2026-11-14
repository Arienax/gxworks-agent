"""Own FX R666-668 device permissions; preserve previously verified BAND behavior."""
import json
from pathlib import Path

HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-dband-native-contract-repair')


def rows():return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
def own(e,key):return next(f for f in e['facts'] if f['id']=='authored:applied:'+key+'@fx3_programming_r')


def test_original_four_and_explicit_adjacent_four_not_relabelled():
    selected=json.loads((HERE/'owner-selectors.json').read_text())
    assert len(rows())==len(selected)==8
    original=[s for s in selected if s['source_scope_role']=='original_DBAND_remaining']
    adjacent=[s for s in selected if s['source_scope_role']=='adjacent_same_table_S3_correction']
    assert len(original)==len(adjacent)==4
    assert {s['original_id'] for s in original}=={'B-00180','B-00181'}
    assert {s['original_id'] for s in adjacent}=={'B-00060','B-00061'}
    base={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'baseline-source.json').read_text())['entries']}
    for e in rows():
        current={f['id']:f for f in e['facts']}
        for f in base[e['target_model'],e['opcode']]['facts']:
            if f['id'].startswith(('authored:applied:','authored:ql:')):assert f['id'] in current
            if f['id'].startswith(('authored:applied:','authored:ql:')) and f['id'] not in {
                'authored:applied:native_typed_operands@fx3_programming_r',
                'authored:applied:variant_family@fx3_programming_r',
                'authored:applied:band_native_error_qualification@fx3_programming_r'}:assert current[f['id']]==f


def test_own_signed_width_literals_and_S3_device_only():
    for e in rows():
        bits=32 if e['opcode'].startswith('DBAND') else 16
        f=own(e,'native_typed_operands');v=f['value'];assert f['status']=='source_verified'
        assert {'context':'project_mode','equals':'native_ladder_instruction'} in f['conditions']
        assert {'context':'cpu_version','minimum':'2.20' if e['target_model']=='FX3U' else '1.00'} in f['conditions']
        assert [p['name'] for p in v['operands']]==['S1','S2','S3','D']
        for i,p in enumerate(v['operands']):
            assert p['type']=='ANY'+str(bits) and p['signed'] is True and p['encoding']=='binary'
            assert p['word_device_layout']['word_count']==bits//16
            assert p['direction']==('output' if i==3 else 'input')
            assert p['range']['literal_prefixes']==(['K','H'] if i<2 else [])
            if i>=2:assert p['passing']=='device_reference'
            else:assert p['range']['integer_literal_widths']=={'K':bits,'H':bits}
        assert own(e,'variant_family')['value']['fnc_number']==257


def test_own_Kn_class_and_Modify_without_invented_alignment():
    for e in rows():
        bits=32 if e['opcode'].startswith('DBAND') else 16
        for i,p in enumerate(own(e,'native_typed_operands')['value']['operands']):
            r=p['range'];assert r['device_prefixes']==['T','C','D','R']
            assert r['digit_group_device_prefixes']==(['X','Y','M','S'] if i!=3 else ['Y','M','S'])
            assert r['digit_groups_max']==bits//4 and r['address_forms']==['buffer_memory'] and r['index_modification'] is True
            assert 'digit_groups_allowed' not in r and 'digit_group_start_bit_multiple' not in r


def test_bounds_error_is_not_S3_outside_band_and_current_order_unknown():
    for e in rows():
        v=own(e,'band_native_error_qualification')['value']
        assert v['documented_error_condition']==dict(left='S1',comparison='greater_than',right='S2')
        assert (v['error_flag'],v['error_code_device'],v['error_code'])==('M8067','D8067',6706)
        for key in ['S3_outside_dead_band_is_operation_error','bounds_order_static_validation_established',
                    'current_bounds_order_verified','current_S3_value_verified','error_destination_behavior_established',
                    'core_execution_established','native_execution_verified','full_source_relation_complete']:
            assert v[key] is False
