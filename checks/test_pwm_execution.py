"""Execute the actual production ScanMachine dispatcher, never a toy engine.

These checks exercise an explicitly opt-in bounded software peripheral contract.
They neither validate physical pulse timing nor claim native FX5 simulation.
"""
import copy
import json
import pathlib
import unittest
import sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from simulator.bounded import ScanMachine, check_bounded_traces
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
from plc.pwm_contract import SOURCE_BINDING
RECEIPTS = []


def context():
    return {'program_context': 'ladder', 'hardware': {'module_type': 'CPU_module', 'output_type': 'transistor'},
            'pwm': {'execution_mode': 'core_software_model', 'time_unit': 'us',
                    'configured_for_PWM': True, 'unused_channel_available': True,
                    'resource_owner': None, 'output_device': 'Y0', 'channel': 1,
                    'initially_idle': True, 'pulse_count': 0, 'source_binding': SOURCE_BINDING}}


def ladder(args=None, second=None, opcode='PWM'):
    outputs = [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': args or ['K10', 'K20', 'Y0']}]
    if second:
        outputs.append(second)
    return {'rungs': [{'rung_id': 1, 'branches': [{'inputs': [{'type': 'NO', 'address': 'X0'}], 'outputs': outputs}]}]}


def machine(args=None, ctx=None, second=None, initial=None, model='FX5U', opcode='PWM'):
    return ScanMachine(ladder(args, second, opcode), plc_model=model,
                       initial={'X0': True, 'SM8034': False, 'Y0': False, **(initial or {})},
                       execution_context=context() if ctx is None else ctx)


def scan(m, t, **inputs):
    result = m.scan({'timestamp_ms': t, 'inputs': inputs}, first_scan=m.scan_index == 0)
    RECEIPTS.append({'scan': m.scan_index - 1, 'observation': result, 'gaps': copy.deepcopy(m.gaps)})
    return result


class PWMExecution(unittest.TestCase):
    def test_actual_dispatch_and_storage_without_fake_waveform(self):
        m = machine()
        for t in (0, 0.001, 1, 100):
            o = scan(m, t)
            state = o['peripherals']['pwm']['channels'][1]
            self.assertEqual(state['command_state'], 'run_commanded')
            self.assertEqual(state['generation'], 1)
            self.assertEqual(state['nominal_frequency_hz'], 50000)
            self.assertIsNone(o['after']['Y0'])
            self.assertEqual(o['after']['SD5302'], 10)
            self.assertEqual(o['after']['SD5303'], 0)
            self.assertEqual(o['after']['SD5304'], 20)
            self.assertIsNone(state['physical_pulse_count'])
            self.assertEqual(state['hardware_effect'], 'not_tested')
            self.assertEqual(state['full_graph_admission'], 'unverified')
        self.assertEqual(len(m.pwm.events), 1)

    def test_stop_restart_and_drive_disable(self):
        m = machine()
        states = []
        for t, inputs in enumerate(({}, {'SM8034': True}, {}, {'SM8034': False}, {'X0': False}, {'X0': True})):
            states.append(scan(m, t, **inputs)['peripherals']['pwm']['channels'][1])
        self.assertEqual([s['command_state'] for s in states], [
            'run_commanded', 'forced_stop_commanded', 'forced_stop_commanded',
            'run_commanded', 'drive_disabled', 'run_commanded'])
        self.assertEqual([s['generation'] for s in states], [1, 1, 1, 2, 2, 3])
        self.assertTrue(all(s['stop_latency_us'] is None and s['physical_level'] is None for s in states))

    def test_clock_is_explicit_and_not_scan_count(self):
        m = machine()
        scan(m, 0)
        before = copy.deepcopy(m.pwm.channels)
        m.scan({'inputs': {}})
        self.assertEqual(m.pwm.channels, before)
        self.assertTrue(any(g['reason'] == 'pwm_explicit_clock_required' for g in m.gaps))
        for bad in (float('nan'), float('inf')):
            m = machine()
            scan(m, bad)
            self.assertEqual(m.pwm.channels, {})

    def test_missing_stop_state_rejects_without_channel_commit(self):
        m = machine(initial={'SM8034': None})
        scan(m, 0)
        self.assertEqual(m.pwm.channels, {})
        self.assertTrue(any('stop_bit_unknown' in g['reason'] for g in m.gaps))

    def test_dynamic_update_rejected_atomically(self):
        m = machine(['D0', 'D1', 'Y0'], initial={'D0': 10, 'D1': 20})
        scan(m, 0)
        before = copy.deepcopy(m.pwm.channels)
        scan(m, 1, D0=12)
        self.assertEqual(m.pwm.channels, before)
        self.assertIsNone(m.memory['SD5302'])
        self.assertTrue(any(g['reason'] == 'pwm_dynamic_update_unverified' for g in m.gaps))

    def test_channel_and_unit_reconfiguration_rejected(self):
        m = machine(); scan(m, 0)
        m.execution_context['pwm']['channel'] = 2
        o = scan(m, 1)
        self.assertEqual(set(m.pwm.channels), {1})
        self.assertEqual(o['peripherals']['pwm']['channels'][1]['command_state'], 'unknown')
        m = machine(); scan(m, 0)
        m.execution_context['pwm']['time_unit'] = 'ms'
        scan(m, 1)
        self.assertEqual(m.pwm.channels[1]['request']['time_unit'], 'us')
        self.assertEqual(m.pwm.last_attempt['status'], 'unverified')

    def test_external_ownership_and_missing_context(self):
        for key in set(context()['pwm']) - {'output_device'}:
            ctx = context(); del ctx['pwm'][key]
            m = machine(ctx=ctx); scan(m, 0)
            self.assertEqual(m.pwm.channels, {}, key)
        for owner in ('positioning_instruction', 'other_pwm'):
            ctx = context(); ctx['pwm']['resource_owner'] = owner
            m = machine(ctx=ctx); scan(m, 0)
            self.assertEqual(m.pwm.channels, {})

    def test_competing_writer_rejected_before_commit(self):
        for other in ({'type': 'APP_INSTR', 'opcode': 'PWM', 'operands': ['K10', 'K20', 'Y0']},
                      {'type': 'APP_INSTR', 'opcode': 'MOV', 'operands': ['K1', 'SD5300']}):
            m = machine(second=other)
            scan(m, 0)
            self.assertEqual(m.pwm.channels, {})
            self.assertTrue(any('resource_conflict' in g['reason'] for g in m.gaps))

    def test_finite_count_not_silently_treated_as_unlimited(self):
        ctx = context(); ctx['pwm']['pulse_count'] = 3
        m = machine(ctx=ctx); scan(m, 0)
        self.assertEqual(m.pwm.channels, {})
        m = machine(initial={'SD5300': 3, 'SD5301': 0}); scan(m, 0)
        self.assertEqual(m.pwm.channels, {})

    def test_baseline_unknown_path_retained(self):
        m = machine(ctx={}); o = scan(m, 0)
        self.assertEqual(o['peripherals'], {})
        self.assertTrue(m.gaps)
        self.assertIsNone(o['after']['Y0'])

    def test_malformed_context_does_not_crash(self):
        for malformed in (None, [], 'core_software_model', 1):
            m = machine(ctx={'pwm': malformed})
            o = scan(m, 0)
            self.assertEqual(o['peripherals'], {})
            self.assertEqual(m.pwm.channels, {})

    def test_dpwm_constants_use_actual_dispatch_and_32bit_storage(self):
        m = machine(['K65536', 'K131072', 'Y0'], opcode='DPWM')
        o = scan(m, 0)
        self.assertEqual(o['peripherals']['pwm']['channels'][1]['command_state'], 'run_commanded')
        self.assertEqual([o['after'][key] for key in ('SD5302', 'SD5303', 'SD5304', 'SD5305')], [0, 1, 0, 2])
        self.assertIsNone(o['after']['Y0'])
        m = machine(['K65536', 'K131072', 'D2'], opcode='DPWM', initial={'D2': 1})
        scan(m, 0)
        self.assertIn(1, m.pwm.channels)  # destination stays 16-bit, D3 is not required

    def test_dpwm_multiword_binding_uses_existing_device_region(self):
        m = machine(['D0', 'K131072', 'Y0'], opcode='DPWM', initial={'D0': 65535, 'D1': 0})
        scan(m, 0)
        self.assertEqual(m.pwm.channels[1]['request']['on_time'], 65535)
        self.assertFalse(m.constraint_violations)  # not falsely classified as negative
        for missing in ({'D0': 65535}, {'D0': 65535, 'D1': False}):
            m = machine(['D0', 'K131072', 'Y0'], opcode='DPWM', initial=missing)
            scan(m, 0)
            self.assertEqual(m.pwm.channels, {})
        m = machine(['SD5302', 'K131072', 'Y0'], opcode='DPWM', initial={'SD5302': 65535, 'SD5303': 0})
        scan(m, 0)
        self.assertEqual(m.pwm.channels, {})
        self.assertTrue(any('word_device_region_unverified:SD5302' in g['reason'] for g in m.gaps))

    def test_real_word_operands_and_word_destination(self):
        m = machine(['D0', 'D1', 'D2'], initial={'D0': 10, 'D1': 20, 'D2': 1})
        scan(m, 0)
        self.assertEqual(m.pwm.channels[1]['request']['on_time'], 10)
        ctx = context(); ctx['pwm']['destination_value'] = 1
        m = machine(['D0', 'D1', 'D2'], ctx=ctx, initial={'D0': 10, 'D1': 20})
        scan(m, 0)
        self.assertEqual(m.pwm.channels, {})  # must not trust forged resolved operand
        m = machine(['D0', 'D1', 'K1'], initial={'D0': 65534, 'D1': 65535})
        scan(m, 0)
        self.assertEqual(m.pwm.channels, {})  # source OFF-time minimum, no integer wrap

    def test_supported_models_and_source_conflict(self):
        for model in ('FX5U', 'FX5UC', 'FX5UJ'):
            m = machine(model=model); scan(m, 0)
            self.assertIn(1, m.pwm.channels)
        m = machine(['K2', 'K5', 'Y0'], model='FX5S'); scan(m, 0)
        self.assertEqual(m.pwm.channels, {})
        ctx = context(); ctx['pwm']['output_device'] = 'Y3'
        m = machine(['K200', 'K400', 'Y3'], model='FX5S', ctx=ctx); scan(m, 0)
        self.assertIn(1, m.pwm.channels)

    def test_additional_source_ambiguities_remain_unknown(self):
        ctx = context(); ctx['pwm']['output_device'] = 'Y3'
        m = machine(['K10', 'K20', 'Y3'], model='FX5S', ctx=ctx); scan(m, 0)
        self.assertEqual(m.pwm.channels, {})  # AG fast Y3 versus AB slow Y3
        ctx = context(); ctx['pwm']['time_unit'] = 'ms'
        m = machine(['K100000000', 'K200000000', 'Y0'], opcode='DPWM', ctx=ctx); scan(m, 0)
        self.assertEqual(m.pwm.channels, {})  # ambiguous converted hardware time limit

    def test_units_and_boundary_do_not_invent_stop_priority(self):
        ctx = context(); ctx['pwm']['time_unit'] = 'ms'
        m = machine(['K1', 'K2', 'Y0'], ctx=ctx); scan(m, 0)
        self.assertEqual(m.pwm.channels[1]['request']['period_us'], 2000)
        m = machine(['K5', 'K10', 'Y0']); scan(m, 0)
        o = scan(m, 0.005, SM8034=True)
        self.assertIsNone(o['peripherals']['pwm']['channels'][1]['stop_latency_us'])
        self.assertIsNone(o['after']['Y0'])

    def test_bad_count_word_types_are_not_accepted(self):
        for value in (False, 0.0, None, -1):
            m = machine(initial={'SD5300': value, 'SD5301': 0}); scan(m, 0)
            self.assertEqual(m.pwm.channels, {})

    def test_monitor_initial_values_never_survive_as_evidence(self):
        monitors = {'SM5300': False, 'SM5316': False, 'SM5332': False, 'SD5306': 123, 'SD5307': 0}
        for stop in (False, True):
            m = machine(initial={**monitors, 'SM8034': stop})
            o = scan(m, 0)
            for key in monitors:
                self.assertIsNone(o['after'][key])
        m = machine(initial=monitors)
        scan(m, 0)
        o = scan(m, 1, X0=None)
        self.assertEqual(o['peripherals']['pwm']['channels'][1]['command_state'], 'unknown')
        for key in monitors:
            self.assertIsNone(o['after'][key])

    def test_late_stop_write_invalidates_current_observation(self):
        m = machine(second={'type': 'APP_INSTR', 'opcode': 'MOV', 'operands': ['K1', 'SM8034']})
        o = scan(m, 0)
        self.assertEqual(o['peripherals']['pwm']['channels'][1]['command_state'], 'unknown')

    def two_channel_machine(self, *, second_args=None, second_channel=2, second_output='Y1', late_stop=False):
        program = ladder()
        second = ladder(second_args or ['K15', 'K30', second_output])['rungs'][0]
        second['rung_id'] = 2
        second['branches'][0]['inputs'][0]['address'] = 'X1'
        program['rungs'].append(second)
        if late_stop:
            program['rungs'].append({'rung_id': 3, 'branches': [{'inputs': [], 'outputs': [
                {'type': 'APP_INSTR', 'opcode': 'MOV', 'operands': ['K1', 'SM8034']}]}]})
        ctx = context()
        ctx['pwm']['bindings'] = {'0:branches[0].outputs[0]': {'channel': 1, 'output_device': 'Y0'},
                                 '1:branches[0].outputs[0]': {'channel': second_channel, 'output_device': second_output}}
        return ScanMachine(program, plc_model='FX5U', initial={'X0': True, 'X1': True, 'SM8034': False}, execution_context=ctx)

    def test_two_distinct_channels_have_independent_owners_and_drives(self):
        m = self.two_channel_machine()
        o = scan(m, 0)
        states = o['peripherals']['pwm']['channels']
        self.assertEqual(set(states), {1, 2})
        self.assertTrue(all(s['command_state'] == 'run_commanded' for s in states.values()))
        self.assertEqual(len(o['peripherals']['pwm']['instruction_bindings']), 2)
        o = scan(m, 1, X0=False)
        self.assertEqual(o['peripherals']['pwm']['channels'][1]['command_state'], 'drive_disabled')
        self.assertEqual(o['peripherals']['pwm']['channels'][2]['command_state'], 'run_commanded')
        self.assertEqual(o['peripherals']['pwm']['channels'][2]['generation'], 1)
        o = scan(m, 2, SM8034=True)
        self.assertEqual(o['peripherals']['pwm']['channels'][2]['command_state'], 'forced_stop_commanded')

    def test_multichannel_conflict_or_bad_peer_prevents_all_initial_commits(self):
        for kwargs in ({'second_channel': 1}, {'second_output': 'Y0'}, {'second_args': ['D9', 'K30', 'Y1']}):
            m = self.two_channel_machine(**kwargs)
            scan(m, 0)
            self.assertEqual(m.pwm.channels, {})
        m = self.two_channel_machine(second_args=['D9', 'K30', 'Y1'])
        scan(m, 0, X1=False)
        self.assertEqual(m.pwm.channels, {})  # disabled unknown peer is not guessed
        m = self.two_channel_machine()
        del m.execution_context['pwm']['bindings']['1:branches[0].outputs[0]']
        scan(m, 0)
        self.assertEqual(m.pwm.channels, {})

    def test_late_global_stop_invalidates_every_channel_observation(self):
        m = self.two_channel_machine(late_stop=True)
        o = scan(m, 0)
        self.assertEqual(set(o['peripherals']['pwm']['channels']), {1, 2})
        self.assertTrue(all(s['command_state'] == 'unknown' for s in o['peripherals']['pwm']['channels'].values()))

    def test_one_failed_owner_preserves_unrelated_multichannel_observation(self):
        m = self.two_channel_machine(); scan(m, 0)
        o = scan(m, 1, X1=None)
        self.assertEqual(o['peripherals']['pwm']['channels'][1]['command_state'], 'run_commanded')
        self.assertEqual(o['peripherals']['pwm']['channels'][2]['command_state'], 'unknown')
        self.assertEqual(o['peripherals']['pwm']['owner_attempts']['1:branches[0].outputs[0]']['status'], 'unverified')

    def test_unrelated_production_mov_behavior_is_preserved(self):
        instruction = {'type': 'APP_INSTR', 'opcode': 'MOV', 'operands': ['K7', 'D10']}
        for ctx in ({}, context()):
            m = machine(ctx=ctx, second=instruction)
            o = scan(m, 0)
            self.assertEqual(o['after']['D10'], 7)
            self.assertFalse(any(g['reason'] == 'constraint_unverified:MOV' for g in m.gaps))

    def test_actual_ir_public_entry(self):
        from plc.ir import build_plc_ir
        program = build_plc_ir({**ladder(), 'device_comments': {}}, plc_model='FX5U')
        trace = {'initial': {'X0': True, 'SM8034': False}, 'execution_context': context(),
                 'frames': [{'inputs': {}, 'timestamp_ms': 0}]}
        report = check_bounded_traces(program, {'plc_model': 'FX5U'}, [trace])
        self.assertFalse(report['checker_exceptions'])
        self.assertEqual(report['peripheral_observations'][0]['peripherals']['pwm']['channels'][1]['command_state'], 'run_commanded')
        self.assertEqual(report['status'], 'unverified')

    def test_st_route_remains_unverified_without_false_execution(self):
        for op in ('PWM', 'DPWM'):
            trace = {'initial': {'SM8034': False}, 'execution_context': context(),
                     'frames': [{'inputs': {}, 'timestamp_ms': 0}]}
            report = check_bounded_traces({'st_code': op + '(TRUE,10,20,Y0);'}, {'plc_model': 'FX5U'}, [trace])
            self.assertEqual(report['status'], 'unverified')
            self.assertFalse(report['checker_exceptions'])
            self.assertTrue(all(not row['peripherals']['pwm']['channels'] for row in report.get('peripheral_observations', [])))
            self.assertTrue(report['unsupported_or_missing'])

    def test_real_bounded_report_does_not_verify_electrical_output(self):
        trace = {'initial': {'X0': True, 'SM8034': False, 'Y0': False},
                 'execution_context': context(), 'frames': [{'inputs': {}, 'timestamp_ms': 0}],
                 'expectations': [{'scan': 0, 'requirement_id': 'physical_y',
                                   'predicate': {'op': 'device', 'name': 'Y0', 'type': {'kind': 'bool'}}}]}
        report = check_bounded_traces(ladder(), {'plc_model': 'FX5U'}, [trace])
        self.assertEqual(report['status'], 'unverified')
        self.assertTrue(report['peripheral_observations'])
        self.assertEqual(report['peripheral_observations'][0]['peripherals']['pwm']['channels'][1]['scope'], 'bounded_core_software_model')
        self.assertFalse(report['checker_exceptions'])
        self.assertFalse(report['violations'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
