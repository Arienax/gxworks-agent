"""DEADD/P own Native evidence, separate from legacy generic compatibility."""
import json
from pathlib import Path

from tools import fact_applied_review as producer

HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-deadd-native-contract-repair')


def test_DEADD_own_REAL_pairs_conversion_permissions_and_literal_family():
    entries = json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    positive = 0
    for e in entries:
        own = [f for f in e['facts'] if f['id'].startswith('authored:applied:deadd_documented_processing@')]
        if not own:
            continue
        positive += 1
        facts = {f['id']: f for f in e['facts']}
        for operation in own:
            manual = operation['id'].split('@')[1]
            typed = facts['authored:applied:native_typed_operands@'+manual]
            assert typed['status'] == 'source_verified' and typed['verification'] == 'source_checked'
            assert typed['conditions'][0] == dict(context='project_mode', equals='native_ladder_instruction')
            pins = typed['value']['operands']
            assert [p['name'] for p in pins] == ['S1', 'S2', 'D']
            assert [p['direction'] for p in pins] == ['input', 'input', 'output']
            for p in pins:
                assert p['type'] == 'REAL' and p['word_device_layout'] == dict(device_unit_bits=16, word_count=2, value_bits=32)
                assert p['range']['device_prefixes'] == (['D'] if e['target_model'] in {'FX2N','FX2NC','FX3S'} else ['D','R'])
                assert p['range']['address_forms'] == (['buffer_memory'] if e['target_model'] in {'FX3U','FX3UC'} else [])
            for p in pins[:2]:
                assert p['constant_coercion'] == dict(source_radices=['K','H'], destination_type='REAL', conversion='integer_value_to_floating_point_at_execution')
                assert p['range']['literal_prefixes'] == (['K','H'] if manual != 'fx3_programming_r' else ['K','H','E'])
            assert pins[2]['range']['literal_prefixes'] == [] and 'constant_coercion' not in pins[2]
            v = operation['value']
            assert v['integer_constants_bit_reinterpreted_as_REAL'] is False and v['same_device_allowed'] is True
            assert v['full_source_relation_complete'] is False and v['Core_execution_verified'] is False and v['Native_execution_verified'] is False
            if manual == 'fx3_programming_r':
                minimum = {'FX3S':'1.00','FX3G':'1.10','FX3GC':'1.40','FX3U':'2.20','FX3UC':'1.00'}[e['target_model']]
                assert typed['conditions'][1] == dict(context='cpu_version', minimum=minimum)
                assert 'flag_source_conflict' not in v
            family = facts['authored:applied:variant_family@'+manual]['value']
            assert family['fnc_number'] == 120 and family['base_opcode'] == 'EADD'
            assert family['variant_bindings'] == {'DEADD':dict(data_bits=32,double=True,pulse=False),'DEADDP':dict(data_bits=32,double=True,pulse=True)}
    assert positive == 14


def test_DEADD_own_negative_eight_and_unresolved_legacy_flag_mapping():
    entries = json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    assert len(entries) == 22 and not any(e['target_model'] == 'FX1NC' for e in entries)
    negative = []
    for e in entries:
        for f in e['facts']:
            if f['id'].startswith('authored:applied:cpu_availability:header@') and f['value'].get('available') is False:
                assert f['status'] == 'source_verified' and f['verification'] == 'source_checked'
                assert f['conditions'] == [dict(context='project_mode', equals='native_ladder_instruction')]
                negative.append(e['target_model'])
            if f['id'].startswith('authored:applied:deadd_documented_processing@jy'):
                conflict = f['value']['flag_source_conflict']
                assert conflict['header_box'] == dict(zero='M8020',borrow='M8021',carry='M8022')
                assert conflict['body_point_e'] == dict(zero='M8020',carry='M8021',borrow='M8022')
                assert conflict['mapping_resolved'] is False
    assert set(negative) == {'FX0','FX0S','FX0N','FX1','FXU','FX2C','FX1N','FX1S'} and len(negative) == 8


def test_DEADD_source_guard_rejects_foreign_page_and_nonmatching_literal_pair(monkeypatch):
    record = dict(opcode='EADD', manual_id='fx3_programming_r', revision='R', pdf_pages=[507],
                  text='DEADD DEADDP floating point')
    out = {}
    producer._repair_fx_deadd_source(None, [dict(record, pdf_pages=[508])], out)
    assert not out
    monkeypatch.setattr(producer, '_source_table_groups', lambda *_: [])
    monkeypatch.setattr(producer, '_parameter_source_pages', lambda *_: [])
    monkeypatch.setattr(producer, '_programming_manual_variants', lambda *_: [dict(opcode='DADD',width_bits=32,execution_form='continuous')])
    producer._repair_fx_deadd_source(None, [record], out)
    assert not out


def test_DEADD_CPU520_integer_source_bytes_match_six_vendor_bodies_and_decode():
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    witness = json.loads((HERE/'native-integer-forms/probes.json').read_text(encoding='utf-8'))
    assert witness['cpu_code'] == 520 and len(witness['cases']) == 6
    for case in witness['cases']:
        op, *args = case['text'].split()
        assert op in {'DEADD','DEADDP'}
        assert case['encode_result'] == case['decode_result'] == '0x00000000' and case['native_consumed_all'] is True
        raw = encode_instruction(op, args, profile='fx3u', plc_model='FX3U', text_encoding='cp936')
        assert raw[2] == 13
        # Independent vendor tokens are integer E9/EB, never EC/ED REAL bits.
        assert [t['raw'][2:4] for t in case['tokens'][1:3]] in [['e9','eb'],['eb','e9']]
        raw += encode_instruction('END', [], profile='fx3u', plc_model='FX3U', text_encoding='cp936')
        assert raw == bytes.fromhex(case['body_hex']), case['text']
        listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile='fx3u', text_encoding='cp936')
        assert not listing.gaps and listing.reconstruct() == raw
        assert [(r.mnemonic,list(r.args)) for r in listing.instructions] == [(r['op'],r['args']) for r in case['native_records']]


def test_DEADD_H32_observation_is_slot_and_CPU_specific_and_does_not_open_other_opcodes():
    import pytest
    from gxw.models import GXWFormatError
    from gxw.token_encoder import encode_instruction
    for op in ('DEADD','DEADDP'):
        for profile,cpu in [('fx3u','FX3UC'),('fx3g','FX3G'),('fx3u',None)]:
            with pytest.raises(GXWFormatError):
                encode_instruction(op, ['K1','H2','D100'], profile=profile, plc_model=cpu, text_encoding='cp936')
        for args in (['K1','K2','H2'], ['K-2147483649','H2','D100'], ['K1','H100000000','D100']):
            with pytest.raises(GXWFormatError):
                encode_instruction(op, args, profile='fx3u', plc_model='FX3U', text_encoding='cp936')
    with pytest.raises(GXWFormatError):
        encode_instruction('DESUB', ['H2','K1','D100'], profile='fx3u', plc_model='FX3U', text_encoding='cp936')
