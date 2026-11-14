"""DDEG/P source is the own radians-to-degrees page, not inverse trigonometry."""
import json
from pathlib import Path

from tools import fact_applied_review as producer

HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-ddeg-native-contract-repair')


def test_DDEG_native_CPU520_six_independent_bodies_and_local_roundtrip():
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    evidence = json.loads((HERE/'native-forms/probes.json').read_text(encoding='utf-8'))
    assert evidence['cpu_code'] == 520 and len(evidence['cases']) == 6
    for case in evidence['cases']:
        op, source, destination = case['text'].split()
        assert op in {'DDEG', 'DDEGP'} and source in {'D100', 'E2', 'E5-1'}
        assert case['encode_result'] == case['decode_result'] == '0x00000000'
        assert case['native_consumed_all'] is True
        raw = encode_instruction(op, [source, destination], profile='fx3u',
                                 plc_model='FX3U', text_encoding='cp936')
        raw += encode_instruction('END', [], profile='fx3u', plc_model='FX3U', text_encoding='cp936')
        assert raw == bytes.fromhex(case['body_hex']), case['text']
        listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)),
                                       profile='fx3u', text_encoding='cp936')
        assert not listing.gaps and listing.reconstruct() == raw
        # The vendor normalizes E5-1 to E5.0-1; retain its actual spelling.
        records = list(listing.instructions)
        native = case['native_records']
        assert records[0].mnemonic == native[0]['op'] == op
        assert records[0].args[1] == native[0]['args'][1] == destination
        assert records[0].args[0] == native[0]['args'][0]
        assert native[0]['args'][0] == ('E5.0-1' if source == 'E5-1' else source)
        assert records[1].mnemonic == native[1]['op'] == 'END'


def test_DDEG_codec_observation_does_not_open_other_profiles_or_destination_literals():
    import pytest
    from gxw.models import GXWFormatError
    from gxw.token_encoder import encode_instruction
    for op in ('DDEG', 'DDEGP'):
        for profile, cpu in [('fx3g', 'FX3G'), ('fx1s', 'FX1S'), ('fx3u', 'FX3UC'), ('fx3u', None)]:
            with pytest.raises(GXWFormatError, match='only observed for FX3U/fx3u'):
                encode_instruction(op, ['D100', 'D200'], profile=profile, plc_model=cpu, text_encoding='cp936')
        with pytest.raises(GXWFormatError):
            encode_instruction(op, ['D100', 'E2'], profile='fx3u', plc_model='FX3U', text_encoding='cp936')


def test_actual_DDEG_source_has_REAL_pairs_own_versions_and_formula():
    entries = json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    assert len(entries) == 4
    for e in entries:
        facts = {f['id'].split('@')[0]: f for f in e['facts'] if f['id'].endswith('@fx3_programming_r')}
        typed = facts['authored:applied:native_typed_operands']
        assert typed['status'] == 'source_verified' and typed['verification'] == 'source_checked'
        assert typed['conditions'] == [{'context': 'project_mode', 'equals': 'native_ladder_instruction'},
            {'context': 'cpu_version', 'minimum': '2.20' if e['target_model'] == 'FX3U' else '1.00'}]
        source, destination = typed['value']['operands']
        assert [source['direction'], destination['direction']] == ['input', 'output']
        for p in (source, destination):
            assert p['type'] == 'REAL' and p['word_device_layout']['word_count'] == 2
            assert p['range']['device_prefixes'] == ['D', 'R']
            assert p['range']['address_forms'] == ['buffer_memory'] and p['range']['index_modification'] is True
            assert 'minimum' not in p['range'] and 'maximum' not in p['range']
        assert source['range']['literal_prefixes'] == ['E'] and destination['range']['literal_prefixes'] == []
        v = facts['authored:applied:ddeg_documented_processing']['value']
        assert v['input_angle_unit'] == 'radian' and v['output_angle_unit'] == 'degree'
        assert v['documented_conversion_factor'] == dict(numerator=180, denominator='pi')
        assert v['published_example'] == dict(input=1.435792, output=82.26482)
        assert v['inverse_trigonometric_input_domain_claimed'] is False
        assert v['full_source_relation_complete'] is False and v['Core_execution_verified'] is False
        assert v['Native_execution_verified'] is False
        variant = facts['authored:applied:variant_family']['value']
        assert variant['fnc_number'] == 137 and variant['base_opcode'] == 'DEG'
        assert set(variant['variant_bindings']) == {'DDEG', 'DDEGP'}
        assert variant['variant_bindings'][e['opcode']]['pulse'] == e['opcode'].endswith('P')


def test_DDEG_source_guard_requires_own_page_and_exact_32bit_pair(monkeypatch):
    r = dict(id=1, opcode='DEG', manual_id='fx3_programming_r', manual_number='JY997D16601',
        revision='R', pdf_pages=[532], text='DDEG DDEGP Real number (binary) radians degrees 1.435792 82.26482')
    forms = [dict(opcode='DDEG', width_bits=32, execution_form='continuous'),
             dict(opcode='DDEGP', width_bits=32, execution_form='pulse')]
    monkeypatch.setattr(producer, '_source_table_groups', lambda *_: [])
    monkeypatch.setattr(producer, '_parameter_source_pages', lambda *_: [])
    monkeypatch.setattr(producer, '_programming_manual_variants', lambda *_: forms)
    facts = {}; producer._repair_fx_ddeg_source(None, [r], facts)
    assert set(facts) == {(m, op) for m in ('FX3U', 'FX3UC') for op in ('DDEG', 'DDEGP')}
    for bad in ({**r, 'pdf_pages': [530]}, {**r, 'manual_id': 'fxcpu_basic_applied_m'},
                {**r, 'text': r['text'].replace('82.26482', '')}):
        facts = {}; producer._repair_fx_ddeg_source(None, [bad], facts); assert not facts
    forms[0]['width_bits'] = 16
    facts = {}; producer._repair_fx_ddeg_source(None, [r], facts); assert not facts


def test_DDEG_default_keeps_formula_and_own_version_guards():
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units, technical_knowledge_text
    def walk(x):
        if isinstance(x, dict):
            yield x
            for value in x.values(): yield from walk(value)
        elif isinstance(x, list):
            for value in x: yield from walk(value)
    for e in json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']:
        minimum, below = ('2.20', '2.19') if e['target_model'] == 'FX3U' else ('1.00', '0.99')
        for version in (minimum, below):
            packet = project_packet(dict(kind='instruction', target=e['opcode'], scope=dict(models=[e['target_model']]),
                content_origin='authored_fact', value=e), plc_model=e['target_model'], language='ladder',
                project_mode='native_ladder_instruction', instance_context={'cpu_version': version}, aspects=['programming_semantics'])
            facts = packet['value']['facts']
            own = [f for f in facts if f['id'] == 'authored:applied:ddeg_documented_processing@fx3_programming_r']
            if version == below:
                assert not own
                assert any(f['dimension'] == 'constraints.cpu_availability' and f['value'].get('available') is False for f in facts)
                continue
            assert len(own) == 1
            record = dict(text=json.dumps(packet), fact_kind='instruction', fact_target=e['opcode'],
                _knowledge_context=dict(model=e['target_model'], language='ladder', project_mode='native_ladder_instruction', delivery_languages=['ladder']))
            values = list(walk(technical_context_units(technical_knowledge_text(record))))
            assert any(v.get('output_angle_unit') == 'degree' and v.get('documented_conversion_factor') == dict(numerator=180, denominator='pi')
                and v.get('inverse_trigonometric_input_domain_claimed') is False
                and v.get('full_source_relation_complete') is False for v in values)
