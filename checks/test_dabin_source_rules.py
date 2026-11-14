"""Own Native DABIN fixed signed ASCII source rules; no execution claim."""
import json
from pathlib import Path
import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def entries(kind):
    folder = BASE / (kind + '-dabin-native-contract-repair')
    name = 'ql-source-facts.json' if kind == 'ql' else 'source-facts.json'
    return json.loads((folder / name).read_text(encoding='utf-8'))['entries']


@pytest.mark.parametrize('kind,count', [('ql', 100), ('fx', 4)])
def test_own_six_character_storage_and_direction(kind, count):
    rows = entries(kind)
    assert len(rows) == count
    for entry in rows:
        fact_id = ('authored:ql:native_arguments_s_d:parameters.typed_operands' if kind == 'ql'
                   else 'authored:applied:native_typed_operands@fx3_programming_r')
        fact = next(f for f in entry['facts'] if f['id'] == fact_id)
        s, d = fact['value']['operands']
        assert (s['direction'], d['direction']) == ('input', 'output')
        assert s['word_device_layout']['word_count'] == 3
        assert s['word_device_layout']['element_count'] == 6
        assert d['word_device_layout']['word_count'] == 1
        assert s['passing'] == d['passing'] == 'device_reference'
        assert fact['status'] == 'source_verified'
        assert {'context': 'project_mode', 'equals': 'native_ladder_instruction'} in fact['conditions']


def test_fx_sign_errors_zero_digits_and_exact_mapping():
    for entry in entries('fx'):
        fact = next(f for f in entry['facts'] if f['id'].startswith('authored:applied:dabin_documented_conversion@'))
        v = fact['value']
        assert v['sign_position'] == dict(source_word_offset=0, source_byte_offset=0)
        assert v['character_positions'] == [dict(source_word_offset=(i+1)//2, source_byte_offset=(i+1)%2, multiplier=10**(4-i)) for i in range(5)]
        assert v['other_sign_ASCII_codes_processed_as'] == 'operation_error'
        assert v['digit_zero_ASCII_codes'] == [0, 32]
        assert v['zero_byte_is_terminator'] is False
        assert v['published_example'] == dict(source_words=[0x202d, 0x3220, 0x3637], result=-276)
        assert v['full_source_relation_complete'] is False
        assert v['Native_execution_verified'] is v['Core_execution_verified'] is False
        assert {'context': 'cpu_version', 'minimum': '2.20'} in fact['conditions']
        error = next(f for f in entry['facts'] if f['id'].startswith('authored:applied:dabin_documented_errors@'))['value']
        assert error['error_code'] == 6706 and error['source_extent'] == {'word_count': 3}
        assert error['numeric_result_range'] == [-32768, 32767]


def test_fx_permissions_and_pulse_are_own_source():
    for e in entries('fx'):
        typed = next(f for f in e['facts'] if f['id'].startswith('authored:applied:native_typed_operands@'))['value']
        s, d = typed['operands']
        assert s['range']['device_prefixes'] == ['T', 'C', 'D', 'R']
        assert s['range']['literal_prefixes'] == s['range']['address_forms'] == []
        assert d['range']['digit_group_device_prefixes'] == ['Y', 'M', 'S']
        gate = next(f for f in e['facts'] if f['id'].startswith('authored:applied:dabin_execution_gate@'))['value']
        assert gate['condition'] == ('rising_edge' if e['opcode'] == 'DABINP' else 'execution_condition_ON')


def test_full_original_namespace_retention():
    for kind in ('ql', 'fx'):
        folder = BASE / (kind + '-dabin-native-contract-repair')
        baseline = {(e['target_model'], e['opcode']): e for e in json.loads((folder/'baseline-source.json').read_text())['entries']}
        for e in entries(kind):
            current = {f['id']: f for f in e['facts']}
            for f in baseline[e['target_model'],e['opcode']]['facts']:
                if f['id'].startswith(('authored:applied:', 'authored:ql:')):
                    assert f['id'] in current
                if any(s.get('manual_id') == 'fxcpu_basic_applied_m' for s in f.get('sources', [])):
                    assert current.get(f['id'], f) == f


def test_ql_own_sign_mapping_and_printed_error_scope():
    for entry in entries('ql'):
        op = next(f for f in entry['facts'] if f['id'] == 'authored:ql:operation:effects.conversion')['value']
        assert [(p['source_word_offset'], p['source_byte_offset'], p['multiplier']) for p in op['character_positions']] == [(0,1,10000),(1,0,1000),(1,1,100),(2,0,10),(2,1,1)]
        assert op['other_sign_ASCII_codes_processed_as'] == 'positive'
        assert op['zero_digit_ASCII_codes'] == [0,32] and op['string_terminator_stops_conversion'] is False
        assert op['valid_decimal_range'] == [-32768,32767]
        assert op['published_example'] == dict(source_words=[0x322d,0x3135,0x3830],destination_value=-25108)
        err = next(f for f in entry['facts'] if f['id'] == 'authored:ql:error:effects.error_response:SM0|SD0')['value']
        for key in ['printed_scope_complete', 'printed_character_error_span_is_exact_DABIN_storage', 'character_error_predicate_including_sign_complete']:
            assert err[key] is False


def test_fx_own_fnc_number_is_not_chapter_range_start():
    for e in entries('fx'):
        fact = next(f for f in e['facts'] if f['id'] == 'authored:applied:variant_family@fx3_programming_r')
        assert fact['value']['fnc_number'] == 260
        assert all(s['pdf_pages'] == [676,677,678] for s in fact['sources'])
