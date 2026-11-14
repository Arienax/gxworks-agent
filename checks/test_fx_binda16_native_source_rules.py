"""R679-681 Native BINDA/BINDAP 16-bit source contract boundaries."""
import json
import sys
from pathlib import Path

import pytest


BASE = Path(r'D:\gxworks-agent-private\tmp\rag-gap-implementation-20261029\fx-binda16-native-call-repair')
sys.path[:0] = [r'D:\gxworks-agent-integration\src', r'D:\gxworks-agent-integration']
SOURCE = json.loads((BASE / 'source-facts.json').read_text(encoding='utf-8'))
ROWS = {(entry['target_model'], entry['opcode']): entry for entry in SOURCE['entries']}
MODELS = ('FX3U', 'FX3UC')
OPCODES = ('BINDA', 'BINDAP')
NATIVE = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
VERSION = {'context': 'cpu_version', 'minimum': '2.20'}


@pytest.mark.parametrize('model,opcode', [(m, o) for m in MODELS for o in OPCODES])
def test_own_16bit_native_operands_and_conditional_tail(model, opcode):
    entry = ROWS[(model, opcode)]
    facts = {fact['id']: fact for fact in entry['facts']}
    suffix = '@fx3_programming_r'
    typed = facts['authored:applied:native_typed_operands' + suffix]
    assert typed['status'] == 'source_verified'
    assert typed['verification'] == 'source_checked'
    assert typed['conditions'] == [NATIVE, VERSION]
    assert typed['value']['operand_count'] == 2
    source, destination = typed['value']['operands']
    assert source['name'] == 'S' and source['type'] == 'ANY16'
    assert source['direction'] == 'input' and source['encoding'] == 'binary' and source['signed'] is True
    assert source['word_device_layout'] == {
        'device_unit_bits': 16, 'word_count': 1, 'value_bits': 16,
        'signed': True, 'low_word_offset': 0}
    assert source['range'] == {
        'device_prefixes': ['T', 'C', 'D', 'R', 'V', 'Z'],
        'digit_group_device_prefixes': ['X', 'Y', 'M', 'S'],
        'digit_groups_allowed': [1, 2, 3, 4], 'digit_groups_max': 4,
        'basis': 'own_R679_applicable_devices_plus_R161_162_digit_groups',
        'literal_prefixes': ['K', 'H'], 'integer_literal_widths': {'K': 16, 'H': 16},
        'address_forms': ['buffer_memory'], 'index_modification': True}
    assert destination['name'] == 'D' and destination['type'] == 'STRING'
    assert destination['direction'] == 'output'
    assert destination['passing'] == 'device_reference' and destination['addressing'] == 'start_device'
    assert destination['word_device_layout'] == {
        'kind': 'fixed_character_sequence', 'device_unit_bits': 16,
        'word_count': 3, 'element_bits': 8, 'element_count': 6,
        'single_scalar': False, 'byte_order': 'low_byte_then_high_byte'}
    assert destination['conditional_tail_write'] == {
        'selector': 'M8091', 'word_offset': 3,
        'OFF': {'word_count': 1, 'value': 0}, 'ON': 'unchanged',
        'current_selector_value_verified': False}
    assert destination['range'] == {
        'device_prefixes': ['T', 'C', 'D', 'R'], 'literal_prefixes': [],
        'address_forms': [], 'index_modification': True}
    source_refs = {(ref.get('manual_id'), tuple(ref.get('pdf_pages', [])))
                   for ref in typed.get('sources', [])}
    assert ('fx3_programming_r', (679, 680, 681)) in source_refs
    assert ('fx3_programming_r', (161,)) in source_refs
    assert ('fx3_programming_r', (162,)) in source_refs
    assert all(dep in facts for fact in entry['facts'] for dep in fact.get('depends_on', []))


@pytest.mark.parametrize('model,opcode', [(m, o) for m in MODELS for o in OPCODES])
def test_form_range_steps_error_and_m8091_behavior(model, opcode):
    facts = {fact['id']: fact for fact in ROWS[(model, opcode)]['facts']}
    suffix = '@fx3_programming_r'
    assert facts['authored:applied:native_order' + suffix]['value']['symbols'] == ['S', 'D']
    assert facts['authored:applied:native_arity' + suffix]['value']['minimum'] == 2
    assert facts['authored:applied:native_arity' + suffix]['value']['maximum'] == 2
    assert facts['authored:applied:program_steps' + suffix]['value']['steps'] == 5
    form = facts['authored:applied:execution_form' + suffix]
    assert form['value']['width_bits'] == 16
    assert form['value']['form'] == ('pulse' if opcode == 'BINDAP' else 'continuous')
    gate = facts['authored:applied:dbinda_execution_gate' + suffix]
    assert gate['value']['condition'] == ('rising_edge' if opcode == 'BINDAP' else 'execution_condition_ON')
    operation = facts['authored:applied:operation:73' + suffix]['value']
    assert operation['source_value_range'] == [-32768, 32767]
    assert operation['source_value_bits'] == 16 and operation['source_word_count'] == 1
    assert operation['destination_data_word_count'] == 3 and operation['destination_character_count'] == 6
    assert operation['positive_sign_ASCII_code'] == 32 and operation['negative_sign_ASCII_code'] == 45
    assert operation['leading_zero_suppression_ASCII_code'] == 32
    assert operation['conditional_tail'] == {
        'selector': 'M8091', 'destination_word_offset': 3,
        'OFF': {'write_value': 0, 'meaning': '0000H_NULL'},
        'ON': {'write': False, 'meaning': 'destination_word_unchanged'}}
    assert operation['M8091_changes_destination_data_word_count'] is False
    assert operation['occupied_destination_points'] == {'M8091_ON': 3, 'M8091_OFF': 4}
    assert operation['published_example']['destination_data_words'] == [0x312d, 0x3332, 0x3534]
    errors = facts['authored:applied:dbinda_documented_errors' + suffix]['value']
    assert (errors['error_flag'], errors['error_code_device'], errors['error_code']) == ('M8067', 'D8067', 6706)
    assert errors['occupied_points_by_M8091'] == {'ON': 3, 'OFF': 4}
    for fact in ROWS[(model, opcode)]['facts']:
        assert fact['status'] in {'source_verified', 'declared_unverified'}
    assert all(operation[key] is False for key in (
        'current_source_data_verified', 'current_selector_value_verified',
        'command_FALSE_output_behavior_established', 'indexed_effective_address_verified',
        'Core_execution_verified', 'Native_execution_verified', 'full_source_relation_complete'))


@pytest.mark.parametrize('model,opcode', [(m, o) for m in MODELS for o in OPCODES])
def test_header_threshold_and_statement_is_not_a_bool_return(model, opcode):
    facts = {fact['id']: fact for fact in ROWS[(model, opcode)]['facts']}
    suffix = '@fx3_programming_r'
    header = facts['authored:applied:source_cpu_header' + suffix]
    assert header['value']['model'] == model
    assert header['value']['minimum_version'] == '2.20'
    assert header['conditions'] == [NATIVE]
    availability = facts['authored:applied:cpu_availability:header' + suffix]
    assert availability['conditions'] == [NATIVE, VERSION]
    version = facts['authored:applied:cpu_version:header_minimum' + suffix]
    assert version['dimension'] == 'constraints.cpu_version'
    assert version['scope'] == {'models': [model], 'forms': [opcode], 'languages': ['ladder']}
    assert version['value']['models'] == [model]
    assert version['value']['minimum_series_version'] == 'Ver.2.20'
    assert version['depends_on'] == [header['id']]
    assert version['sources'][0]['manual_id'] == 'fx3_programming_r'
    assert version['sources'][0]['pdf_pages'] == [679]
    owner_witness = next(source for source in version['sources']
                         if source.get('representation') == 'instruction_header_cpu_version_badges')
    assert owner_witness['opcode'] == opcode
    assert owner_witness['target_model'] == model
    assert owner_witness['source_ref'] == 'fx3_programming_r:R:pages=679'
    assert version['id'] in availability['depends_on']
    catalogue = facts['source:instruction_catalogue:fx3_programming_r']
    assert catalogue['value']['model_states'] == {model: 'conditional'}
    assert 'available' not in catalogue['value']
    assert catalogue['depends_on'].count(version['id']) == 1
    assert catalogue['conditions'].count(VERSION) == 1
    m703 = [source for source in availability.get('sources', [])
            if source.get('manual_id') == 'fxcpu_basic_applied_m' and source.get('pdf_pages') == [703]]
    assert bool(m703) is (model == 'FX3UC')
    m_signature = facts['authored:applied:st_signature@fxcpu_basic_applied_m']['value']
    assert m_signature['call_kind'] == 'statement'
    assert m_signature['returns'] == {'role': 'not_assigned_in_documented_statement'}
    assert not any('statement_normalizer' in fact.get('value', {})
                   for fact in ROWS[(model, opcode)]['facts'])


@pytest.mark.parametrize('model,opcode', [(m, o) for m in MODELS for o in OPCODES])
def test_real_conditional_tail_consumer_uses_documented_m8091_off_word(model, opcode):
    from plc.device_identity import _conditional_string_tail_region

    facts = {fact['id']: fact for fact in ROWS[(model, opcode)]['facts']}
    destination = facts['authored:applied:native_typed_operands@fx3_programming_r']['value']['operands'][1]
    assert _conditional_string_tail_region(destination, 1, 'D200', model) == {
        'operand_index': 1, 'head': 'D200', 'access': 'write', 'unit': 'words',
        'offset': 3, 'count': 1, 'resolved_count': 1,
        'condition': {'context': 'device_state', 'device': 'M8091', 'equals': False},
        'value_when_condition': 0, 'addresses': ['D203'],
        'dependencies': ['M8091'], 'requires': ['device_value:M8091']}


def test_original_queue_ids_and_non_fx_members_remain_in_package_scope():
    queue = json.loads((Path(r'D:\gxworks-agent-private\tmp\rag-gap-implementation-20261029') /
                        'execution-queue.json').read_text(encoding='utf-8'))['instructions']
    selectors = json.loads((BASE / 'owner-selectors.json').read_text(encoding='utf-8'))
    assert selectors['original_queue_members']['BINDAP']['unit_ids'] == queue['BINDAP']['unit_ids']
    assert selectors['original_queue_members']['BINDAP']['models'] == queue['BINDAP']['models']
    assert selectors['original_queue_members']['BINDAP']['unit_ids'] == [f'I-{i:05d}' for i in range(571, 581)]
    assert selectors['original_queue_members']['BINDA']['unit_ids'] == queue['BINDA']['unit_ids']
    assert selectors['original_queue_members']['BINDA']['models'] == queue['BINDA']['models']
    assert len(selectors['original_queue_members']['BINDAP']['models']) == 55
    assert len([m for m in queue['BINDAP']['models'] if m.startswith(('Q', 'L'))]) == 53
    assert {(row['target_model'], row['opcode']) for row in selectors['selected_owners']} == {
        (model, opcode) for model in MODELS for opcode in OPCODES}
    assert all(row['original_ids'] == queue[row['opcode']]['unit_ids']
               for row in selectors['selected_owners'])


def test_instruction_class_dependents_refresh_from_current_variant_roots():
    assert SOURCE['entries']
    assert all('authored:applied:variant_family@fxcpu_basic_applied_m:variant:2' not in
               {fact['id'] for fact in entry['facts']} for entry in SOURCE['entries'])
    for entry in SOURCE['entries']:
        facts = {fact['id']: fact for fact in entry['facts']}
        class_facts = [fact for fact in entry['facts'] if fact['id'].startswith('instruction_class_review:')]
        assert len(class_facts) == 2
        for manual in ('fx3_programming_r', 'fxcpu_basic_applied_m'):
            source_id = 'authored:applied:variant_family@' + manual
            derived = next(fact for fact in class_facts if fact['id'].endswith(':' + manual)
                           and fact['depends_on'] == [source_id])
            assert source_id in facts
            assert derived['status'] == facts[source_id]['status']
    assert json.loads((BASE / 'source-verification.json').read_text(encoding='utf-8'))[
        'refreshed_derived_namespaces'] == ['instruction_class_review:']
