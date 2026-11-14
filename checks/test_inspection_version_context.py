"""Focused regression for confirmed version context in static inspection."""
import unittest
from unittest.mock import patch

import test_fact_task_projection as fixtures


class StaticInspectionVersionContextTests(unittest.TestCase):
    def inspect(self, confirmed):
        from inspection.engine import _static_findings
        from plc.instruction_constraints import check_fact_constraints

        captured = []
        groups = fixtures.NativeReviewedCpuHeaderConstraintTests().groups()
        groups.append({**groups[0], 'id': 'other-unformalized',
                       'dimension': 'constraints.other_source_observation'})
        other = {'source': 'local', 'code': 'OTHER_UNKNOWN', 'severity': 'info'}
        def checked(ladder, *, plc_model, context):
            captured.append(context)
            report = check_fact_constraints(groups, {}, target='TRD',
                plc_model=plc_model, language='ladder',
                context={**context, 'project_mode': 'native_ladder'})
            return [{**report, 'rung_id': 0, 'path': '$.rungs[0]'}]
        with patch('plc.ir.build_plc_ir', return_value={'analysis': {'findings': [other]}}), \
                patch('plc.semantics.semantic_requirements_from_spec', return_value=[]), \
                patch('plc.instruction_constraints.inspect_ladder_constraints', side_effect=checked):
            _, findings = _static_findings({}, confirmed, 'FX3U')
        self.assertIn(other, findings)
        self.assertTrue(any('constraint_not_formalized' in str(f) for f in findings))
        return captured[0], findings

    def test_cpu_versions_reach_shared_checker_without_erasing_other_information(self):
        for version, failed, unresolved in [('2.19', True, False),
                                           ('2.20', False, False),
                                           (None, False, False)]:
            with self.subTest(version=version):
                confirmed = {'hardware_profile': {} if version is None else {'cpu_version': version},
                    'project_context': {'task': 'MAIN'}, 'custom_context': 'retained'}
                context, findings = self.inspect(confirmed)
                self.assertEqual(context.get('cpu_version'), version)
                self.assertEqual(context['hardware'], confirmed['hardware_profile'])
                self.assertEqual(context['project'], confirmed['project_context'])
                self.assertEqual(context['custom_context'], 'retained')
                self.assertEqual(any(f['severity'] == 'error' for f in findings), failed)
                self.assertEqual(any('condition_not_resolved' in str(f) for f in findings), unresolved)

    def test_conflicting_raw_versions_are_removed_before_normalized_values(self):
        context, findings = self.inspect({'cpu_version': '2.19', 'software_version': '1.0',
            'hardware_profile': {'cpu_version': '2.20'},
            'project_context': {'software_version': '2.0'}})
        self.assertNotIn('cpu_version', context)
        self.assertNotIn('software_version', context)
        self.assertFalse(any(f['severity'] == 'error' for f in findings))
        self.assertTrue(any('condition_not_resolved' in str(f) for f in findings))

    def test_normalized_alias_and_software_version_keep_separate_axes(self):
        context, findings = self.inspect({'hardware_profile': {'firmware': '2.20'},
            'project_context': {'software_version': '1.99'}})
        self.assertEqual(context['cpu_version'], '2.20')
        self.assertEqual(context['software_version'], '1.99')
        self.assertFalse(any(f['severity'] == 'error' for f in findings))
