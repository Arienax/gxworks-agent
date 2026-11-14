"""Own processing clauses define bounded documentation, not a numeric executor."""
import unittest

from tools import fact_structured_behavior_review as review


class FxBitArrayBehaviorTests(unittest.TestCase):
    def build(self, opcode, enabled, *, common_enable=True):
        record = next(r for r in review._conversion_records() if r['op'] == opcode)
        own = {'manual_id': review.FUNCTION_MANUAL, 'revision': 'K', 'pdf_pages': list(record['pages'])}
        common = {'manual_id': review.FUNCTION_MANUAL, 'revision': 'K', 'pdf_pages': [36, 37]}
        rows = review._form_facts(record, opcode + ('_E' if enabled else ''), enabled,
            'FX3U', {'text': ' '.join(record['witness'])}, [own], None, 'source_verified',
            execution_reference={'manual_id': 'structured_fundamentals_o', 'revision': 'O', 'pdf_pages': [29]},
            enable_reference=common if common_enable else None)
        return record, rows + review._structured_ladder_facts(rows)

    def test_all_forms_preserve_only_documented_transfer_and_padding(self):
        for opcode in review.BIT_ARRAY_COUNT_CONVERSIONS:
            for enabled in (False, True):
                with self.subTest(opcode=opcode, enabled=enabled):
                    record, rows = self.build(opcode, enabled)
                    identities = {f['id'] for f in rows}
                    effects = [f for f in rows if f['dimension'] == 'effects.result_rule']
                    self.assertEqual(len(effects), 2)
                    for fact in effects:
                        value = fact['value']; relation = value['bit_array_relation']
                        self.assertEqual(relation['kind'], record['rule']['rule'])
                        self.assertEqual(relation['source_parameter'], record['inputs'][0][0])
                        self.assertEqual(relation['destination_parameter'], record['outputs'][0][0])
                        self.assertEqual(relation['count_parameter'], 'n')
                        self.assertEqual(relation['element_bit_order'], 'not_stated')
                        self.assertEqual(relation['logical_unit'], 'bit_elements')
                        self.assertNotIn('outside_destination', relation)
                        self.assertNotIn('behavior', value)
                        self.assertIs(value['full_source_relation_complete'], False)
                        self.assertEqual(value['unresolved_items'], [
                            'array_element_to_integer_bit_position_not_stated' if opcode.startswith('BITARR_TO_')
                            else 'trailing_destination_elements_not_stated' if opcode == 'CPY_BITARR'
                            else 'integer_bit_to_array_element_order_not_stated'])
                        self.assertEqual(value['count_domain'], {
                            'allowed_counts': review._bit_array_counts(record), 'constant_only': True})
                        self.assertTrue(set(fact['depends_on']) <= identities)
                        suffix = ':structured_ladder' if fact['scope']['languages'] == ['ladder'] else ''
                        for key in ('operands', 'limits_0', 'enable' if enabled else 'call_enable'):
                            self.assertIn('structured:' + opcode.lower() + ('_e' if enabled else '')
                                + ':' + key + '@' + review.FUNCTION_MANUAL + suffix, fact['depends_on'])
                        if opcode.startswith('BITARR_TO_'):
                            self.assertEqual(relation['destination_bits_beyond_count'], 'zero')
                            self.assertNotIn('trailing_destination_elements', relation)
                        else:
                            self.assertEqual(relation['trailing_destination_elements'],
                                             'not_stated' if opcode == 'CPY_BITARR' else 'unchanged')
                            self.assertNotIn('destination_bits_beyond_count', relation)
                        if opcode == 'CPY_BITARR':
                            self.assertFalse(any(':limits_1@' in key for key in fact['depends_on']))
                        else:
                            self.assertTrue(any(':limits_1@' in key for key in fact['depends_on']))

    def test_en_false_is_indefinite_from_common_table_not_retained(self):
        for opcode in review.BIT_ARRAY_COUNT_CONVERSIONS:
            record, rows = self.build(opcode, True)
            for fact in rows:
                if fact['dimension'] == 'execution.enable':
                    false_row = next(r for r in fact['value']['truth_table'] if r['enable'] == 'FALSE')
                    self.assertEqual(false_row['status'], 'FALSE')
                    self.assertEqual(false_row['operation_result'], 'indefinite_value')
                    self.assertTrue(any(s['pdf_pages'] == [36, 37] for s in fact['sources']))
                if fact['dimension'] == 'execution.disabled':
                    self.assertEqual(fact['value']['when_disabled'], 'indefinite_value')
                    self.assertEqual(fact['value']['outputs'], [record['outputs'][0][0]])
            _, incomplete = self.build(opcode, True, common_enable=False)
            for fact in incomplete:
                if fact['dimension'] == 'execution.enable':
                    self.assertEqual(fact['status'], 'declared_unverified')
                    self.assertEqual(next(r for r in fact['value']['truth_table']
                                          if r['enable'] == 'FALSE')['operation_result'], 'unknown')
            self.assertFalse(any(f['dimension'] == 'execution.disabled' for f in incomplete))

    def test_ordinary_call_is_at_call_site_without_inventing_scan_or_pulse(self):
        for opcode in review.BIT_ARRAY_COUNT_CONVERSIONS:
            _, rows = self.build(opcode, False)
            for fact in rows:
                if fact['dimension'] == 'execution.call_enable':
                    self.assertEqual(fact['value']['evaluation'], 'at_call_site')
                    self.assertEqual(fact['value']['enable_gate'], 'none')
            self.assertFalse(any(f['dimension'] == 'execution.enable' for f in rows))

    def test_own_full_operation_and_negated_padding_clauses_are_required(self):
        for opcode in review.BIT_ARRAY_COUNT_CONVERSIONS:
            record = next(r for r in review._conversion_records() if r['op'] == opcode)
            for clause in review.BIT_ARRAY_OPERATION_WITNESSES[opcode]:
                self.assertIn(clause, record['witness'])
                row = {'manual_id': review.FUNCTION_MANUAL,
                       'text': ' '.join(record['witness']).replace(clause, '')}
                with self.subTest(opcode=opcode, missing=clause), self.assertRaises(ValueError):
                    review._verify(row, record['witness'], opcode)


if __name__ == '__main__':
    unittest.main()
