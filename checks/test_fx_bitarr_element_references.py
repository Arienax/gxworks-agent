"""Array-head semantics survive canonical Bit types on every source interface."""
import copy
import unittest

from plc.instruction_definition import ValueType
from tools import fact_structured_behavior_review as review


class FxBitArrayElementReferenceTests(unittest.TestCase):
    cases = (
        ('BITARR_TO_INT', ['BitArr'], [4, 8, 12, 16]),
        ('BITARR_TO_DINT', ['BitArr'], [4, 8, 12, 16, 20, 24, 28, 32]),
        ('INT_TO_BITARR', ['BitArr'], [4, 8, 12, 16]),
        ('DINT_TO_BITARR', ['BitArr'], [4, 8, 12, 16, 20, 24, 28, 32]),
        ('CPY_BITARR', ['BitArrIn', 'BitArrOut'], [4, 8, 12, 16, 20, 24, 28, 32]),
    )

    def test_element_type_and_access_extent_are_preserved_on_each_interface(self):
        for opcode, names, counts in self.cases:
            record = next(r for r in review._conversion_records() if r['op'] == opcode)
            before = copy.deepcopy(record)
            expected = {'element_type': {'kind': 'bool'}, 'start': 'selected_array_element',
                'extent': {'parameter': 'n', 'unit': 'logical_elements',
                           'allowed_counts': counts, 'constant_only': True}}
            for enabled in (False, True):
                with self.subTest(opcode=opcode, enabled=enabled):
                    operands = review._operand_facts(record, enabled)
                    references = [p for p in operands if p.get('array_element_reference')]
                    self.assertEqual([p['name'] for p in references], names)
                    directions = {'BITARR_TO_INT': ['input'], 'BITARR_TO_DINT': ['input'],
                        'INT_TO_BITARR': ['output_variable'], 'DINT_TO_BITARR': ['output_variable'],
                        'CPY_BITARR': ['input', 'output_variable']}
                    self.assertEqual([p['direction'] for p in references], directions[opcode])
                    collections = [operands, review._pins(record, enabled),
                                   review._st_parameters(record, enabled)]
                    returns = review._st_returns(record, enabled)
                    shape = review._return_shape(record, enabled)
                    output_array = opcode in {'INT_TO_BITARR', 'DINT_TO_BITARR', 'CPY_BITARR'}
                    if output_array and not enabled:
                        collections.extend([[returns], [shape]])
                    else:
                        self.assertNotIn('array_element_reference', returns)
                        self.assertNotIn('array_element_reference', shape)
                    for collection in collections:
                        for parameter in collection:
                            if 'array_element_reference' not in parameter:
                                continue
                            self.assertEqual(parameter['array_element_reference'], expected)
                            self.assertEqual(parameter['argument_passing'], 'array_element_reference')
                            kind = parameter.get('data_type', parameter.get('type', parameter.get('value_type')))
                            self.assertEqual(ValueType.from_mapping(kind).kind, 'bool')
                            self.assertEqual(kind, {'kind': 'bool'})
                    # No interface can mutate another interface's qualifier.
                    references[0]['array_element_reference']['extent']['allowed_counts'].clear()
                    fresh = next(p['array_element_reference']
                                 for p in review._operand_facts(record, enabled)
                                 if 'array_element_reference' in p)
                    self.assertEqual(fresh['extent']['allowed_counts'], counts)
            self.assertEqual(record, before)

    def test_count_domain_and_element_head_are_own_source_requirements(self):
        for opcode, _, counts in self.cases:
            record = next(r for r in review._conversion_records() if r['op'] == opcode)
            numbers = ', '.join(map(str, counts[:-1])) + ' or ' + str(counts[-1])
            clauses = ('Specify an array element.',
                       'Only a constant ' + numbers + ' can be specified in n .',
                       *review.BIT_ARRAY_ELEMENT_WITNESSES[opcode])
            for clause in clauses:
                self.assertIn(clause, record['witness'])
                row = {'manual_id': review.FUNCTION_MANUAL,
                       'text': ' '.join(record['witness']).replace(clause, '')}
                with self.subTest(opcode=opcode, missing=clause), self.assertRaises(ValueError):
                    review._verify(row, record['witness'], opcode)
            incomplete = copy.deepcopy(record)
            incomplete['limits'] = [(d, v) for d, v in incomplete['limits'] if d != 'constraints.bit_count']
            with self.assertRaises(ValueError):
                review._parameter_qualifiers(incomplete, 'BITARR')

    def test_plain_conversion_returns_keep_any_and_documented_width_alternatives(self):
        expected = {
            'BITARR_TO_INT': ({'flexible_type': 'ANY16'}, ['INT', 'WORD']),
            'BITARR_TO_DINT': ({'flexible_type': 'ANY32'}, ['DINT', 'DWORD']),
        }
        for opcode, (primary, alternatives) in expected.items():
            record = next(r for r in review._conversion_records() if r['op'] == opcode)
            with self.subTest(opcode=opcode):
                signature_return = review._st_returns(record, enabled=False)
                return_shape = review._return_shape(record, enabled=False)
                self.assertEqual(signature_return['type'], primary)
                self.assertEqual(signature_return['type_alternatives'], alternatives)
                self.assertEqual(return_shape['value_type'], primary)
                self.assertEqual(return_shape['type_alternatives'], alternatives)
                self.assertEqual(review._st_returns(record, enabled=True),
                                 {'type': {'kind': 'bool'}, 'role': 'ENO_execution_status'})
                self.assertNotIn('type_alternatives', review._return_shape(record, enabled=True))

                incomplete = copy.deepcopy(record)
                evidence = review.BIT_ARRAY_PLAIN_RETURN_TYPE_ALTERNATIVES[opcode][1]
                incomplete['witness'] = tuple(
                    phrase for phrase in incomplete['witness']
                    if review._flatten(evidence) not in review._flatten(phrase))
                with self.assertRaises(ValueError):
                    review._bit_array_plain_return_type_alternatives(incomplete)


if __name__ == '__main__':
    unittest.main()
