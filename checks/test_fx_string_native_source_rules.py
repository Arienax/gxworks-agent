"""Exact R-page source authoring for FX LEFT/RIGHT/LEN and their queued P forms."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, 'D:/gxworks-agent-integration/src')
from tools.fact_applied_review import reviewed_applied_behavior

PRIVATE = Path('D:/gxworks-agent-private')
PACKAGE = PRIVATE / 'tmp/rag-gap-implementation-20261029/fx-r-string-native-contract-repair'
DATABASE = PRIVATE / 'resources/knowledge/fx3u_knowledge.sqlite'
PDFS = PRIVATE / 'resources/knowledge/sources'
EXPECTED = {
    'LEFT': (['S', 'D', 'n'], ['STRING', 'STRING', 'ANY16'], 7, 205, 630),
    'RIGHT': (['S', 'D', 'n'], ['STRING', 'STRING', 'ANY16'], 7, 204, 628),
    'LEN': (['S', 'D'], ['STRING', 'ANY16'], 5, 203, 626),
}
HEADER_MODELS = ('FX3S', 'FX3G', 'FX3GC', 'FX3U', 'FX3UC')


def _fresh_r_facts():
    connection = sqlite3.connect(DATABASE.as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return reviewed_applied_behavior(
            connection, manuals=['fx3_programming_r'], families={'LEFT', 'RIGHT', 'LEN'},
            source_pdf_root=PDFS)
    finally:
        connection.close()


def test_R_native_string_source_exactly_binds_header_types_devices_order_and_steps():
    facts, _receipt = _fresh_r_facts()
    expected_owners = ({(model, opcode) for model in ('FX3U', 'FX3UC')
                        for base in EXPECTED for opcode in (base, base + 'P')}
                       | {(model, base) for model in ('FX3S', 'FX3G', 'FX3GC')
                          for base in EXPECTED})
    assert expected_owners <= set(facts)

    for model, opcode in sorted(expected_owners):
        base = opcode.removesuffix('P')
        rows = {row['id']: row for row in facts[model, opcode]}
        header = rows['authored:applied:source_cpu_header@fx3_programming_r']
        availability = rows['authored:applied:cpu_availability:header@fx3_programming_r']
        if model in ('FX3S', 'FX3G', 'FX3GC'):
            assert header['value'] == {
                'model': model, 'state': 'unsupported',
                'basis': 'reviewed_own_instruction_header_cpu_version_badges'}
            assert availability['value']['available'] is False
            assert all(fact['status'] == 'source_verified' and fact['verification'] == 'source_checked'
                       for fact in (header, availability))
            continue

        names, types, steps, fnc, page = EXPECTED[base]
        typed = rows['authored:applied:native_typed_operands@fx3_programming_r']
        order = rows['authored:applied:native_order@fx3_programming_r']
        arity = rows['authored:applied:native_arity@fx3_programming_r']
        execution = rows['authored:applied:execution_form@fx3_programming_r']
        program_steps = rows['authored:applied:program_steps@fx3_programming_r']

        assert all(fact['status'] == 'source_verified' and fact['verification'] == 'source_checked'
                   for fact in (typed, order, arity, execution, program_steps, header, availability))
        assert [pin['name'] for pin in typed['value']['operands']] == names
        assert [pin['type'] for pin in typed['value']['operands']] == types
        for pin in typed['value']['operands']:
            if pin['type'] == 'STRING':
                assert pin['passing'] == 'device_reference'
                assert pin['addressing'] == 'start_device'
            else:
                assert 'passing' not in pin and 'addressing' not in pin
        assert order['value']['symbols'] == names
        assert (arity['value']['minimum'], arity['value']['maximum']) == (len(names), len(names))
        assert execution['value'] == {
            'form': 'pulse' if opcode.endswith('P') else 'continuous', 'width_bits': 16,
            'basis': 'own_R_instruction_format_table'}
        assert program_steps['value'] == {
            'steps': steps, 'basis': 'own_R_16_bit_instruction_format_step_cell'}
        assert {'context': 'cpu_version', 'minimum': '2.20' if model == 'FX3U' else '1.00'} in typed['conditions']
        assert typed['scope']['languages'] == ['ladder']
        assert {'context': 'project_mode', 'equals': 'native_ladder_instruction'} in availability['conditions']
        assert any(source['pdf_pages'] == [page, page + 1] for source in typed['sources'])
        assert header['value']['model'] == model and header['value']['state'] == 'conditional'
        assert header['value']['minimum_version'] == ('2.20' if model == 'FX3U' else '1.00')
        assert availability['value']['available'] is True

        pins = {pin['name']: pin for pin in typed['value']['operands']}
        assert (pins['S']['direction'], pins['S']['range']['device_prefixes']) == (
            'input', ['T', 'C', 'D', 'R'])
        assert pins['S']['range']['digit_group_device_prefixes'] == ['X', 'Y', 'M', 'S']
        assert pins['S']['range']['address_forms'] == ['buffer_memory']
        if base == 'LEN':
            assert pins['D']['direction'] == 'output' and pins['D']['type'] == 'ANY16'
        else:
            assert pins['D']['direction'] == 'output' and pins['D']['type'] == 'STRING'
            assert pins['D']['range']['digit_group_device_prefixes'] == ['Y', 'M', 'S']
            assert pins['n']['direction'] == 'input' and pins['n']['range']['integer_literal_widths'] == {
                'K': 16, 'H': 16}
            assert pins['n']['range']['device_prefixes'] == ['D', 'R']

def test_package_preserves_exact_queue_ids_and_all_original_models():
    selectors = json.loads((PACKAGE / 'owner-selectors.json').read_text(encoding='utf-8'))
    facts = json.loads((PACKAGE / 'source-facts.json').read_text(encoding='utf-8'))['entries']
    corrections = json.loads((PACKAGE / 'source-corrections.json').read_text(encoding='utf-8'))['changes']
    units = json.loads((PACKAGE / 'original-units.json').read_text(encoding='utf-8'))['entries']
    dispositions = json.loads((PACKAGE / 'member-dispositions.json').read_text(encoding='utf-8'))['entries']
    assert len(selectors) == len(facts) == 21
    assert {(row['target_model'], row['opcode']) for row in selectors} == {
        (row['target_model'], row['opcode']) for row in facts}
    assert {row['opcode'] for row in units} == {
        'LEFT', 'LEFTP', 'RIGHT', 'RIGHTP', 'LEN', 'LENP'}
    assert len(units) == 6 and len(dispositions) == sum(len(row['models']) for row in units)
    for row in selectors:
        unit = next(item for item in units if item['opcode'] == row['opcode'])
        assert row['original_unit_ids'] == unit['original_unit_ids']
        assert row['target_model'] in unit['models']
    for row in units:
        assert all(identity.startswith('I-') for identity in row['original_unit_ids'])

    by_owner = {(row['target_model'], row['opcode']): row for row in facts}
    supported = {(model, opcode) for model in ('FX3U', 'FX3UC')
                 for opcode in ('LEFT', 'LEFTP', 'RIGHT', 'RIGHTP', 'LEN', 'LENP')}
    catalog_changes = {(row['target_model'], row['opcode']) for row in corrections
                       if row['id'] == 'source:instruction_catalogue:fx3_programming_r'}
    assert catalog_changes == supported
    for model, opcode in supported:
        own = {row['id']: row for row in by_owner[model, opcode]['facts']}
        header = own['authored:applied:source_cpu_header@fx3_programming_r']
        catalogue = own['source:instruction_catalogue:fx3_programming_r']
        version = '2.20' if model == 'FX3U' else '1.00'
        assert {'context': 'cpu_version', 'minimum': version} in catalogue['conditions']
        assert 'authored:applied:source_cpu_header@fx3_programming_r' in catalogue['depends_on']
        assert any(source['pdf_pages'] == [75, 83] for source in catalogue['sources'])
        assert any(source.get('representation') == 'instruction_header_cpu_version_badges'
                   for source in catalogue['sources'])
        typed = own['authored:applied:native_typed_operands@fx3_programming_r']
        for pin in typed['value']['operands']:
            if pin['type'] == 'STRING':
                assert (pin.get('passing'), pin.get('addressing')) == (
                    'device_reference', 'start_device')

