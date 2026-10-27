# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Source compiler and live-registry adversarial regressions, public data only."""
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from plc import pwm_fact_source as source
from plc.instruction_constraints import check_fact_constraints
from plc.instruction_definition import UnknownInstructionSemantics
from plc.pwm_contract import validate_pwm_request, pwm_formal_facts
from checks.test_pwm_contract import context


class Registry:
    def __init__(self, facts=None, present=True, current=True, **metadata):
        self.facts, self.present, self.current = facts, present, current
        self.metadata = metadata
    def definition_entry(self, *args, **kwargs):
        return {'facts': self.facts, **self.metadata} if self.present else None
    def fact_bundle_current(self):
        return self.current


def run(facts, **kwargs):
    with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY', Registry(facts, **kwargs)):
        return validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', context())


class PWMSourcePipeline(unittest.TestCase):
    def test_reproducible_build(self):
        authored, generated = source.load_contract()
        self.assertEqual(source.compile_source(authored), generated)
        self.assertEqual(generated['source_sha256'], source.digest(authored))

    def test_compiler_reads_values_not_core_constant(self):
        authored = copy.deepcopy(source.load_contract()[0])
        for owner in authored['owners']:
            for fact in owner['facts']:
                for rule in fact['value'].get('rules', []):
                    if rule['kind'] == 'PWM_minimum_OFF_time':
                        rule['minimum_period_minus_ON_time_us'] = 7
        result = source.compile_source(authored)
        self.assertEqual(result['profiles']['PWM:FX5U']['predicates']['off_time']['args'][1]['value'], 7)
        self.assertNotEqual(result['binding'], source.source_binding())


    def test_missing_source_is_unknown_and_import_is_lazy(self):
        import plc.pwm_contract as contract
        with tempfile.TemporaryDirectory() as d, patch.object(source, 'GENERATED_PATH', Path(d) / 'missing'):
            importlib.reload(contract)  # No unrelated import failure.
            with self.assertRaises(UnknownInstructionSemantics):
                validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', context())

    def test_source_or_generated_drift_is_unknown(self):
        for key in ('GENERATED_PATH',):
            with tempfile.TemporaryDirectory() as d:
                original = json.loads(getattr(source, key).read_text())
                original['unreviewed_change'] = True
                path = Path(d) / 'drift.json'; path.write_text(json.dumps(original))
                with patch.object(source, key, path), self.assertRaises(UnknownInstructionSemantics):
                    source.load_contract()

    def test_snapshot_is_explicit_and_bounded(self):
        result = run(None, present=False)
        self.assertEqual(result['constraint_report']['source_mode'], 'version_bound_snapshot')
        self.assertEqual(result['whole_graph_admission'], 'not_established')

    def test_live_owner_is_used(self):
        result = run(source.snapshot_facts('PWM', 'FX5U'))
        self.assertEqual(result['constraint_report']['source_mode'], 'live_owner')
        self.assertEqual(len(result['constraint_report']['root_selection']), 11)
        self.assertEqual(result['constraint_report']['coverage'], 'selected_roots_only')

    def test_catalogue_skeleton_does_not_replace_the_reviewed_contract(self):
        facts = [{'id': 'registry:identity.form', 'dimension': 'identity.form',
                  'status': 'declared_unverified', 'verification': 'not_performed',
                  'scope': {'forms': ['PWM'], 'models': ['FX5U']},
                  'sources': [{'opcode': 'PWM', 'target_model': 'FX5U'}], 'value': {}}]
        metadata = {'support_status': 'existing_declared_scope',
                    'call_available': True, 'registry_member': True, 'conflicts': []}
        result = run(facts, **metadata)
        self.assertEqual(result['constraint_report']['source_mode'], 'version_bound_snapshot_with_catalogue')
        self.assertEqual(result['constraint_report']['status'], 'passed')
        for changed in ({'call_available': False}, {'conflicts': ['withdrawn']},
                        {'support_status': 'not_applicable'}):
            with self.subTest(changed=changed), self.assertRaises(UnknownInstructionSemantics):
                run(facts, **{**metadata, **changed})
        facts[0]['id'] = 'reviewed:withdrawn'
        with self.assertRaises(UnknownInstructionSemantics):
            run(facts, **metadata)

    def test_empty_or_stale_owner_never_falls_back(self):
        for registry in (Registry([]), Registry(None, present=False, current=False)):
            with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY', registry), self.assertRaises(UnknownInstructionSemantics):
                validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', context())

    def test_withdrawn_or_changed_source_is_unknown(self):
        for mutation in ('status', 'sources', 'scope', 'depends_on', 'conditions', 'missing'):
            facts = source.snapshot_facts('PWM', 'FX5U')
            off = next(f for f in facts if any(r['kind'] == 'PWM_minimum_OFF_time' for r in f['value'].get('rules', [])))
            if mutation == 'status': off['status'] = 'declared_unverified'
            elif mutation == 'sources': off['sources'][0]['revision'] = 'OTHER'
            elif mutation == 'scope': off['scope'].pop('languages')
            elif mutation == 'depends_on': off['depends_on'] = []
            elif mutation == 'conditions': off.pop('conditions')
            else: facts.remove(off)
            with self.subTest(mutation=mutation), self.assertRaises(UnknownInstructionSemantics):
                run(facts)

    def test_runtime_projection_keeps_source_identity(self):
        from knowledge.fact_corpus import project_definition
        facts = source.snapshot_facts('PWM', 'FX5U')
        projected, dropped = project_definition({'vendor': 'mitsubishi', 'opcode': 'PWM',
            'target_model': 'FX5U', 'facts': facts})
        self.assertFalse(dropped)
        self.assertEqual(run(projected['facts'])['constraint_report']['status'], 'passed')

    def test_conditional_or_rescoped_AG_cannot_be_ignored(self):
        for mutation in ('conditions', 'scope'):
            authored = copy.deepcopy(source.load_contract()[0])
            ag = authored['supplemental_facts'][0]
            if mutation == 'conditions': ag['conditions'] = [{'context': 'extra', 'equals': True}]
            else: ag['scope']['models'] = ['FX5U']
            with self.assertRaises(ValueError): source.compile_source(authored)

    def test_dependency_withdrawal_is_unknown(self):
        facts = source.snapshot_facts('PWM', 'FX5U')
        facts = [f for f in facts if f['dimension'] != 'identity.applicability']
        with self.assertRaises(UnknownInstructionSemantics): run(facts)

    def test_live_software_version_scope(self):
        facts = source.snapshot_facts('PWM', 'FX5U')
        off = next(f for f in facts if any(r['kind'] == 'PWM_minimum_OFF_time' for r in f['value'].get('rules', [])))
        off['scope']['versions'] = ['GX-version-A']
        with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY', Registry(facts)):
            for software_version in (None, 'GX-version-B'):
                c = context(); c['cpu_version'] = 'GX-version-A'
                if software_version is not None: c['software_version'] = software_version
                with self.assertRaises(UnknownInstructionSemantics):
                    validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', c)
            c = context(); c['software_version'] = 'GX-version-A'
            self.assertEqual(validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', c)['period_us'], 20)

    def test_no_empty_or_malformed_selected_root_pass(self):
        facts = source.snapshot_facts('PWM', 'FX5U')
        for roots in ([], ['missing'], 1, 'pwm-formal', [['nested']], [None]):
            report = check_fact_constraints(facts, {}, target='PWM', plc_model='FX5U', language='ladder', root_ids=roots)
            self.assertEqual(report['status'], 'unverified')

    def test_context_cannot_select_away_roots(self):
        c = context(); c['root_ids'] = []; c['pwm']['root_ids'] = []
        c['pwm']['configured_for_PWM'] = False
        with self.assertRaises(ValueError):
            validate_pwm_request('PWM', 'FX5U', 10, 20, 'Y0', c)

    def test_bare_formal_predicates_have_no_authority(self):
        report = check_fact_constraints(pwm_formal_facts('PWM', 'FX5U'),
            {'S1': 10, 'S2': 20, '$OPERANDS': ['K10', 'K20', 'Y0']},
            target='PWM', plc_model='FX5U', language='ladder', context=context())
        self.assertEqual(report['status'], 'unverified')

if __name__ == '__main__': unittest.main()
