"""Counterexamples for PWM input identity and bounded uncertainty footprints."""
import copy
import pathlib
import sys
import unittest
sys.path[:0] = [str(pathlib.Path(__file__).resolve().parents[1] / 'src'), str(pathlib.Path(__file__).resolve().parent)]
from test_pwm_execution import context, machine, scan
import test_pwm_execution as execution_fixtures


class PWMPeripheralCorrectness(unittest.TestCase):
    def test_ladder_language_is_not_caller_controlled(self):
        for language in ('sfc', 'unknown', '', None):
            ctx = context(); ctx['language'] = language
            m = machine(['K20', 'K10', 'Y0'], ctx=ctx)
            scan(m, 0)
            self.assertFalse(m.pwm.channels)
            self.assertTrue(m.constraint_violations)
            m = machine(ctx=ctx)
            scan(m, 0)
            self.assertEqual(m.pwm.channels[1]['command_state'], 'run_commanded')

    def test_leading_zero_writer_aliases_conflict_before_commit(self):
        for address in ('Y0', 'Y000', 'y000', 'SD5302', 'SD05302', 'SM05300'):
            other = ({'type': 'COIL', 'address': address} if address.upper().startswith('Y') else
                     {'type': 'APP_INSTR', 'opcode': 'MOV', 'operands': ['K1', address]})
            m = machine(second=other)
            scan(m, 0)
            self.assertFalse(m.pwm.channels, address)
            self.assertTrue(any('pwm_resource_conflict' in gap['reason'] for gap in m.gaps), address)

    def test_multiword_writer_overlapping_reserved_region_is_rejected(self):
        for address in ('SD5301', 'SD05301'):
            m = machine(second={'type': 'APP_INSTR', 'opcode': 'DMOV',
                                'operands': ['K1', address]})
            scan(m, 0)
            self.assertFalse(m.pwm.channels, address)
            self.assertTrue(any('pwm_resource_conflict' in gap['reason'] or
                                'pwm_peer_write_scope_unverified' in gap['reason']
                                for gap in m.gaps), address)

    def test_known_failed_binding_preserves_unrelated_memory(self):
        for initial in ({'X0': None}, {'SM8034': None}):
            m = machine(initial={**initial, 'Y7': True, 'SD5478': 123,
                                 'SD5318': 456, 'SM5301': True, 'SD5302': 999})
            scan(m, 0)
            for address, expected in [('Y7', True), ('SD5478', 123), ('SD5318', 456), ('SM5301', True)]:
                self.assertEqual(m.memory[address], expected, address)
            self.assertIsNone(m.memory['SD5302'])
            self.assertIsNone(m.memory['Y0'])

    def test_one_failed_owner_does_not_poison_independent_observation(self):
        m = execution_fixtures.PWMExecution().two_channel_machine()
        scan(m, 0)
        result = scan(m, 1, X1=None)
        states = result['peripherals']['pwm']['channels']
        self.assertEqual(states[1]['command_state'], 'run_commanded')
        self.assertEqual(states[2]['command_state'], 'unknown')
        self.assertEqual(m.memory['SD5302'], 10)
        self.assertIsNone(m.memory['SD5318'])

    def test_failed_migration_invalidates_old_and_candidate_binding_only(self):
        m = machine(initial={'Y7': True, 'SD5478': 123, 'SD5318': 99})
        scan(m, 0)
        prior = copy.deepcopy(m.pwm.channels)
        m.execution_context['pwm']['channel'] = 2
        scan(m, 1)
        self.assertEqual(m.pwm.channels, prior)
        self.assertIsNone(m.memory['SD5302'])
        self.assertIsNone(m.memory['SD5318'])
        self.assertEqual(m.memory['SD5478'], 123)
        self.assertTrue(m.memory['Y7'])

    def test_unresolved_binding_is_bounded_to_CPU_channels(self):
        ctx = context(); ctx['pwm']['bindings'] = {}
        m = machine(ctx=ctx, initial={'SD5478': 123, 'SD5366': 456})
        scan(m, 0)
        self.assertFalse(m.pwm.channels)
        self.assertEqual(m.memory['SD5478'], 123)
        self.assertEqual(m.memory['SD5366'], 456)  # CH5 belongs to unsupported external hardware.
        self.assertIsNone(m.memory['SD5302'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
