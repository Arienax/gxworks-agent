"""Own X Native pulse-NOT contracts must retain their actual source boundaries."""
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from tools.fact_ql_behavior_review import _cpu_header_requirement, _formal_conditions, _native_parameter_specs, _reviewed_contact_specs


class QLPulseNotSourceRules(unittest.TestCase):
    def test_own_cpu_serial_clause_does_not_leak_to_explicit_supported_models(self):
        section = SimpleNamespace(lines=[
            'QnU(D)(H)CPU, QnUDE(H)CPU: the serial number (first five digits) is "10102" or later',
            'Q00UJCPU, Q00UCPU, Q01UCPU, QnUDVCPU, QnUDPVCPU: Supported'])
        for model in ['Q03UD', 'Q03UDE', 'Q06UDH', 'Q06UDEH']:
            with self.subTest(model=model):
                result = _cpu_header_requirement(section, model)
                self.assertEqual(result['state'], 'conditional')
                self.assertEqual(result['conditions'][0]['minimum'], '10102')
        for model in ['Q00UJ', 'Q00U', 'Q01U', 'Q03UDV', 'Q13UDPV']:
            self.assertEqual(_cpu_header_requirement(section, model), {'state': 'supported', 'conditions': []})
        self.assertIsNone(_cpu_header_requirement(section, 'L02'))
        # P91 states its own same split in a single sentence.
        section.lines = ['QnU(D)(H)CPU and QnUDE(H)CPU with a serial number (first five digits) of "10102" or later, '
                         'Q00UJCPU, Q00UCPU, Q01UCPU, QnUDVCPU, and QnUDPVCPU: Supported']
        for model in ['Q00U', 'Q03UDV']:
            self.assertEqual(_cpu_header_requirement(section, model), {'state': 'supported', 'conditions': []})
        from plc.instruction_constraints import _condition
        from plc.instruction_definition import UnknownInstructionSemantics
        for model in ['Q03UD', 'Q03UDE']:
            conditions, classified = _formal_conditions(_cpu_header_requirement(section, model)['conditions'])
            self.assertTrue(classified)
            condition = conditions[0]
            self.assertFalse(_condition(condition, {}, {'cpu_serial_first_five': 10101}))
            self.assertTrue(_condition(condition, {}, {'cpu_serial_first_five': 10102}))
            with self.assertRaises(UnknownInstructionSemantics):
                _condition(condition, {}, {})

    def test_literal_pair_connection_clause_produces_inverted_edge_and_composition(self):
        text = ('ANDPI is a rising edge pulse NOT series connection, and ANDFI is a falling pulse NOT series connection. '
                'ANDPI and ANDFI execute an AND operation with the previous operation result. '
                'ORPI is a rising edge pulse NOT parallel connection, and ORFI is a falling pulse NOT parallel connection. '
                'LDPI is the rising edge pulse NOT operation start instruction. '
                'LDFI is the falling edge pulse NOT operation start instruction.')
        section = SimpleNamespace(manual='sh080809engx', text=text)
        with patch('tools.fact_ql_behavior_review.family_of', return_value='contact'), patch(
                'tools.fact_ql_behavior_review._section_languages', return_value=('ladder',)):
            for symbol, edge, composition in [('LDPI','rising','assign'), ('LDFI','falling','assign'),
                    ('ANDPI','rising','and'), ('ANDFI','falling','and'), ('ORPI','rising','or'), ('ORFI','falling','or')]:
                value = _reviewed_contact_specs(section, symbol)[0]['value']
                self.assertEqual(value['predicate'], 'NOT(' + edge + '_edge(S))')
                self.assertEqual(value['composition'], composition)
                self.assertNotIn('refresh', value)
            section.text = 'ANDPI, ANDFI pulse NOT series connection'
            self.assertEqual(_reviewed_contact_specs(section, 'ANDPI'), [])

    def test_addressing_forms_need_own_drawing_not_just_bool_or_symbol(self):
        row = dict(description='Devices used as contacts (bits)', witness='synthetic-bit-legend')
        section = SimpleNamespace(manual='sh080809engx', heading_line='synthetic-heading',
            _native_parameter_contracts=[dict(allowed=['ANDPI'], order=['S'], rows={'S': row}, minimum=1)],
            bind=lambda witness: [(1,139)], text='synthetic')
        with patch('tools.fact_ql_behavior_review._native_data_type', return_value={'type':'BOOL'}), patch(
                'tools.fact_ql_behavior_review._parameter_form_type', side_effect=lambda s,o,p:p):
            pin = _native_parameter_specs(section, 'ANDPI')[0]['value']['operands'][0]
            self.assertEqual(pin['addressing_forms'], ['bit_device', 'word_device_bit_designation'])
            self.assertEqual(pin['passing'], 'device_reference')
            self.assertNotIn('allowed_device_classes', pin)
            section.bind = lambda witness: []
            pin = _native_parameter_specs(section, 'ANDPI')[0]['value']['operands'][0]
            self.assertNotIn('addressing_forms', pin)
