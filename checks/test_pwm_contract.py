# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Public synthetic contract regressions; no private manual corpus fixtures."""
import copy
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from plc.pwm_contract import SOURCE_BINDING, validate_pwm_request
from plc.instruction_definition import UnknownInstructionSemantics


def context():
    return {'program_context': 'ladder', 'hardware': {'module_type': 'CPU_module', 'output_type': 'transistor'},
            'pwm': {'execution_mode': 'core_software_model', 'source_binding': SOURCE_BINDING,
                    'initially_idle': True, 'pulse_count': 0, 'channel': 1, 'output_device': 'Y0',
                    'configured_for_PWM': True, 'unused_channel_available': True,
                    'resource_owner': None, 'time_unit': 'us'}}


class ReviewedPWMTestCase(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from plc.pwm_fact_source import snapshot_facts
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as installed
        class ReviewedRegistry:
            def __getattr__(self, name): return getattr(installed, name)
            def fact_bundle_current(self): return True
            def definition_entry(self, opcode, *, cpu):
                return {'facts': snapshot_facts(opcode, cpu)} if opcode in {'PWM', 'DPWM'} else installed.definition_entry(opcode, cpu=cpu)
        self._owner_patch = patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY', ReviewedRegistry())
        self._owner_patch.start()
        self.addCleanup(self._owner_patch.stop)


class PWMContractTests(ReviewedPWMTestCase):
    def test_explicit_bounded_contract(self):
        for cpu in ('FX5U', 'FX5UC', 'FX5UJ', 'FX5S'):
            for opcode in ('PWM', 'DPWM'):
                request = validate_pwm_request(opcode, cpu, 10, 20, 'Y0', context())
                self.assertEqual(request['period_us'], 20)
                self.assertEqual(request['coverage'], 'bounded_software_model_only')
                self.assertEqual(request['whole_graph_admission'], 'not_established')

    def test_configuration_unknown_does_not_pass(self):
        for key in ('source_binding', 'time_unit', 'resource_owner', 'configured_for_PWM', 'initially_idle', 'pulse_count'):
            c = context(); del c['pwm'][key]
            with self.assertRaises(UnknownInstructionSemantics):
                validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', c)

    def test_known_relation_and_resource_violations(self):
        with self.assertRaises(ValueError):
            validate_pwm_request('PWM', 'FX5U', 20, 10, 'Y0', context())
        c = context(); c['pwm']['resource_owner'] = 'positioning_instruction'
        with self.assertRaises(ValueError):
            validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', c)

    def test_source_disagreements_stay_unknown(self):
        for output, on, period in [('Y0', 2, 5), ('Y3', 10, 20)]:
            c = context(); c['pwm']['output_device'] = output
            with self.assertRaises(UnknownInstructionSemantics):
                validate_pwm_request('PWM', 'FX5S', on, period, output, c)
        c = context(); c['pwm']['time_unit'] = 'ms'
        with self.assertRaises(UnknownInstructionSemantics):
            validate_pwm_request('DPWM', 'FX5U', 100000000, 200000000, 'Y0', c)

    def test_unsupported_does_not_become_known_illegal(self):
        with self.assertRaises(ValueError):
            validate_pwm_request('PWM', 'FX5U', 0, 20, 'Y0', context())
        with self.assertRaises(UnknownInstructionSemantics):
            validate_pwm_request('PWM', 'FX5U', 10, 10, 'Y0', context())
        for count in (1, False, 0.0, None):
            c = context(); c['pwm']['pulse_count'] = count
            with self.assertRaises(UnknownInstructionSemantics):
                validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', c)


if __name__ == '__main__':
    unittest.main(verbosity=2)
