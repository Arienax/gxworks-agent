# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Synthetic source-scope and public contract regressions; no private corpus."""
import copy
import pathlib
import sys
import unittest
from unittest.mock import patch
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from plc.pwm_contract import derive_pwm_formal_facts, validate_pwm_request, PWM_AB_MINIMUM_TIMES, _supported_pwm_rule_shape
from plc.instruction_constraints import check_fact_constraints
from plc.instruction_definition import InstructionFactGroup, UnknownInstructionSemantics
from checks.test_pwm_contract import context


def witness(kind='operand_relation'):
    # Use public reviewed semantic fixtures, not invented provenance. Mutations
    # below remain synthetic and exercise the same source-safety regressions.
    from plc.pwm_fact_source import snapshot_facts
    return next(f for f in snapshot_facts('PWM', 'FX5U')
                if any(r['kind'] == kind for r in f['value'].get('rules', [])))


def prerequisite_facts():
    from plc.pwm_fact_source import snapshot_facts
    return [f for f in snapshot_facts('PWM', 'FX5U')
            if f['dimension'] in {'identity.applicability', 'parameters.typed_operands'}]


def derived(source, suffix='operand_relation'):
    return next(f for f in derive_pwm_formal_facts('PWM', 'FX5U', 'ladder',
        [InstructionFactGroup.from_mapping(source)]) if f['id'].endswith(':' + suffix))


def check(source, *, version='v1', extra=(), include_source=True):
    d = derived(source)
    from plc.pwm_fact_source import snapshot_facts
    closure = [f for f in snapshot_facts('PWM', 'FX5U')
               if f['dimension'] in {'identity.applicability', 'parameters.typed_operands'}]
    if not include_source:
        closure = [f for f in closure if f['dimension'] != 'identity.applicability']
    report = check_fact_constraints(closure + ([source] if include_source else []) + [d] + list(extra),
        {'S1': 20, 'S2': 10, '$OPERANDS': ['K20', 'K10', 'Y0']}, target='PWM',
        plc_model='FX5U', language='ladder', version=version, context=context())
    return next(r for r in report['checks'] if r['fact_id'] == d['id'])['status']


class PWMContractCorrectness(unittest.TestCase):
    def test_unsupported_language_never_vacuously_accepts(self):
        for language in ('sfc', '', None, 'LADDER', [], {}):
            c = context(); c['language'] = language
            with self.subTest(language=language), self.assertRaises(UnknownInstructionSemantics):
                validate_pwm_request('PWM', 'FX5U', 20, 10, 'Y0', c)

    def test_all_required_predicates_must_actually_pass(self):
        for checks in ([], [{'fact_id': 'irrelevant', 'status': 'passed'}]):
            with patch('plc.instruction_constraints.check_fact_constraints', return_value={'status': 'passed', 'checks': checks}):
                with self.assertRaises(UnknownInstructionSemantics):
                    validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', context())

    def test_derived_scope_retains_source_version(self):
        s = witness(); s['scope']['versions'] = ['v2']
        self.assertEqual(derived(s)['scope']['versions'], ['v2'])
        self.assertEqual(check(s), 'not_applicable')
        self.assertEqual(check(s, version=None), 'unverified')
        self.assertEqual(check(s, version='v2'), 'failed')

    def test_derived_dependency_is_real_not_just_a_label(self):
        s = witness(); s['depends_on'].append('prerequisite')
        d = derived(s)
        self.assertEqual(set(d['depends_on']), set(s['depends_on']))
        self.assertEqual(d['sources'], s['sources'])
        self.assertEqual(check(s), 'unverified')
        dep = {'id': 'prerequisite', 'dimension': 'identity.applicability', 'value': {},
               'status': 'source_verified', 'verification': 'source_checked', 'sources': [{'id': 'synthetic'}]}
        self.assertEqual(check(s, extra=[dep]), 'failed')
        dep['status'] = 'declared_unverified'; dep['verification'] = 'not_performed'
        self.assertEqual(check(s, extra=[dep]), 'unverified')

    def test_missing_witness_never_becomes_a_hard_rejection(self):
        self.assertEqual(check(witness(), include_source=False), 'unverified')

    def test_unrelated_compound_rule_does_not_block_proven_relation(self):
        s = witness(); s['value']['rules'].append({'kind': 'unsupported_unrelated_timing'})
        self.assertEqual(check(s), 'failed')

    def test_resource_fields_are_not_interchangeable(self):
        s = witness('resource_in_use'); r = {'kind': 'resource_in_use', 'resource': 'pulse_output_destination',
                            'owner': 'positioning_instruction', 'subject': {'operand': 'd'}, 'effects': {'PWM_output': 'not_executed'}}
        s['value']['rules'] = [r]
        self.assertEqual(derived(s, 'resource')['status'], 'source_verified')
        for key, value in [('resource', 'unrelated'), ('owner', 'unrelated'), ('subject', {'operand': 's1'})]:
            altered = copy.deepcopy(s); altered['value']['rules'][0][key] = value
            self.assertEqual(derived(altered, 'resource')['status'], 'declared_unverified')

    def test_mutated_source_rules_cannot_hard_reject(self):
        # A matching source owner activates live derivation; exercise the entire
        # graph, not just the status assigned to an isolated derived fact.
        identity = {'id': 'synthetic:identity', 'dimension': 'identity.applicability',
                    'status': 'source_verified', 'verification': 'source_checked',
                    'sources': [{'manual_number': 'JY997D55801', 'revision': 'AB'}],
                    'value': {'model_states': {'FX5U': 'supported'}}}
        variants = [
            {'kind': 'resource_in_use', 'resource': 'pulse_output_destination',
             'owner': 'positioning_instruction', 'subject': {'operand': 's1'}},
            {'kind': 'resource_in_use', 'resource': 'pulse_output_destination',
             'owner': 'unrelated', 'subject': {'operand': 'd'}},
            {'kind': 'operand_relation', 'left': {'operand': 's1'}, 'operator': '>',
             'right': {'operand': 's2'}, 'error': False},
            {'kind': 'PWM_minimum_OFF_time', 'ON_time': {'operand': 's2'},
             'period': {'operand': 's1'}, 'minimum_period_minus_ON_time_us': 3},
            {'kind': 'any_value_less_than', 'value': 1, 'logical_connective': 'OR',
             'subjects': [{'operand': 's1'}, {'operand': 's2'}], 'error': False},
            {'kind': 'PWM_channel_configuration', 'channel': {'operand': 's1'},
             'when': 'not_selected_for_PWM_in_parameters', 'execution': False},
            {'kind': 'conditional_result',
             'when': {'all': [{'operand': 'd', 'device_class': 'Y'},
                              {'parameter_unused_channel_available': False}]},
             'result': {'operation_error': False}}]
        c = context(); c['pwm']['resource_owner'] = 'positioning_instruction'
        c['pwm']['configured_for_PWM'] = False
        c['pwm']['unused_channel_available'] = False
        for rule in variants:
            source = witness(); source['value']['rules'] = [rule]
            report = check_fact_constraints(prerequisite_facts() + [identity, source],
                {'S1': 20, 'S2': 10, '$OPERANDS': ['K20', 'K10', 'Y0']}, target='PWM',
                plc_model='FX5U', language='ladder', context=c)
            self.assertEqual(report['status'], 'unverified', rule)
            self.assertFalse(any(r['status'] == 'failed' for r in report['checks']), rule)
        source = witness(); source['value']['rules'] = [{
            'kind': 'resource_in_use', 'resource': 'pulse_output_destination',
            'owner': 'positioning_instruction', 'subject': {'operand': 'd'}, 'effects': {'PWM_output': 'not_executed'}}]
        report = check_fact_constraints(prerequisite_facts() + [identity, source],
            {'S1': 10, 'S2': 20, '$OPERANDS': ['K10', 'K20', 'Y0']}, target='PWM',
            plc_model='FX5U', language='ladder', context=c)
        self.assertEqual(report['status'], 'failed')

    def test_destination_witness_requires_complete_source_shape(self):
        identity = {'id': 'synthetic:identity', 'dimension': 'identity.applicability',
                    'status': 'source_verified', 'verification': 'source_checked',
                    'sources': [{'manual_number': 'JY997D55801', 'revision': 'AB'}],
                    'value': {'model_states': {'FX5U': 'supported'}}}
        source = witness()
        source['value']['rules'] = [
            {'kind': 'allowed_interval', 'subject': {'operand': 'd'},
             'when': {'representation_any_of': ['word_device', 'constant'], 'hardware': 'CPU_module'},
             'quantity': 'channel_number', 'minimum': 1, 'maximum': 4, 'inclusive': True},
            {'kind': 'allowed_device_classes', 'subject': {'operand': 'd'},
             'when': {'representation': 'bit_device'}, 'allowed': ['Y'],
             'allowed_addresses': ['Y' + str(n) for n in range(8)], 'hardware': 'CPU_module'}]
        for destination, mutation in [('K5', 'hardware'), ('Y10', 'addresses')]:
            c = context()
            values = {'S1': 10, 'S2': 20, '$OPERANDS': ['K10', 'K20', destination]}
            def report(g):
                return check_fact_constraints(prerequisite_facts() + [identity, g], values, target='PWM',
                    plc_model='FX5U', language='ladder', context=c)
            self.assertEqual(report(source)['status'], 'failed')
            changed = copy.deepcopy(source)
            if mutation == 'hardware':
                changed['value']['rules'][0]['when']['hardware'] = 'unresolved_module'
            else:
                changed['value']['rules'][1]['allowed_addresses'].append('Y10')
            self.assertEqual(derived(changed, 'destination')['status'], 'declared_unverified')
            self.assertEqual(report(changed)['status'], 'unverified')

    def test_complete_rule_shapes_reject_extra_conditions_and_rebound_operands(self):
        relation = next(r for r in witness()['value']['rules'] if r['kind'] == 'operand_relation')
        resource = {'kind': 'resource_in_use', 'resource': 'pulse_output_destination',
                    'owner': 'positioning_instruction', 'subject': {'operand': 'd'}, 'effects': {'PWM_output': 'not_executed'}}
        hardware = {'kind': 'PWM_hardware_minimum_times', 'ON_time': {'operand': 's1'},
                    'period': {'operand': 's2'}, 'rows': list(PWM_AB_MINIMUM_TIMES),
                    'dash_cell': 'no_module_output_entry', 'unit_conversion': 'parameter_selected_time_to_us'}
        interval = {'kind': 'allowed_interval', 'minimum': 1, 'maximum': 65535,
                    'inclusive': True, 'lower_encoding': 'decimal', 'upper_encoding': 'decimal',
                    'subject': {'operand': 's1'}}
        for rule in (relation, resource, hardware, interval):
            self.assertTrue(_supported_pwm_rule_shape(rule))
            changed = copy.deepcopy(rule); changed['when'] = {'external_condition': True}
            self.assertFalse(_supported_pwm_rule_shape(changed))
        changed = copy.deepcopy(hardware); changed['ON_time'] = {'operand': 's2'}
        self.assertFalse(_supported_pwm_rule_shape(changed))
        changed = copy.deepcopy(interval); changed['inclusive'] = 1
        self.assertFalse(_supported_pwm_rule_shape(changed))
        changed = copy.deepcopy(relation); changed['error'] = 1
        self.assertFalse(_supported_pwm_rule_shape(changed))
        identity = {'id': 'synthetic:identity', 'dimension': 'identity.applicability',
                    'status': 'source_verified', 'verification': 'source_checked',
                    'sources': [{'manual_number': 'JY997D55801', 'revision': 'AB'}],
                    'value': {'model_states': {'FX5U': 'supported'}}}
        for rule in (relation, resource, hardware):
            changed = copy.deepcopy(rule); changed['when'] = {'external_condition': True}
            source = witness(); source['value']['rules'] = [changed]
            c = context(); c['pwm']['resource_owner'] = 'positioning_instruction'
            report = check_fact_constraints(prerequisite_facts() + [identity, source],
                {'S1': 20, 'S2': 10, '$OPERANDS': ['K20', 'K10', 'Y0']}, target='PWM',
                plc_model='FX5U', language='ladder', context=c)
            self.assertEqual(report['status'], 'unverified', rule['kind'])



if __name__ == '__main__':
    unittest.main()
