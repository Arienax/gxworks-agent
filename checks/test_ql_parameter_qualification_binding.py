"""Named source constraints must match every declared interface contract."""
import copy
from types import SimpleNamespace
import unittest
from tools.fact_ql_behavior_review import (Section, _numeric_limit_specs,
    _bind_n_parameter_qualifications, _reviewed_spec)


def owner(symbol):
    return SimpleNamespace(manual='sh080784engn', symbols=[symbol], kind='function',
        chapter='synthetic', symbol_tokens=[], call_forms=lambda: [])


def contract(pins, languages=('st', 'fbd')):
    return _reviewed_spec('typed_operands', 'parameters.typed_operands', ['synthetic-contract'],
        {'operands': [dict(name=name, type=kind, direction='input') for name, kind in pins]}, languages)


def qualification(key, names, **detail):
    return _reviewed_spec('n_review_' + key, 'constraints.source_qualifications', ['synthetic-clause'],
        {'subjects': names, 'qualifications': [dict(operand_names=names, parameter_binding='unknown',
            source_disposition='source_present_binding_unresolved', **detail)]}, ('st', 'fbd'))


class QLParameterQualificationBinding(unittest.TestCase):
    def test_numeric_subject_case_matches_actual_contract(self):
        for subject, name, symbol in [('n', 'n', 'INSERT_E'), ('N', 'n', 'INSERT_E'),
                                      ('s', 's', 'INT_TO_DINT_E'), ('S', 's', 'INT_TO_DINT_E')]:
            with self.subTest(subject=subject):
                fake = owner(symbol)
                fake.limits = lambda ignored: [dict(subject=subject, minimum=1, maximum=255, line='synthetic-range')]
                result = _numeric_limit_specs(fake, symbol, [contract([(name, 'INT')])])
                self.assertEqual(result[0]['dimension'], 'constraints.numeric_ranges')
                self.assertEqual(result[0]['value']['ranges'][0]['operand_index'], 0)

    def test_mid_explicit_position_does_not_bind_trailing_source_string(self):
        fake = owner('MID_E')
        fake.lines = [f'The value to be input to {name} is word (signed) type data within the range from '
            f'{minimum} to 255. (The input value must not exceed the number of characters of '
            'character string input to (s).)' for name, minimum in [('n1', 0), ('n2', 1)]]
        result = Section.limits(fake, 'MID_E')
        self.assertEqual([limit['subject'] for limit in result], ['N1', 'N2'])
        self.assertTrue(all(limit.get('subject_kind') != 'string_length' for limit in result))
        self.assertTrue(all(limit['qualifications'][0]['maximum_from'] == 'string_length' for limit in result))

    def test_named_qualification_requires_all_pins_and_all_interfaces(self):
        fake = owner('MID_E')
        item = qualification('string_position_length', ['n1', 'n2'], unit='character',
            bounds=[dict(name='n1', minimum=0, maximum=255, maximum_length_of='s'),
                    dict(name='n2', minimum=1, maximum=255, maximum_length_of='s')])
        valid = contract([('n1', 'INT'), ('n2', 'INT'), ('s', 'STRING')])
        def result(contracts):
            return _bind_n_parameter_qualifications(fake, 'MID_E', [item, *contracts])[0]['value']['qualifications'][0]
        self.assertEqual(result([valid])['parameter_binding'], 'named_parameter_contract')
        self.assertEqual(result([valid])['unit'], 'character')
        self.assertEqual(result([valid])['bounds'][0]['maximum_length_of'], 's')
        for missing in ['n1', 'n2', 's']:
            broken = copy.deepcopy(valid)
            broken['value']['operands'] = [p for p in broken['value']['operands'] if p['name'] != missing]
            self.assertEqual(result([broken])['parameter_binding'], 'unknown')
        st_only = dict(valid, languages=['st'])
        wrong_fbd = contract([('n1', 'REAL'), ('n2', 'INT'), ('s', 'STRING')], ('fbd',))
        self.assertEqual(result([st_only, wrong_fbd])['parameter_binding'], 'unknown')
        self.assertEqual(result([st_only])['parameter_binding'], 'unknown')

    def test_concat_expands_only_complete_declared_string_group(self):
        fake = owner('CONCAT_E')
        item = qualification('string_capacity', ['s1..s28'], unit='byte', minimum=0, maximum=255)
        valid = contract([('s' + str(n), 'STRING') for n in range(1, 29)])
        def result(typed):
            return _bind_n_parameter_qualifications(fake, 'CONCAT_E', [item, typed])[0]['value']['qualifications'][0]
        self.assertEqual(len(result(valid)['operand_names']), 28)
        self.assertEqual(result(valid)['unit'], 'byte')
        broken = copy.deepcopy(valid)
        broken['value']['operands'].pop(5)
        self.assertEqual(result(broken)['parameter_binding'], 'unknown')
        extra = copy.deepcopy(valid)
        extra['value']['operands'].append(dict(name='s29', type='STRING', direction='input'))
        self.assertEqual(result(extra)['parameter_binding'], 'unknown')

    def test_any_num_remains_polymorphic(self):
        item = qualification('nonzero_divisor', ['s2'], relation='not_equal', excluded_value=0)
        result = _bind_n_parameter_qualifications(owner('DIV_TIME_E'), 'DIV_TIME_E',
            [item, contract([('s2', 'ANY_NUM')])])[0]['value']['qualifications'][0]
        self.assertEqual(result['operand_bindings'], [dict(name='s2', type='ANY_NUM', direction='input')])
        self.assertNotIn('bits', result)


if __name__ == '__main__':
    unittest.main()
