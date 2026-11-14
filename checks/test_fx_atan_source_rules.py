"""Own R528-529 ATAN source rules, never inherited ASIN domain restrictions."""
import json
from pathlib import Path

HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-atan-native-contract-repair')


def entries():return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
def own(e,key):return next(f for f in e['facts'] if f['id']=='authored:applied:'+key+'@fx3_programming_r')


def test_original_four_native_members_and_complete_namespace():
    source=entries();selectors=json.loads((HERE/'owner-selectors.json').read_text())
    assert len(source)==len(selectors)==4
    assert {(e['target_model'],e['opcode']) for e in source}=={(s['target_model'],s['opcode']) for s in selectors}
    base={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'baseline-source.json').read_text())['entries']}
    for e in source:
        current={f['id']:f for f in e['facts']}
        for f in base[e['target_model'],e['opcode']]['facts']:
            if f['id'].startswith(('authored:applied:','authored:ql:')):assert f['id'] in current
            if f['id'].startswith('authored:applied:') and not f['id'].endswith('@fx3_programming_r'):assert current[f['id']]==f


def test_own_REAL_double_words_versions_and_device_grid():
    for e in entries():
        f=own(e,'native_typed_operands');assert f['status']=='source_verified'
        assert {'context':'project_mode','equals':'native_ladder_instruction'} in f['conditions']
        assert {'context':'cpu_version','minimum':'2.20' if e['target_model']=='FX3U' else '1.00'} in f['conditions']
        assert any(s.get('pdf_pages')==[528,529] for s in f['sources'])
        s,d=f['value']['operands']
        assert [s['type'],d['type']]==['REAL','REAL'] and [s['direction'],d['direction']]==['input','output']
        assert s['word_device_layout']['word_count']==d['word_device_layout']['word_count']==2
        for p in [s,d]:
            assert p['range']['device_prefixes']==['D','R'] and p['range']['address_forms']==['buffer_memory']
            assert p['range']['index_modification'] is True
        assert s['range']['literal_prefixes']==['E'] and d['range']['literal_prefixes']==[]
        assert 'minimum' not in s['range'] and 'maximum' not in s['range']


def test_own_arctangent_radians_without_ASIN_range_or_error_claim():
    for e in entries():
        v=own(e,'atan_documented_processing')['value']
        assert v['operation']=='arctangent' and v['output_angle_unit']=='radian'
        assert v['output_range']==dict(minimum='-pi/2',maximum='pi/2')
        assert v['documented_example']==dict(input=1,output=0.785398)
        for k in ['minus_one_to_plus_one_input_restriction_stated','output_range_endpoint_inclusivity_established',
                  'operation_error_conditions_established','command_FALSE_output_behavior_established',
                  'source_destination_overlap_behavior_established','indexed_effective_address_verified',
                  'current_source_value_verified','special_float_input_behavior_established',
                  'core_execution_established','native_execution_verified','full_source_relation_complete']:
            assert v[k] is False
        assert not any(f['dimension']=='effects.error_response' and f['id'].endswith('@fx3_programming_r') for f in e['facts'])


def test_own_FNC135_nine_steps_and_separate_pulse_gate():
    for e in entries():
        assert own(e,'variant_family')['value']['fnc_number']==135
        assert own(e,'program_steps')['value']['steps']==9
        assert own(e,'execution_form')['value']['form']==('pulse' if e['opcode']=='DATANP' else 'continuous')
        gate=own(e,'atan_execution_gate')['value']
        assert gate['condition']==('rising_edge' if e['opcode']=='DATANP' else 'execution_condition_ON')
        assert gate['Core_execution_verified'] is gate['Native_execution_verified'] is False
