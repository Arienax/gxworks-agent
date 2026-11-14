"""FX DDABIN/P own fixed ASCII record, distinct from Q/L sign rules."""
import copy
import json
from pathlib import Path

from tools import fact_applied_review as producer

HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-ddabin-native-contract-repair')


def test_actual_FX_DDABIN_record_layout_and_byte_positions():
    entries = json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    corrections = json.loads((HERE/'source-corrections.json').read_text(encoding='utf-8'))['changes']
    assert len(entries) == 4
    expected = [(0, 1, 1000000000), (1, 0, 100000000), (1, 1, 10000000),
                (2, 0, 1000000), (2, 1, 100000), (3, 0, 10000),
                (3, 1, 1000), (4, 0, 100), (4, 1, 10), (5, 0, 1)]
    for entry in entries:
        facts = {f['id'].split('@')[0]: f for f in entry['facts'] if f['id'].endswith('@fx3_programming_r')}
        typed = facts['authored:applied:native_typed_operands']
        assert typed['status'] == 'source_verified' and typed['verification'] == 'source_checked'
        assert typed['conditions'] == [{'context': 'project_mode', 'equals': 'native_ladder_instruction'},
                                       {'context': 'cpu_version', 'minimum': '2.20'}]
        source, destination = typed['value']['operands']
        assert [source['direction'], destination['direction']] == ['input', 'output']
        assert source['type'] == 'STRING' and source['passing'] == 'device_reference'
        assert source['word_device_layout'] == dict(kind='fixed_character_sequence', device_unit_bits=16,
            word_count=6, element_bits=8, element_count=11, single_scalar=False, ignored_byte_offsets=[11])
        assert destination['type'] == 'ANY32' and destination['signed'] is True
        assert destination['word_device_layout']['word_count'] == 2
        assert source['range']['literal_prefixes'] == [] and source['range']['address_forms'] == []
        assert source['range']['device_prefixes'] == ['T', 'C', 'D', 'R']
        assert destination['range']['digit_groups_max'] == 8
        v = facts['authored:applied:ddabin_documented_conversion']['value']
        assert [(p['source_word_offset'], p['source_byte_offset'], p['multiplier']) for p in v['character_positions']] == expected
        assert v['sign_position'] == dict(source_word_offset=0, source_byte_offset=0)
        assert v['ignored_source_byte'] == dict(source_word_offset=5, source_byte_offset=1)
        assert v['other_sign_ASCII_codes_processed_as'] == 'operation_error'
        assert v['digit_zero_ASCII_codes'] == [0, 32] and v['zero_byte_is_terminator'] is False
        assert [v['minimum'], v['maximum']] == [-2147483648, 2147483647]
        assert v['published_example']['result'] == -1234543210
        assert v['published_example']['last_source_word_high_byte_ignored'] is True
        assert v['full_source_relation_complete'] is False and v['Core_execution_verified'] is False
        assert v['Native_execution_verified'] is False
        errors = facts['authored:applied:ddabin_documented_errors']['value']
        assert errors['error_flag'] == 'M8067' and errors['error_code_device'] == 'D8067' and errors['error_code'] == 6706
        assert errors['digit_positions'] == v['character_positions']
        assert errors['ignored_source_byte'] == v['ignored_source_byte']
        variant = facts['authored:applied:variant_family']['value']
        assert variant['fnc_number'] == 260 and variant['base_opcode'] == 'DABIN'
        assert variant['variant_bindings'][entry['opcode']]['pulse'] == entry['opcode'].endswith('P')
    old = [c for c in corrections if c['id'] == 'authored:applied:native_typed_operands@fx3_programming_r']
    assert len(old) == 4 and all(c['before']['value']['operands'][0]['direction'] == 'output' for c in old)


def test_FX_DDABIN_helper_needs_own_32bit_clause_and_pair(monkeypatch):
    terms = ('32-bit operation (DDABIN and DDABINP) -2,147,483,648 to +2,147,483,647 '
             'high-order byte ignored low-order byte 20H (space) 2DH (-) 00H (NULL) '
             'any value other than K6706 -1,234,543,210')
    record = dict(id=1, opcode='DABIN', manual_id='fx3_programming_r', manual_number='JY997D16601',
                  revision='R', pdf_pages=[676, 677, 678], text=terms)
    forms = [dict(opcode='DDABIN', width_bits=32, execution_form='continuous'),
             dict(opcode='DDABINP', width_bits=32, execution_form='pulse')]
    monkeypatch.setattr(producer, '_source_table_groups', lambda *_: [])
    monkeypatch.setattr(producer, '_parameter_source_pages', lambda *_: [])
    monkeypatch.setattr(producer, '_programming_manual_variants', lambda *_: forms)
    facts = {}; producer._repair_fx_ddabin_source(None, [record], facts)
    assert set(facts) == {(m, op) for m in ('FX3U', 'FX3UC') for op in ('DDABIN', 'DDABINP')}
    for bad in ({**record, 'pdf_pages': [676]}, {**record, 'manual_id': 'sh080809engx'},
                {**record, 'text': terms.replace('ignored', '')}):
        facts = {}; producer._repair_fx_ddabin_source(None, [bad], facts); assert not facts
    forms[0]['width_bits'] = 16
    facts = {}; producer._repair_fx_ddabin_source(None, [record], facts); assert not facts


def test_FX_DDABIN_default_projection_keeps_own_220_scope_and_partial_data():
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units, technical_knowledge_text
    entries = json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    def walk(x):
        if isinstance(x, dict):
            yield x
            for value in x.values(): yield from walk(value)
        elif isinstance(x, list):
            for value in x: yield from walk(value)
    for e in entries:
        for version in ('2.19', '2.20'):
            packet = project_packet(dict(kind='instruction', target=e['opcode'], scope=dict(models=[e['target_model']]),
                content_origin='authored_fact', value=e), plc_model=e['target_model'], language='ladder',
                project_mode='native_ladder_instruction', instance_context={'cpu_version': version}, aspects=['programming_semantics'])
            facts = packet['value']['facts']
            own = [f for f in facts if f['id'] == 'authored:applied:ddabin_documented_conversion@fx3_programming_r']
            if version == '2.19':
                assert not own
                assert any(f['dimension'] == 'constraints.cpu_availability' and f['value'].get('available') is False for f in facts)
                continue
            assert len(own) == 1
            record = dict(text=json.dumps(packet), fact_kind='instruction', fact_target=e['opcode'],
                          _knowledge_context=dict(model=e['target_model'], language='ladder',
                              project_mode='native_ladder_instruction', delivery_languages=['ladder']))
            nodes = list(walk(technical_context_units(technical_knowledge_text(record))))
            assert any(n.get('source_character_count') == 11 and n.get('source_word_count') == 6
                and n.get('destination_word_count') == 2 and n.get('zero_byte_is_terminator') is False
                and n.get('other_sign_ASCII_codes_processed_as') == 'operation_error'
                and n.get('full_source_relation_complete') is False for n in nodes)
