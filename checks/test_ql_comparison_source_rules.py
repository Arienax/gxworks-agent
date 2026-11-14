"""Comparison families retain explicit source declarations and domain bounds."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools.fact_ql_behavior_review import Section, _comparison_domain_details, _refine_clock_comparison_selector, _comparison_parameter_storage


def glyph_section(family, table):
    row = dict(id='synthetic', pdf_page=1, pdf_page_end=1, instruction_opcode='', chunk_type='instruction',
        text='[PAGE 1 PROSE]\nSynthetic comparison\n' + family + '[GLYPH-F06F]\n' + table + '\nOperation error')
    return Section('sh080809engx', 'X', 'SH080809ENG', 'ladder', 'Synthetic > comparison', [row])


class QLComparisonSourceRules(unittest.TestCase):
    def test_native_glyph_family_uses_only_operators_present_in_result_table(self):
        for family, suffix in [('LD$', '$'), ('ANDDT', 'DT'), ('ORTM', 'TM')]:
            with self.subTest(family=family):
                section = glyph_section(family, suffix + '< synthetic-left synthetic-right\n' + suffix + '= synthetic-equal')
                self.assertEqual(set(section.symbols), {family + '<', family + '='})
                self.assertNotIn(family + '>=', section.symbols)

    def test_domain_is_not_inferred_from_opcode_without_complete_source(self):
        fake = SimpleNamespace(manual='sh080809engx', text='synthetic partial comparison', bind=lambda witness: [1])
        for symbol in ['AND$<', 'ANDDT<', 'ANDTM<']:
            self.assertEqual(_comparison_domain_details(fake, symbol), (None, []))
        fake.manual = 'unregistered'
        self.assertEqual(_comparison_domain_details(fake, 'AND$<'), (None, []))

    def test_selector_source_conflict_is_preserved_when_effective_direction_is_bound(self):
        fake = SimpleNamespace(manual='sh080783engp', text='The comparison target can be selected by specifying n',
            bind=lambda witness: [1])
        pin = dict(name='n', type='ANY16', direction='output', role='count')
        row = dict(operand=pin, description='Value to indicate the comparison target', witness='synthetic-argument')
        contract = dict(allowed=['ANDDT<'], rows=[row])
        _refine_clock_comparison_selector(fake, contract)
        self.assertEqual(pin['direction'], 'input')
        self.assertEqual(pin['role'], 'comparison_selector')
        self.assertEqual(pin['table_declared_direction'], 'output')
        self.assertEqual(row['clock_selector_binding']['table_declaration']['direction'], 'output')
        self.assertFalse(row['clock_selector_binding']['manufacturer_erratum_claimed'])

    def test_calendar_reference_is_three_words_but_selector_is_one(self):
        domain = dict(kind='selected_calendar_components', components=[
            dict(name=name, word_offset=index, range=bounds) for index, (name, bounds) in
            enumerate([('year', [1980, 2079]), ('month', [1, 12]), ('day', [1, 31])])])
        fake = SimpleNamespace(text='year (s1) and (s2). month (s1)+1 and (s2)+1. day (s1)+2 and (s2)+2.',
            bind=lambda witness: [1])
        specs = [dict(patterns=('synthetic-contract',), value=dict(operands=[
            dict(name=name, type='ANY16', direction='input', passing='device_reference',
                 role='comparison_value', representation='native_operand') for name in ['S1', 'S2', 'n']]))]
        with patch('tools.fact_ql_behavior_review._comparison_domain_details', return_value=(domain, ['synthetic-domain'])):
            result = _comparison_parameter_storage(fake, 'ANDDT<', specs)
            pins = result[0]['value']['operands']
            self.assertEqual([p['word_device_layout']['word_count'] for p in pins], [3, 3, 1])
            self.assertEqual(pins[0]['referent_fields'][2]['offset'], 2)
            self.assertFalse(pins[0]['word_device_layout']['single_scalar'])
            self.assertEqual(pins[2]['role'], 'comparison_selector')
            self.assertEqual(pins[2]['purpose'], 'comparison_selector')
            self.assertEqual(pins[0]['role'], 'comparison_value')
            self.assertNotIn('word_device_layout', specs[0]['value']['operands'][0])
            fake.text = fake.text.replace('day (s1)+2 and (s2)+2.', '')
            self.assertEqual(_comparison_parameter_storage(fake, 'ANDDT<', specs), specs)

    def test_string_layout_keeps_terminator_and_short_circuit_without_fixed_capacity(self):
        fake = SimpleNamespace(text='synthetic-byte-diagram b15 b8 b7 b0', bind=lambda witness: [1])
        specs = [dict(patterns=('synthetic-contract',), value=dict(operands=[
            dict(name='S1', type='STRING', direction='input', passing='device_reference')]))]
        with patch('tools.fact_ql_behavior_review._comparison_domain_details',
                   return_value=(dict(kind='ascii_lexicographic_until_null'), ['synthetic-domain'])):
            result = _comparison_parameter_storage(fake, 'AND$<', specs)[0]['value']['operands'][0]
            layout = result['string_layout']
            self.assertEqual(layout['extent'], 'through_first_terminator')
            self.assertEqual(layout['short_circuit_extent'], 'first_difference_or_both_terminators')
            self.assertEqual(layout['device_range_check'], 'as_compared')
            self.assertNotIn('within_device_range', layout)
            self.assertNotIn('declared_capacity', result)
            self.assertNotIn('word_count', layout)
            fake.text = 'synthetic-no-byte-layout'
            self.assertEqual(_comparison_parameter_storage(fake, 'AND$<', specs), specs)
            fake.manual = 'sh080783engp'
            fake.text = '42H(B) 41H(A) 43H(C) 44H(D)'
            self.assertEqual(_comparison_parameter_storage(fake, 'AND$<', specs)[0]['value']['operands'][0]
                ['string_layout']['unit_bits'], 8)
            fake.text = '42H(B) 41H(A)'
            self.assertEqual(_comparison_parameter_storage(fake, 'AND$<', specs), specs)

    def test_selector_direction_requires_own_family_and_bound_prose(self):
        for symbol, source in [('AND<', True), ('ANDDT<', False)]:
            fake = SimpleNamespace(manual='sh080783engp', text='The comparison target can be selected by specifying n',
                bind=lambda witness: [1] if source else [])
            pin = dict(name='n', type='ANY16', direction='output', role='count')
            _refine_clock_comparison_selector(fake, dict(allowed=[symbol], rows=[dict(operand=pin,
                description='Value to indicate the comparison target')]))
            self.assertEqual(pin['direction'], 'output')


if __name__ == '__main__':
    unittest.main()
