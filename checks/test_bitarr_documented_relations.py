"""Documented FX n-element regions remain distinct from scalar execution."""
import copy
import unittest

from plc.instruction_relations import bind_relation_facts
from tools.inventory_instruction_semantics import _concrete_relation


CASES = (
    ('BITARR_TO_INT', 'bit_array_to_integer', 'BitArr', 'Output_label', 16, 140),
    ('BITARR_TO_DINT', 'bit_array_to_integer', 'BitArr', 'Output_label', 32, 142),
    ('INT_TO_BITARR', 'integer_to_bit_array', 's', 'BitArr', 16, 144),
    ('DINT_TO_BITARR', 'integer_to_bit_array', 's', 'BitArr', 32, 146),
    ('CPY_BITARR', 'bit_array_copy', 'BitArrIn', 'BitArrOut', 32, 148),
)


def documented_facts(case, enabled=False, language='st'):
    opcode, rule, source, destination, width, page = case
    opcode += '_E' if enabled else ''
    counts = list(range(4, width + 1, 4))
    reference = {'element_type': {'kind': 'bool'}, 'start': 'selected_array_element',
                 'extent': {'parameter': 'n', 'unit': 'logical_elements',
                            'allowed_counts': counts, 'constant_only': True}}
    def pin(name, direction, array=False):
        return {'name': name, 'direction': direction,
                'data_type': {'kind': 'bool'} if array else {'flexible_type': 'ANY' + str(width)},
                **({'argument_passing': 'array_element_reference',
                    'array_element_reference': copy.deepcopy(reference)} if array else {})}
    inputs = [pin(source, 'input', rule != 'integer_to_bit_array'),
              {'name': 'n', 'direction': 'input', 'data_type': {'kind': 'int', 'bits': 16, 'signed': True}}]
    output = pin(destination, 'output_variable', rule != 'bit_array_to_integer')
    operands = [*inputs, output]
    returned = {'value_type': copy.deepcopy(output['data_type']), 'returned_by_st_call': True,
                'operation_result_via': 'return_value'}
    for key in ('argument_passing', 'array_element_reference'):
        if key in output: returned[key] = copy.deepcopy(output[key])
    if enabled:
        operands = [{'name': 'EN', 'direction': 'input', 'data_type': {'kind': 'bool'},
                     'enable_pin': True, 'role': 'execution_condition'}, *operands,
                    {'name': 'ENO', 'direction': 'output', 'data_type': {'kind': 'bool'},
                     'enable_pin': True, 'role': 'execution_status'}]
        returned = {'value_type': {'kind': 'bool'}, 'returned_by_st_call': True,
                    'operation_result_via': 'output_argument', 'role': 'ENO_execution_status'}
    def fact(identity, dimension, value):
        return {'id': identity, 'dimension': dimension, 'value': value,
                'status': 'source_verified', 'verification': 'source_checked',
                'scope': {'models': ['FX3U'], 'forms': [opcode], 'languages': [language]},
                'conditions': ([{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
                               if language == 'ladder' else []),
                'sources': [{'manual_id': 'fxcpu_application_k', 'revision': 'K', 'pdf_pages': [page]}]}
    relation = {'kind': rule, 'source_parameter': source, 'destination_parameter': destination,
                'count_parameter': 'n', 'logical_unit': 'bit_elements',
                'source_selection': ('low_order_n_bits' if rule == 'integer_to_bit_array' else
                                     'n_elements_from_selected_array_element'),
                'destination_selection': ('converted_integer' if rule == 'bit_array_to_integer' else
                                          'n_elements_from_selected_array_element'),
                'element_bit_order': 'not_stated'}
    relation.update({'destination_bits_beyond_count': 'zero'} if rule == 'bit_array_to_integer' else
                    {'trailing_destination_elements': 'unchanged' if rule == 'integer_to_bit_array' else 'not_stated'})
    effect = fact('effect', 'effects.result_rule', {'rule': rule, 'bit_array_relation': relation,
                  'count_domain': {'allowed_counts': counts, 'constant_only': True}})
    effect['depends_on'] = ['signature', 'count', 'evaluation']
    padding = []
    if rule != 'bit_array_copy':
        effect['depends_on'].append('padding')
        padding = [fact('padding', 'constraints.result_padding',
                        {'beyond_specified_bits': 'zero' if rule == 'bit_array_to_integer' else 'unchanged'})]
    return [fact('signature', 'parameters.typed_operands', {'operands': operands, 'return_shape': returned}),
            effect, fact('count', 'constraints.bit_count', {'allowed': counts, 'constant_only': True}),
            *padding,
            fact('evaluation', 'execution.enable' if enabled else 'execution.call_enable',
                 {'enable_argument': 'EN', 'status_argument': 'ENO', 'enabled_form_execution': 'when_EN_is_TRUE'} if enabled else
                 {'enable_parameter': False, 'enable_gate': 'none', 'evaluation': 'at_call_site'})]


class BitArrayDocumentedRelationTests(unittest.TestCase):
    def test_unknown_order_or_trailing_elements_cannot_complete_effects_inventory(self):
        from plc.instructions import InstructionRegistry
        from tools.inventory_instruction_semantics import inspect_owner
        registry = InstructionRegistry()
        for case in (CASES[0], CASES[2], CASES[4]):
            with self.subTest(rule=case[1]):
                facts = documented_facts(case)
                facts += bind_relation_facts(facts)
                rows, _ = inspect_owner({'opcode': case[0], 'target_model': 'FX3U', 'facts': facts},
                                        registry, composed_facts=facts)
                self.assertEqual(rows[0]['states']['effects'], 'unresolved')
                self.assertTrue(any(gap['facet'] == 'effects' and gap['status'] == 'source_review_pending'
                                    for gap in rows[0]['remaining']))

    def test_ten_real_form_shapes_keep_regions_returns_and_unresolved_algorithm(self):
        for case in CASES:
            for enabled in (False, True):
                for language in ('st', 'fbd', 'ladder'):
                    with self.subTest(opcode=case[0], enabled=enabled, language=language):
                        facts = documented_facts(case, enabled, language)
                        before = copy.deepcopy(facts)
                        result, = bind_relation_facts(facts)
                        value = result['value']
                        self.assertEqual(value['relation_scope'], 'documented_n_element_relation')
                        self.assertEqual(value['executor_capability'], 'not_established')
                        self.assertFalse(value['full_source_relation_complete'])
                        self.assertTrue(value['unresolved_items'])
                        self.assertFalse(_concrete_relation(result))
                        self.assertNotIn('outputs', value)
                        self.assertNotIn('behavior', value)
                        self.assertNotIn('expression', str(value))
                        self.assertEqual(value['operand_binding']['return_shape'], facts[0]['value']['return_shape'])
                        for actual in [*value['operand_binding']['inputs'], value['operand_binding']['output']]:
                            original = next(pin for pin in facts[0]['value']['operands'] if pin['name'] == actual['parameter'])
                            if 'array_element_reference' in original:
                                self.assertEqual(actual['array_element_reference'], original['array_element_reference'])
                                self.assertEqual(actual['argument_passing'], 'array_element_reference')
                        self.assertEqual(result['conditions'], facts[0]['conditions'])
                        self.assertEqual(result['depends_on'][:2], ['effect', 'signature'])
                        if enabled:
                            self.assertEqual(value['operand_binding']['execution_arguments'], [
                                {'parameter': 'EN', 'role': 'execution_condition'},
                                {'parameter': 'ENO', 'role': 'execution_status'}])
                        self.assertEqual(facts, before)

    def test_incomplete_or_wrong_reference_count_interface_and_dependencies_do_not_bind(self):
        for mutation in ('label_only', 'count_not_constant', 'count_float', 'extent_mismatch',
                         'missing_reference', 'wrong_element_type', 'wrong_count_type', 'extra_input',
                         'wrong_output_direction', 'wrong_return', 'native', 'wrong_scope',
                         'wrong_interface', 'missing_dependency', 'missing_boundary',
                         'count_dependency_conflict', 'padding_dependency_conflict', 'extra_head_claim',
                         'missing_count_dependency', 'unverified_dependency',
                         'malformed_count_domain', 'missing_operand_table'):
            with self.subTest(mutation=mutation):
                facts = documented_facts(CASES[0])
                signature, effect = facts[:2]
                if mutation == 'label_only': effect['value'].pop('bit_array_relation')
                if mutation == 'count_not_constant': effect['value']['count_domain']['constant_only'] = False
                if mutation == 'count_float': effect['value']['count_domain']['allowed_counts'][0] = 4.0
                if mutation == 'extent_mismatch': signature['value']['operands'][0]['array_element_reference']['extent']['parameter'] = 'absent'
                if mutation == 'missing_reference': signature['value']['operands'][0].pop('array_element_reference')
                if mutation == 'wrong_element_type': signature['value']['operands'][0]['data_type'] = {'kind': 'int', 'bits': 16}
                if mutation == 'wrong_count_type': signature['value']['operands'][1]['data_type'] = {'kind': 'bool'}
                if mutation == 'extra_input': signature['value']['operands'].insert(0, {'name': 'extra', 'direction': 'input', 'type': 'BOOL'})
                if mutation == 'wrong_output_direction': signature['value']['operands'][-1]['direction'] = 'in_out'
                if mutation == 'wrong_return': signature['value']['return_shape']['operation_result_via'] = 'output_argument'
                if mutation == 'native': signature['value']['native_form'] = {'operand_order': ['BitArr', 'n', 'Output_label']}
                if mutation == 'wrong_scope': signature['scope']['languages'] = ['ladder']
                if mutation == 'wrong_interface':
                    signature['value']['declaration_kind'] = 'function_block'
                    effect['value']['declaration_kind'] = 'function'
                if mutation == 'missing_dependency': facts.pop()
                if mutation == 'missing_boundary': effect['value']['bit_array_relation'].pop('destination_bits_beyond_count')
                if mutation == 'count_dependency_conflict': facts[2]['value'] = {'allowed': [1, 2], 'constant_only': False}
                if mutation == 'padding_dependency_conflict': facts[3]['value']['beyond_specified_bits'] = 'unchanged'
                if mutation == 'extra_head_claim': effect['value']['bit_array_relation']['before_selected_head'] = 'unchanged'
                if mutation == 'missing_count_dependency': effect['depends_on'].remove('count')
                if mutation == 'unverified_dependency': facts[2]['status'] = 'candidate_evidence'
                if mutation == 'malformed_count_domain': effect['value']['count_domain'] = None
                if mutation == 'missing_operand_table': signature['value'].pop('operands')
                self.assertEqual(bind_relation_facts(facts), [])

    def test_reference_result_and_enabled_status_return_cannot_be_interchanged(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                facts = documented_facts(CASES[4], enabled)
                shape = facts[0]['value']['return_shape']
                if enabled:
                    shape.update(copy.deepcopy(facts[0]['value']['operands'][-2]['array_element_reference']))
                    shape['argument_passing'] = 'array_element_reference'
                else:
                    shape.pop('array_element_reference')
                self.assertEqual(bind_relation_facts(facts), [])


if __name__ == '__main__':
    unittest.main()
