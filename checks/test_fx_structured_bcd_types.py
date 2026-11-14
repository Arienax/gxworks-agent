"""BCD parameter types follow the own FX Set data table, not encoding names."""
import copy
import unittest

from tools import fact_structured_behavior_review as review
from tools.inventory_instruction_semantics import _type_present, _type_bits, _storage_present


class FxBcdParameterTypesTests(unittest.TestCase):
    def test_all_conversion_forms_keep_declared_types_and_encoding(self):
        for opcode in review.BCD_PARAMETER_WITNESSES:
            record = next(r for r in review._conversion_records() if r['op'] == opcode)
            before = copy.deepcopy(record)
            packed = next(token for _, token, _, _ in record['inputs'] + record['outputs']
                          if token.startswith('BCD'))
            expected = ({'kind': 'int', 'bits': 16, 'signed': False, 'encoding': 'bcd'}
                        if packed == 'BCD16' else {'flexible_type': 'ANY_BIT', 'encoding': 'bcd'})
            for enabled in (False, True):
                with self.subTest(opcode=opcode, enabled=enabled):
                    form = opcode + ('_E' if enabled else '')
                    condition = {'context': 'cpu_version_not_resolved'}
                    rows = review._form_facts(record, form, enabled, 'FX3U', {'text': ''},
                        [{'source_id': 'own-set-data-table'}], condition, 'declared_unverified')
                    signature = next(f for f in rows if f['dimension'] == 'parameters.typed_operands')
                    operands = signature['value']['operands']
                    operand = next(p for p in operands if p['data_type'].get('encoding') == 'bcd')
                    self.assertEqual(operand['data_type'], expected)
                    for interface in ('st_call', 'fbd_call', 'structured_ladder_block'):
                        self.assertTrue(all(_type_present(p, interface) for p in operands))
                    self.assertEqual(signature['status'], 'declared_unverified')
                    self.assertEqual(signature['conditions'], [condition])
                    pins = review._pins(record, enabled)
                    self.assertEqual(next(p['data_type'] for p in pins
                                          if p['data_type'].get('encoding') == 'bcd'), expected)
                    st = review._st_parameters(record, enabled)
                    exposed = [p['type'] for p in st] + [review._st_returns(record, enabled)['type']]
                    self.assertIn(expected, exposed)
                    if packed == 'BCD32':
                        domain = next(f for f in rows if f['dimension'] == 'parameters.type_domain'
                                      and f['value']['type_token'] == 'ANY_BIT')
                        self.assertTrue(domain['value']['generic_data_type'])
                        self.assertNotIn('bits', expected)
                        self.assertFalse(_storage_present(operand, 'native_ladder_instruction'))
                        self.assertIsNone(_type_bits(operand))
            self.assertEqual(record, before)

    def test_encoding_metadata_alone_does_not_establish_a_parameter_type(self):
        for bits in (16, 32):
            self.assertFalse(_type_present({'data_type': {'encoding': 'bcd', 'bits': bits}}, 'st_call'))

    def test_own_table_type_witness_is_required(self):
        for opcode, phrase in review.BCD_PARAMETER_WITNESSES.items():
            record = next(r for r in review._conversion_records() if r['op'] == opcode)
            self.assertIn(phrase, record['witness'])
            row = {'manual_id': review.FUNCTION_MANUAL, 'text': ' '.join(record['witness'])}
            review._verify(row, record['witness'], opcode)
            row['text'] = row['text'].replace(phrase, '')
            with self.assertRaises(ValueError):
                review._verify(row, record['witness'], opcode)


class FxBcdConditionalStorageTests(unittest.TestCase):
    SOURCE = {'id': '2715', 'manual_id': 'fxcpu_application_k', 'manual_number': 'JY997D34801',
              'revision': 'K', 'pdf_pages': [33, 34]}

    def record(self, opcode):
        return dict(next(r for r in review._conversion_records() if r['op'] == opcode),
                    bcd_storage_source=copy.deepcopy(self.SOURCE))

    def test_every_real_bcd32_interface_preserves_abstract_type_and_all_conditional_spans(self):
        for opcode in ('BCD_TO_DINT', 'DINT_TO_BCD'):
            for enabled in (False, True):
                with self.subTest(opcode=opcode, enabled=enabled):
                    record = self.record(opcode)
                    packed = next(p for p in review._operand_facts(record, enabled)
                                  if p['data_type'].get('encoding') == 'bcd')
                    self.assertEqual(packed['data_type'], {'flexible_type': 'ANY_BIT', 'encoding': 'bcd'})
                    self.assertIsNone(_type_bits(packed))
                    self.assertEqual(packed['type_alternatives'], ['BOOL', 'WORD', 'DWORD'])
                    self.assertEqual([(b['type'], b['occupied_bit_count'])
                                      for b in packed['storage_by_type']['branches']],
                                     [('BOOL', 1), ('WORD', 16), ('DWORD', 32)])
                    for branch in packed['storage_by_type']['branches']:
                        self.assertEqual(branch['sources'], [self.SOURCE])
                        self.assertNotIn('word_device_layout', branch)
                    from shared.model_context import technical_value, model_knowledge_value
                    projected = model_knowledge_value(technical_value(packed))
                    self.assertEqual(projected['storage_by_type']['selected_by'], 'actual_parameter_type')
                    self.assertEqual([(b['type'], b['occupied_bit_count'])
                                      for b in projected['storage_by_type']['branches']],
                                     [('BOOL', 1), ('WORD', 16), ('DWORD', 32)])
                    self.assertNotIn('occupied_bit_count', packed)
                    for interface in ('st_call', 'fbd_call', 'structured_ladder_block'):
                        self.assertTrue(_storage_present(packed, interface, sources=[self.SOURCE]))
                    self.assertFalse(_storage_present(packed, 'native_ladder_instruction', sources=[self.SOURCE]))
                    pins = [p for p in review._pins(record, enabled) if p['data_type'].get('encoding') == 'bcd']
                    st = [p for p in review._st_parameters(record, enabled) if p['type'].get('encoding') == 'bcd']
                    if opcode == 'DINT_TO_BCD' and not enabled:
                        st.append(review._st_returns(record, enabled))
                        shape = review._return_shape(record, enabled)
                        self.assertEqual(shape['storage_by_type'], packed['storage_by_type'])
                    if enabled:
                        self.assertNotIn('storage_by_type', review._st_returns(record, enabled))
                        self.assertNotIn('storage_by_type', review._return_shape(record, enabled))
                    self.assertTrue(pins and st)
                    for parameter in pins + st:
                        self.assertEqual(parameter['storage_by_type'], packed['storage_by_type'])

    def test_storage_requires_every_width_and_the_enclosing_source_binding(self):
        original = next(p for p in review._operand_facts(self.record('BCD_TO_DINT'), False)
                        if p['data_type'].get('encoding') == 'bcd')
        for case in ('no_conditional_layout', 'no_members', 'mapping_member', 'missing_bit_member',
                     'missing_branch', 'missing_width', 'wrong_width', 'duplicate_branch',
                     'wrong_source', 'wrong_revision', 'wrong_page', 'mapping_source', 'no_source', 'extra_layout_field'):
            with self.subTest(case=case):
                parameter = copy.deepcopy(original)
                branches = parameter['storage_by_type']['branches']
                if case == 'no_conditional_layout': parameter.pop('storage_by_type')
                if case == 'no_members': parameter.pop('type_alternatives')
                if case == 'mapping_member': parameter['type_alternatives'][0] = {'bits': 1}
                if case == 'missing_bit_member': parameter['type_alternatives'].remove('BOOL')
                if case == 'missing_branch': branches.pop(0)
                if case == 'missing_width': branches[1].pop('occupied_bit_count')
                if case == 'wrong_width': branches[2]['occupied_bit_count'] = 16
                if case == 'duplicate_branch': branches[2] = copy.deepcopy(branches[1])
                if case == 'wrong_source': branches[1]['sources'][0]['manual_id'] = 'unrelated_manual'
                if case == 'wrong_revision': branches[1]['sources'][0]['revision'] = 'unrelated_revision'
                if case == 'wrong_page': branches[1]['sources'][0]['pdf_pages'] = [80]
                if case == 'mapping_source': branches[1]['sources'][0]['manual_id'] = {'unrelated': True}
                if case == 'no_source': branches[2]['sources'] = []
                if case == 'extra_layout_field': branches[2]['word_count'] = 2
                self.assertFalse(_storage_present(parameter, 'st_call', sources=[self.SOURCE]))
        self.assertFalse(_storage_present(original, 'st_call'))
        published_source = {key: value for key, value in self.SOURCE.items() if key != 'id'}
        published_source['source_ref'] = 'fxcpu_application_k:K:pages=33,34'
        self.assertTrue(_storage_present(original, 'st_call', sources=[published_source]))
        self.assertFalse(_storage_present({'data_type': {'flexible_type': 'ANY_BIT'},
                                          'type_alternatives': ['BOOL', 'WORD', 'DWORD']}, 'st_call'))

    def test_width_and_domain_source_witnesses_are_all_required(self):
        text = ' '.join(review.BCD_STORAGE_WITNESSES)
        review._verify({'manual_id': review.FUNCTION_MANUAL, 'text': text}, review.BCD_STORAGE_WITNESSES, 'BCD storage')
        for witness in review.BCD_STORAGE_WITNESSES:
            with self.subTest(witness=witness), self.assertRaises(ValueError):
                review._verify({'manual_id': review.FUNCTION_MANUAL, 'text': text.replace(witness, '')},
                               review.BCD_STORAGE_WITNESSES, 'BCD storage')


if __name__ == '__main__':
    unittest.main()
