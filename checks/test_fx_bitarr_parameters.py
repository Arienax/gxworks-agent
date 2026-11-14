"""Own FX bit-array parameter tables retain their explicit count argument."""
import copy
import unittest

from tools import fact_structured_behavior_review as review
from tools.inventory_instruction_semantics import _type_present


class FxBitArrayParameterTests(unittest.TestCase):
    # Names and argument positions from K PDF140/142/144/146/148 Set data/Format.
    cases = (
        ('BITARR_TO_INT', 'BitArr', 'Output_label', 'specified_bits'),
        ('BITARR_TO_DINT', 'BitArr', 'Output_label', 'specified_bits'),
        ('INT_TO_BITARR', 's', 'BitArr', 'specified_bits'),
        ('DINT_TO_BITARR', 's', 'BitArr', 'specified_bits'),
        ('CPY_BITARR', 'BitArrIn', 'BitArrOut', 'copied_bits'),
    )

    def test_witness_supplement_does_not_erase_count_contract(self):
        for opcode, source, destination, role in self.cases:
            record = next(r for r in review._conversion_records() if r['op'] == opcode)
            before = copy.deepcopy(record)
            self.assertEqual(record['st_plain'], f'{opcode}({source},n)')
            self.assertEqual(record['st_enabled'], f'{opcode}_E(EN,{source},n,{destination})')
            for enabled in (False, True):
                with self.subTest(opcode=opcode, enabled=enabled):
                    operands = review._operand_facts(record, enabled)
                    count, = [p for p in operands if p['marker'] == 'n']
                    self.assertEqual(count, {'name': 'n', 'marker': 'n', 'direction': 'input',
                        'role': role, 'data_type': {'kind': 'int', 'bits': 16, 'signed': True},
                        'enable_pin': False})
                    self.assertTrue(_type_present(count, 'st_call'))
                    self.assertEqual([p['name'] for p in review._st_parameters(record, enabled)],
                        ['EN', source, 'n', destination] if enabled else [source, 'n'])
                    self.assertEqual([p['pin'] for p in review._pins(record, enabled)
                                      if p['direction'] == 'input' and p['pin'] != 'EN'], [source, 'n'])
                    self.assertEqual(review._st_parameters(record, enabled)[2 if enabled else 1],
                        {'name': 'n', 'type': {'kind': 'int', 'bits': 16, 'signed': True},
                         'direction': 'input'})
                    # Element-head references supply no whole-array bounds.
                    for operand in operands:
                        if operand.get('array_element_reference'):
                            self.assertEqual(operand['data_type'], {'kind': 'bool'})
                            self.assertNotIn('index_max', operand['data_type'])
                    self.assertEqual(record, before)

    def test_count_binding_requires_its_own_complete_table_row(self):
        witness = 'n | Number of specified bits | Word [signed]'
        for opcode, *_ in self.cases:
            record = next(r for r in review._conversion_records() if r['op'] == opcode)
            self.assertIn(witness, record['witness'])
            original = ' '.join(record['witness'])
            for replacement in ('m | Number of specified bits | Word [signed]',
                                'n | Number of specified bits | Word [unsigned]',
                                'n | Number of specified bits |'):
                row = {'manual_id': review.FUNCTION_MANUAL,
                       'text': original.replace(witness, replacement)}
                with self.subTest(opcode=opcode, replacement=replacement), self.assertRaises(ValueError):
                    review._verify(row, record['witness'], opcode)


if __name__ == '__main__':
    unittest.main()
