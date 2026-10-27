# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Public synthetic regression fixtures; no manual text or private evidence."""
import copy
import json
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from knowledge.fact_selection import project_packet, project_instruction_records
from knowledge.structured_facts import authored_packet, fact_record_coverage
from knowledge.instruction_facts import retrieve_instruction_facts
from knowledge.retriever import build_knowledge_context, retrieve_fact_aware_knowledge
from shared.model_context import technical_fact_packet


def fact(identity, dimension, **extra):
    return dict(id=identity, dimension=dimension, value={'synthetic': True},
                status='source_verified', sources=[{'source_id': 'synthetic'}],
                verification='source_checked', **extra)


def packet(facts, model='FX5U'):
    return dict(kind='instruction', target='PWM', scope={'models': [model]},
                content_origin='authored_fact', status='source_verified',
                value={'opcode': 'PWM', 'target_model': model, 'facts': facts})


def row(p):
    return dict(id='synthetic-pwm', text=json.dumps(p), content_origin='authored_fact',
                fact_kind='instruction', fact_target='PWM', source='synthetic',
                source_lane='fact', plc_models=['FX5U'], chunk_type='instruction',
                distribution_status='fact_representation_reviewed')


class TaskProjectionTests(unittest.TestCase):
    def test_narrow_roots_full_closure(self):
        source = packet([fact('root', 'parameters.1.range', depends_on=['dep']),
            fact('dep', 'execution.enable', conditions=[{'context': 'enabled', 'equals': True}]),
            fact('noise', 'effects.unrelated')])
        result = project_packet(source, plc_model='FX5U', aspects=['operand_range'])
        self.assertEqual([x['id'] for x in result['value']['facts']], ['root', 'dep'])
        self.assertEqual(result['value']['facts'][1]['selection_applicability'], 'unverified')
        self.assertTrue(result['fact_selection']['gaps'])
        self.assertEqual(len(source['value']['facts']), 3)
        self.assertEqual(result['value']['facts'][1]['conditions'], source['value']['facts'][1]['conditions'])

    def test_language_mismatch_dependency_not_admitted(self):
        p = packet([fact('root', 'effects.result', depends_on=['ld']),
                    fact('ld', 'parameters.order', scope={'languages': ['ladder']})])
        r = project_packet(p, plc_model='FX5U', language='st', aspects=['effects'])
        self.assertEqual(r['value']['facts'][1]['selection_applicability'], 'not_applicable')
        self.assertFalse(fact_record_coverage(row(r)))
        self.assertEqual(r['value']['facts'][0]['depends_on'], ['ld'])

    def test_known_conditions_and_project_mode(self):
        p = packet([fact('root', 'effects.result', conditions=[{'context': 'project_mode', 'equals': 'structured'}])])
        for mode, count in [('structured', 1), ('simple', 0), (None, 1)]:
            r = project_packet(p, plc_model='FX5U', aspects=['effects'], project_mode=mode)
            self.assertEqual(len(r['value']['facts']), count)
            if mode != 'structured': self.assertTrue(r['fact_selection']['gaps'])

    def test_empty_cpu_and_form_mismatch_are_gaps(self):
        for p in [packet([], 'FX5U'), packet([fact('a','effects.result')], 'FX3U'),
                  packet([fact('a', 'effects.result', scope={'forms': ['DPWM']})])]:
            r = project_packet(p, plc_model='FX5U', aspects=['effects'])
            self.assertFalse(r['value']['facts'])
            self.assertTrue(r['fact_selection']['gaps'])
            self.assertFalse(fact_record_coverage(row(r)))

    def test_common_aspects_and_exact_dimension(self):
        p = packet([fact('p','parameters.1.range'), fact('c','constraints.numeric_ranges'),
                    fact('x','execution.scan'), fact('e','effects.write')])
        for aspects, ids in [(['parameters'], ['p']), (['constraints'], ['c']),
            (['execution'], ['x']), (['effects'], ['e']), (['parameters.1.range'], ['p'])]:
            self.assertEqual([f['id'] for f in project_packet(p, plc_model='FX5U', aspects=aspects)['value']['facts']], ids)

    def test_generation_packer_and_query_aliases(self):
        p = packet([fact('p','parameters.1.range'), fact('e','effects.write')])
        for query in ['PWM 参数范围', 'PWM parameter range']:
            with patch('knowledge.structured_facts._runtime', return_value=(None,None,None,None,{})), \
                 patch('knowledge.fact_corpus.resolve', return_value=[row(p)]), \
                 patch('knowledge.structured_facts._adjudicated_rows', side_effect=lambda rows,*a:(rows,True)):
                results, report = retrieve_instruction_facts(query, plc_model='FX5U', task_type='generate',
                    targets=[{'opcode':'PWM','base_opcode':'PWM'}], language='st', aspects=['operand_range'], token_budget=1)
            self.assertEqual([f['id'] for f in authored_packet(results[0])['value']['facts']], ['p'])
            self.assertEqual(len(report['records']), 1)

    def test_mandatory_identity_does_not_mask_requested_gap(self):
        r = project_packet(packet([fact('identity','identity.name'),
            fact('cap','execution.core_execution_capability')]), plc_model='FX5U', aspects=['operand_range'])
        self.assertEqual(len(r['value']['facts']), 2)
        self.assertIn('requested_aspect_not_selected', [g['reason'] for g in r['fact_selection']['gaps']])
        self.assertFalse(fact_record_coverage(row(r)))

    def test_unknown_scope_field_cannot_claim_complete(self):
        r = project_packet(packet([fact('x','effects.result', scope={'project_modes':['structured']})]),
                           plc_model='FX5U', aspects=['effects'], project_mode='simple')
        self.assertTrue(r['fact_selection']['gaps'])
        self.assertFalse(fact_record_coverage(row(r)))

    def test_language_bound_operand_order(self):
        ld = fact('ld', 'parameters.order', scope={'languages':['ladder']})
        ld['value'] = {'symbols':['s1','s2']}
        st = fact('st', 'parameters.order', scope={'languages':['st']})
        st['value'] = {'symbols':['s2','s1']}
        p = packet([ld,st,fact('r1','parameters.1.range'),fact('r2','parameters.2.range')])
        for language, expected in [('ladder','r1'),('st','r2')]:
            r = project_packet(p, plc_model='FX5U', language=language, aspects='operand_range', query='PWM s1')
            self.assertIn(expected, [f['id'] for f in r['value']['facts']])
            self.assertNotIn('r2' if expected=='r1' else 'r1', [f['id'] for f in r['value']['facts']])
        st['scope'] = ld['scope']
        r = project_packet(packet([ld,st,fact('r1','parameters.1.range'),fact('r2','parameters.2.range')]),
            plc_model='FX5U', language='ladder', aspects=['operand_range'], query='PWM s1')
        self.assertEqual(len(r['value']['facts']), 4)
        self.assertIn('ambiguous_operand_order', [g['reason'] for g in r['fact_selection']['gaps']])

    def test_unknown_missing_and_cyclic_dependencies_remain_unresolved(self):
        unknown = fact('u','execution.state')
        unknown['status'] = 'unknown'
        for dependency in [unknown, fact('u','execution.state',depends_on=['r'])]:
            r = project_packet(packet([fact('r','effects.write',depends_on=['u']),dependency]),
                               plc_model='FX5U', aspects=['effects'])
            self.assertEqual(len(r['value']['facts']), 2)
            self.assertTrue(r['fact_selection']['gaps'])
            self.assertFalse(fact_record_coverage(row(r)))
        r = project_packet(packet([fact('r','effects.write',depends_on=['missing'])]),
                           plc_model='FX5U', aspects=['effects'])
        self.assertIn('missing_dependency', [g['reason'] for g in r['fact_selection']['gaps']])
        self.assertEqual(r['value']['facts'][0]['depends_on'], ['missing'])

    def test_query_aliases_derive_same_slots(self):
        from knowledge.structured_facts import requested_aspects
        for query in ['PWM 参数范围', 'PWM parameter range']:
            self.assertIn('operand_range', requested_aspects(query))
        self.assertEqual(requested_aspects('', aspects='constraints'), ['constraints'])

    def test_cross_packet_dependency_same_owner_only(self):
        root = packet([fact('r','effects.write',depends_on=['dep'])])
        dep = packet([fact('dep','parameters.order')])
        wrong_cpu = packet([fact('dep','parameters.order')], model='FX3U')
        wrong_cpu['value']['facts'][0]['value'] = {'wrong_cpu': True}
        results = project_instruction_records([row(root),row(dep),row(wrong_cpu)],
            plc_model='FX5U', aspects=['effects'])
        selected = authored_packet(results[0])
        self.assertEqual([f['id'] for f in selected['value']['facts']], ['r','dep'])
        self.assertNotIn('wrong_cpu', str(selected['value']['facts']))
        self.assertFalse(selected['fact_selection']['gaps'])
        missing = authored_packet(project_instruction_records([row(root),row(wrong_cpu)],
            plc_model='FX5U', aspects=['effects'])[0])
        self.assertIn('missing_dependency', [g['reason'] for g in missing['fact_selection']['gaps']])

    def test_cross_packet_ambiguous_dependency_and_related_pool(self):
        root = packet([fact('r','effects.write',depends_on=['dep'])])
        dep = packet([fact('dep','parameters.order')])
        conflict = copy.deepcopy(dep)
        conflict['value']['facts'][0]['value'] = {'conflicting': True}
        r = authored_packet(project_instruction_records([row(root),row(dep),row(conflict)],
            plc_model='FX5U', aspects=['effects'])[0])
        self.assertIn('ambiguous_dependency_identity', [g['reason'] for g in r['fact_selection']['gaps']])
        root['value']['related_instructions'] = [dep['value']]
        r = authored_packet(project_instruction_records([row(root)], plc_model='FX5U', aspects=['effects'])[0])
        self.assertEqual([f['id'] for f in r['value']['facts']], ['r','dep'])
        self.assertNotIn('missing_dependency', [g['reason'] for g in r['fact_selection']['gaps']])

    def test_operand_order_requires_complete_applicable_dependencies(self):
        order = fact('order', 'parameters.order', depends_on=['mapping'])
        order['value'] = {'symbols':['s1','s2']}
        conflict = fact('mapping','identity.mapping')
        conflict['status'] = 'conflict'
        unknown_condition = fact('mapping','identity.mapping', conditions=[{'context':'enabled','equals':True}])
        for deps in [[], [conflict], [unknown_condition]]:
            r = project_packet(packet([order,*deps,fact('r1','parameters.1.range'),fact('r2','parameters.2.range')]),
                plc_model='FX5U', language='st', aspects=['operand_range'], query='PWM s1')
            self.assertTrue({'r1','r2'} <= {f['id'] for f in r['value']['facts']})
            self.assertIn('operand_order_dependency_unresolved', [g['reason'] for g in r['fact_selection']['gaps']])

    def test_model_projection_preserves_limits_not_provenance(self):
        f = fact('cap', 'execution.core_execution_capability')
        f['value'] = {'core_execution_capability': 'unverified', 'source_page': 42,
                      'source_line': 1, 'clause_index': 3, 'complete_clause_review': True,
                      'manual_text': 'DO NOT LEAK', 'raw_text': 'DO NOT LEAK', 'unknown_condition': True}
        r = technical_fact_packet(project_packet(packet([f]), plc_model='FX5U', aspects=['parameters']))
        encoded = json.dumps(r)
        self.assertNotIn('DO NOT LEAK', encoded)
        self.assertNotIn('source_page', encoded)
        self.assertIn('unverified', encoded)
        self.assertIn('unknown_condition', encoded)
        self.assertFalse(r['selection']['whole_instruction_complete'])

    def test_real_retrieval_and_context_zero_top_k(self):
        p = packet([fact('p','parameters.1.range'),fact('e','effects.write')])
        with ExitStack() as stack:
            for name in ('resolve_device_family_records', 'resolve_device_records', 'resolve_error_records', 'resolve_positioning_records', 'resolve_language_records'):
                stack.enter_context(patch('knowledge.structured_facts.' + name, return_value=[]))
            # External Haystack source routing is orthogonal to the projection.
            stack.enter_context(patch('knowledge.scope.retrieval_plan', return_value={'facts':True,'design':False,'source_lanes':['fact','support','unclassified']}))
            stack.enter_context(patch('knowledge.scope.filter_records', side_effect=lambda rows,lanes:list(rows)))
            stack.enter_context(patch('knowledge.structured_facts._runtime', return_value=(None,None,None,None,{})))
            stack.enter_context(patch('knowledge.fact_corpus.resolve', side_effect=lambda kind,*a,**k:[row(p)] if kind=='instruction' else []))
            stack.enter_context(patch('knowledge.structured_facts._adjudicated_rows', side_effect=lambda rows,*a:(rows,True)))
            results = retrieve_fact_aware_knowledge('PWM 参数范围', plc_model='FX5U', language='st',
                aspects=['operand_range'], top_k=0, targets={'instructions':[{'opcode':'PWM','base_opcode':'PWM'}]})
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]['_explicit_target'])
            context = build_knowledge_context('PWM 参数范围', plc_model='FX5U', language='st',
                aspects=['operand_range'], task_type='generate', top_k=0)
            self.assertIn('parameters.1.range', str(context))
            self.assertNotIn('effects.write', str(context))


if __name__ == '__main__': unittest.main()
