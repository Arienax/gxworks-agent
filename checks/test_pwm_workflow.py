# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Normal specification -> explicit automatic trace -> public bounded entry."""
import copy
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(pathlib.Path(__file__).resolve().parent)]
from plc.instruction_definition import DefinitionError
from plc.ir import build_plc_ir
from plc.runtime_semantics import control_runtime_facts, first_scan_fact
from plc.device_profiles import device_address_rules
from plc.target_support import normalize_target_cpu, require_native_program_target, TargetSupportRegistry
from gxworks2.project_profiles import native_project_profile
from plc.specification.approach import normalize_approach
from plc.specification.behavior import normalize_construction_plan
from plc.specification.confirmed import validate_spec_draft
from simulator.bounded import check_confirmed_behavior, finite_behavior_traces
from checks.test_pwm_execution import context, ladder


def specification(cpu='FX5U'):
    ctx = context()
    ctx['simulation'] = {'clock': {'kind': 'explicit_timestamps',
                                  'timestamps_ms': [0, 0.01, 0.1, 1, 2, 3, 4, 5]},
                         'initial_memory': {'SM8034': False}}
    expr = {'op': 'device', 'name': 'X0', 'type': {'kind': 'bool'}}
    raw = {'plc_model': cpu, 'selected_approach': {'name': 'Explicit PWM software model',
           'construction_plan': {'execution_context': ctx}},
           'behavior_constraints': [{'id': 'input_identity', 'kind': 'assertion', 'status': 'confirmed',
               'provenance': {'source': 'user_authored', 'evidence': ['Explicit simulation inputs']},
               'when': {'op': 'constant', 'value': True, 'type': {'kind': 'bool'}},
               'predicate': {'op': 'eq', 'type': {'kind': 'bool'}, 'args': [expr, expr]}}]}
    raw['selected_approach'] = normalize_approach(raw['selected_approach'])
    return raw


class PWMWorkflow(unittest.TestCase):
    def test_normal_specification_public_entry_all_four_cpus(self):
        for cpu in ('FX5S', 'FX5UJ', 'FX5U', 'FX5UC'):
            with self.subTest(cpu=cpu):
                spec = specification(cpu)
                self.assertFalse(validate_spec_draft(spec)['errors'])
                program = build_plc_ir({**ladder(), 'device_comments': {}}, plc_model=cpu)
                report = check_confirmed_behavior(program, spec)
                self.assertFalse(report['checker_exceptions'])
                self.assertNotIn('missing_cpu_control_facts', str(report['unsupported_or_missing']))
                observations = report['peripheral_observations']
                self.assertTrue(observations)
                states = [row['peripherals']['pwm']['channels'].get(1) for row in observations]
                self.assertTrue(any(s and s['command_state'] == 'run_commanded' for s in states))
                self.assertTrue(all(s is None or s['physical_level'] is None for s in states))
                self.assertEqual(report['default_simulation_assumptions'],
                                 spec['selected_approach']['construction_plan']['execution_context']['simulation'])

    def test_explicit_nonuniform_time_is_preserved_not_scan_count(self):
        spec = specification()
        trace = next(t for t in finite_behavior_traces(spec) if len(t['frames']) == 8)
        self.assertEqual([f['timestamp_ms'] for f in trace['frames']], [0, 0.01, 0.1, 1, 2, 3, 4, 5])
        self.assertEqual(trace['simulation_assumptions']['time_source'], 'explicit_timestamps')
        self.assertIs(trace['initial']['SM8034'], False)

    def test_missing_clock_does_not_infer_timestamps(self):
        spec = specification()
        del spec['selected_approach']['construction_plan']['execution_context']['simulation']['clock']
        self.assertTrue(all('timestamp_ms' not in f for t in finite_behavior_traces(spec) for f in t['frames']))
        report = check_confirmed_behavior(ladder(), spec)
        self.assertIn('pwm_explicit_clock_required', str(report['unsupported_or_missing']))
        self.assertTrue(all(not row['peripherals']['pwm']['channels'] for row in report['peripheral_observations']))

    def test_short_clock_does_not_truncate_test_sequences_or_extend_time(self):
        spec = specification()
        spec['selected_approach']['construction_plan']['execution_context']['simulation']['clock']['timestamps_ms'] = [0]
        trace = next(t for t in finite_behavior_traces(spec) if len(t['frames']) == 8)
        self.assertEqual(trace['frames'][0]['timestamp_ms'], 0)
        self.assertTrue(all('timestamp_ms' not in f for f in trace['frames'][1:]))
        report = check_confirmed_behavior(ladder(), spec)
        self.assertEqual(report['execution_status'], 'unverified')
        self.assertGreater(report['coverage']['frames_without_explicit_clock'], 0)
        self.assertEqual(report['coverage']['maximum_scans'], 8)
        self.assertEqual(report['checks'][0]['status'], 'no_violation_found_in_tested_scope')
        self.assertIn('pwm_explicit_clock_required', str(report['unsupported_or_missing']))

    def test_unknown_initial_stop_bit_not_defaulted_to_zero(self):
        spec = specification()
        del spec['selected_approach']['construction_plan']['execution_context']['simulation']['initial_memory']
        report = check_confirmed_behavior(ladder(), spec)
        self.assertIn('stop_bit_unknown', str(report['unsupported_or_missing']))
        self.assertTrue(all(not row['peripherals']['pwm']['channels'] for row in report['peripheral_observations']))

    def test_configuration_remains_typed_and_partial(self):
        ctx = context()
        partial = normalize_construction_plan({'execution_context': {'pwm': {'channel': 1}}})
        self.assertEqual(partial['execution_context'], {'pwm': {'channel': 1}})
        for field, bad in [('channel', True), ('pulse_count', 0.0), ('configured_for_PWM', 'yes'),
                           ('resource_owner', 4), ('destination_value', 1), ('source_binding', {})]:
            bad_ctx = copy.deepcopy(ctx); bad_ctx['pwm'][field] = bad
            with self.subTest(field=field), self.assertRaises(DefinitionError):
                normalize_construction_plan({'execution_context': bad_ctx})
        c = copy.deepcopy(ctx); c['language'] = 'sfc'
        with self.assertRaises(DefinitionError):
            normalize_construction_plan({'execution_context': c})

    def test_cpu_and_software_versions_remain_distinct(self):
        ctx = {'cpu_version': '1.050', 'software_version': 'GXW3-1.105K'}
        self.assertEqual(normalize_construction_plan({'execution_context': ctx})['execution_context'], ctx)
        with self.assertRaises(DefinitionError):
            normalize_construction_plan({'execution_context': {'version': '1.050'}})

    def test_initial_memory_has_typed_values_and_no_alias_collisions(self):
        for memory in ({'SM8034': 2}, {'D0': True}, {'SD5302': 65536},
                       {'D0': -32769}, {'SM8034': False, 'sm08034': True}):
            with self.subTest(memory=memory), self.assertRaises(DefinitionError):
                normalize_construction_plan({'execution_context': {'simulation': {'initial_memory': memory}}})

    def test_invalid_hardware_is_not_a_successful_execution(self):
        spec = specification()
        spec['selected_approach']['construction_plan']['execution_context']['hardware']['output_type'] = 'relay'
        report = check_confirmed_behavior(ladder(), spec)
        self.assertEqual(report['execution_status'], 'unverified')
        self.assertTrue(report['unsupported_or_missing'])
        self.assertTrue(all(not row['peripherals']['pwm']['channels'] for row in report['peripheral_observations']))

    def test_bad_clock_values_are_rejected_by_normal_spec_schema(self):
        for times in ([True], [float('nan')], [float('inf')], [-1], [2, 1], ['1'], [10**400], [], list(range(9))):
            ctx = context(); ctx['simulation'] = {'clock': {'kind': 'explicit_timestamps', 'timestamps_ms': times}}
            with self.subTest(times=str(times)), self.assertRaises(DefinitionError):
                normalize_construction_plan({'execution_context': ctx})

    def test_core_identities_do_not_grant_native_gxworks2_support(self):
        for cpu in ('FX5S', 'FX5UJ', 'FX5U', 'FX5UC'):
            self.assertEqual(normalize_target_cpu(cpu), cpu)
            self.assertIsNone(native_project_profile(cpu))
            with self.assertRaises(ValueError):
                require_native_program_target(cpu)
            target = TargetSupportRegistry().target(cpu)
            self.assertEqual(target['status'], 'unknown')
            self.assertEqual(target['operations'], {})
            live = TargetSupportRegistry().live_capabilities(cpu, software_version='1.610L', native_ui_language='en')
            self.assertTrue(all(v['status'] == 'unverified' for v in live.values()))
        with self.assertRaises(ValueError):
            normalize_target_cpu('FX5_UNKNOWN')

    def test_scoped_cpu_control_does_not_inherit_unreviewed_fx5u_runtime(self):
        for cpu in ('FX5S', 'FX5UJ', 'FX5UC'):
            facts = control_runtime_facts(cpu)
            self.assertIn('LD', facts['control']['forms'])
            self.assertIn('OUT', facts['control']['forms'])
            self.assertFalse(set(facts) & {'timers', 'counter', 'first_scan', 'always_on', 'transfers'})
            self.assertNotIn('MOV', facts['control']['forms'])
            self.assertNotIn('ZRST', facts['control']['forms'])
            self.assertFalse(first_scan_fact(cpu, {}).get('available'))
            self.assertEqual(device_address_rules(cpu)['status'], 'unavailable')
        self.assertFalse(control_runtime_facts('FX5_UNKNOWN'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
