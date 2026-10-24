"""Independent synthetic acceptance tests for semantic runtime projection.

No manual prose, private evidence, or production code is modified by this suite.
Run with PYTHONPATH=<checkout>/src:<checkout> python -m unittest discover -s <this-dir>.
"""
import copy
import json
import unittest

from knowledge.runtime_fact_projection import project_runtime_definitions
from plc.instruction_definition import select_fact_dependencies


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def fact(identity='a', **changes):
    result = {
        'id': identity,
        'dimension': 'constraints.documented_restriction',
        'value': {'rules': [{'kind': 'synthetic_bound', 'subject': {'operand': 's1'},
                            'maximum': 10, 'unit': 'ms'}]},
        'status': 'source_verified', 'verification': 'source_checked',
        'scope': {'models': ['FX5U'], 'forms': ['PWM'], 'languages': ['ladder']},
        'conditions': [], 'depends_on': [],
        'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_page': 1}],
    }
    result.update(changes)
    return result


def entry(facts, **changes):
    result = {'vendor': 'mitsubishi', 'opcode': 'PWM', 'target_model': 'FX5U',
              'facts': facts, 'semantic_completeness': 'partial', 'gaps': ['not_whole_instruction']}
    result.update(changes)
    return result


def payload(entries):
    return {'schema_version': 1, 'method': 'instruction-material-compiler-v1', 'entries': entries}


def project(facts):
    return project_runtime_definitions(payload([entry(facts)]))[0]['entries'][0]['facts']


class ProjectionAdversarialTests(unittest.TestCase):
    def test_exact_duplicate_combines_provenance_without_mutating_input(self):
        a = fact('a')
        b = fact('b', sources=[{'manual_id': 'synthetic', 'revision': 'A', 'pdf_page': 2}])
        original = payload([entry([a, b])])
        frozen = canonical(original)
        result, lineage, receipt = project_runtime_definitions(original)
        self.assertEqual(canonical(original), frozen)
        self.assertEqual(len(result['entries'][0]['facts']), 1)
        self.assertTrue(lineage)
        self.assertTrue(receipt)
        # Evidence can be pooled, but the private audit must retain both identities.
        combined = canonical({'runtime': result, 'lineage': lineage})
        self.assertIn('"a"', combined)
        self.assertIn('"b"', combined)

    def test_embedded_witness_and_pwm_identity_are_not_broken(self):
        values = [fact('a'), fact('b'), fact('pwm-formal:keep'),
                  fact('witness', value={'live_source_witnesses': ['b']})]
        result = project(values)
        ids = {f['id'] for f in result}
        self.assertTrue({'b', 'pwm-formal:keep', 'witness'} <= ids)
        self.assertEqual(next(f for f in result if f['id'] == 'witness')['value'],
                         {'live_source_witnesses': ['b']})

    def test_authority_self_identity_is_remapped_but_members_preserved(self):
        values = [fact(i, authority_decision={'fact_id': i, 'decision': 'reviewed'})
                  for i in ('a', 'b')]
        result = project(values)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['authority_decision']['fact_id'], result[0]['id'])
        self.assertEqual(len(project([fact('a', members=['s1']), fact('b', members=['s2'])])), 2)

    def test_audit_removal_is_root_only_and_private_lineage_complete(self):
        values = [fact('a', value={'source_page': 1, 'rules': [{'source_page': 9}]}),
                  fact('b', value={'source_page': 2, 'rules': [{'source_page': 9}]})]
        result, private, _ = project_runtime_definitions(payload([entry(values)]))
        self.assertEqual(len(result['entries'][0]['facts']), 1)
        self.assertEqual(result['entries'][0]['facts'][0]['value'], {'rules': [{'source_page': 9}]})
        rows = private['entries'][0]['facts']
        self.assertEqual({row['input_id'] for row in rows}, {'a', 'b'})
        self.assertEqual({row['removed_value_fields']['source_page'] for row in rows}, {1, 2})
        self.assertTrue(all(row['input_sha256'] and row['sources'] for row in rows))
        values[1]['value']['rules'][0]['source_page'] = 10
        self.assertEqual(len(project(values)), 2)

    def test_source_coverage_and_embedded_reference_keys_do_not_disappear(self):
        self.assertEqual(len(project([
            fact('a', sources=[{'coverage': 'full_instruction'}]),
            fact('b', sources=[{'coverage': 'bounded_subset'}])])), 2)
        for witness in ({'witness_id': 'b'}, {'fact_digests': {'b': 'digest'}}):
            values = [fact('a'), fact('b'), fact('c', sources=[witness])]
            self.assertIn('b', {f['id'] for f in project(values)})

    def test_owner_and_payload_embedded_references_remain_resolvable(self):
        for level in ('owner', 'payload'):
            original = payload([entry([fact('a'), fact('b')])])
            holder = original if level == 'payload' else original['entries'][0]
            holder['fact_digests'] = {'b': 'digest'}
            result = project_runtime_definitions(original)[0]
            self.assertIn('b', {f['id'] for f in result['entries'][0]['facts']})

    def test_all_public_pwm_snapshots_preserve_exact_source_identity(self):
        from plc.pwm_fact_source import snapshot_facts, source_identity_matches
        from plc.instruction_definition import InstructionFactGroup
        for model in ('FX5U', 'FX5UC', 'FX5UJ', 'FX5S'):
            for opcode in ('PWM', 'DPWM'):
                original = payload([entry(snapshot_facts(opcode, model),
                                          target_model=model, opcode=opcode)])
                result = project_runtime_definitions(original)[0]
                for value in result['entries'][0]['facts']:
                    self.assertTrue(source_identity_matches(
                        InstructionFactGroup.from_mapping(value), opcode, model))

    def test_source_pool_exact_roundtrip_and_corruption_fails_closed(self):
        from knowledge.runtime_fact_projection import intern_runtime_sources, expand_runtime_sources
        original = payload([entry([fact('a'), fact('b')])])
        pooled = intern_runtime_sources(original)
        self.assertEqual(expand_runtime_sources(pooled), original)
        self.assertEqual(len(pooled['source_catalog']), 1)
        tampered = copy.deepcopy(pooled)
        key = next(iter(tampered['source_catalog']))
        tampered['source_catalog'][key]['pdf_page'] = 999
        with self.assertRaises(ValueError):
            expand_runtime_sources(tampered)
        missing = copy.deepcopy(pooled)
        missing['source_catalog'].clear()
        with self.assertRaises(ValueError):
            expand_runtime_sources(missing)
        ambiguous = copy.deepcopy(pooled)
        ambiguous['entries'][0]['facts'][0]['sources'] = []
        with self.assertRaises(ValueError):
            expand_runtime_sources(ambiguous)

    def test_value_pool_roundtrip_types_and_tamper_rejection(self):
        from knowledge.runtime_fact_projection import intern_runtime_sources, expand_runtime_sources
        original = payload([entry([fact(str(i), value={'x': value})
                                   for i, value in enumerate((True, 1, '1', [2, 1], [1, 2]))])])
        pooled = intern_runtime_sources(original, intern_values=True)
        self.assertEqual(len(pooled['value_catalog']), 5)
        hydrated = expand_runtime_sources(pooled)
        self.assertEqual(hydrated, original)
        hydrated['entries'][0]['facts'][0]['value']['x'] = 'mutated'
        self.assertEqual(expand_runtime_sources(pooled), original)
        for mutation in ('tamper', 'missing', 'ambiguous', 'version'):
            broken = copy.deepcopy(pooled)
            key = next(iter(broken['value_catalog']))
            if mutation == 'tamper': broken['value_catalog'][key]['x'] = 'changed'
            if mutation == 'missing': broken['value_catalog'].clear()
            if mutation == 'ambiguous': broken['entries'][0]['facts'][0]['value'] = {}
            if mutation == 'version': broken['value_serialization'] = 'future-unknown'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                expand_runtime_sources(broken)

    def test_same_dimension_independent_restrictions_are_not_reduced_to_one(self):
        a = fact('interrupt', value={'restriction': 'interrupt_program'})
        b = fact('range', value={'restriction': 'operand_range'})
        c = fact('hardware', value={'restriction': 'hardware_channel'})
        self.assertEqual(len(project([a, b, c])), 3)

    def test_status_and_verification_never_promote(self):
        values = [fact('verified'),
                  fact('unknown', status='declared_unverified', verification='not_performed'),
                  fact('conflict', status='conflict', verification='not_performed')]
        result = project(values)
        self.assertEqual(len(result), 3)
        self.assertEqual(sorted((f['status'], f['verification']) for f in result),
                         sorted((f['status'], f['verification']) for f in values))

    def test_distinct_semantic_axes_do_not_merge(self):
        mutations = {
            'model': {'scope': {'models': ['FX5UC'], 'forms': ['PWM'], 'languages': ['ladder']}},
            'language': {'scope': {'models': ['FX5U'], 'forms': ['PWM'], 'languages': ['st']}},
            'version': {'scope': {'models': ['FX5U'], 'forms': ['PWM'], 'languages': ['ladder'], 'versions': ['v2']}},
            'form': {'scope': {'models': ['FX5U'], 'forms': ['DPWM'], 'languages': ['ladder']}},
            'condition': {'conditions': [{'context': 'project_mode', 'equals': 'structured'}]},
            'unit': {'value': {'rules': [{'kind': 'synthetic_bound', 'subject': {'operand': 's1'}, 'maximum': 10, 'unit': 'us'}]}},
            'operand': {'value': {'rules': [{'kind': 'synthetic_bound', 'subject': {'operand': 's2'}, 'maximum': 10, 'unit': 'ms'}]}},
            'value': {'value': {'rules': [{'kind': 'synthetic_bound', 'subject': {'operand': 's1'}, 'maximum': 11, 'unit': 'ms'}]}},
            'authority': {'authority_decision': {'fact_id': 'b', 'policy': 'different_authority', 'selected_revision': 'B'}},
            'unknown_field': {'new_semantic_axis': 'must_not_drop'},
        }
        for name, changes in mutations.items():
            with self.subTest(axis=name):
                self.assertEqual(len(project([fact('a'), fact('b', **changes)])), 2)

    def test_source_level_conditions_cannot_be_union_erased(self):
        for key in ('conditions', 'applicability'):
            with self.subTest(key=key):
                a = fact('a')
                source = dict(a['sources'][0], **{key: [{'context': 'channel_enabled', 'equals': True}]})
                b = fact('b', sources=[source])
                self.assertEqual(len(project([a, b])), 2)

    def test_boolean_number_string_and_ordered_values_remain_distinct(self):
        values = [True, 1, '1', ['s1', 's2'], ['s2', 's1']]
        self.assertEqual(len(project([fact(str(i), value={'synthetic_value': v}) for i, v in enumerate(values)])), len(values))

    def test_map_key_order_does_not_prevent_exact_dedup(self):
        a = fact('fact-a', value={'x': 1, 'y': {'a': 2, 'b': 3}})
        b = fact('fact-b', value={'y': {'b': 3, 'a': 2}, 'x': 1})
        self.assertEqual(len(project([a, b])), 1)

    def test_dependencies_with_distinct_meaning_are_not_merged(self):
        a = fact('dep_a', value={'condition': 'A'})
        b = fact('dep_b', value={'condition': 'B'})
        roots = [fact('root_a', depends_on=['dep_a']), fact('root_b', depends_on=['dep_b'])]
        out = project([a, b, *roots])
        self.assertEqual(len(out), 4)
        self.assertEqual(len({tuple(f['depends_on']) for f in out if f.get('depends_on')}), 2)

    def test_equivalent_dependencies_remap_to_complete_closure(self):
        deps = [fact('dep_a', value={'condition': 'A'}), fact('dep_b', value={'condition': 'A'})]
        roots = [fact('root_a', depends_on=['dep_a']), fact('root_b', depends_on=['dep_b'])]
        out = project([*deps, *roots])
        self.assertEqual(len(out), 2)
        ids = {f['id'] for f in out}
        self.assertTrue(all(set(f.get('depends_on', [])) <= ids for f in out))
        self.assertEqual(sum(bool(f.get('depends_on')) for f in out), 1)

    def test_owner_identity_never_cross_merges(self):
        entries = [entry([fact('same')]),
                   entry([fact('same', scope={'models': ['FX5UC'], 'forms': ['PWM'], 'languages': ['ladder']})], target_model='FX5UC'),
                   entry([fact('same', scope={'models': ['FX5U'], 'forms': ['DPWM'], 'languages': ['ladder']})], opcode='DPWM')]
        result, _, _ = project_runtime_definitions(payload(entries))
        self.assertEqual(len(result['entries']), 3)
        self.assertTrue(all(len(e['facts']) == 1 for e in result['entries']))
        self.assertEqual({(e['target_model'], e['opcode']) for e in result['entries']},
                         {('FX5U', 'PWM'), ('FX5UC', 'PWM'), ('FX5U', 'DPWM')})

    def test_duplicate_identity_with_conflicting_body_is_rejected(self):
        with self.assertRaises((ValueError, TypeError)):
            project([fact('same'), fact('same', value={'different': True})])

    def test_dangling_and_cyclic_graphs_do_not_certify(self):
        for facts in ([fact('a', depends_on=['missing'])],
                      [fact('a', depends_on=['a'])],
                      [fact('a', depends_on=['b']), fact('b', depends_on=['a'])]):
            with self.subTest(graph=facts):
                try:
                    out = project(facts)
                except (ValueError, TypeError):
                    continue
                for f in out:
                    closure = select_fact_dependencies(out, [f['id']], opcode='PWM', model='FX5U', language='ladder')
                    self.assertTrue(closure['gaps'])
                    self.assertFalse(closure.get('source_verification_complete', False))

    def test_idempotent_and_fact_order_independent(self):
        facts = [fact('z'), fact('a'), fact('other', value={'independent': True})]
        first, _, _ = project_runtime_definitions(payload([entry(facts)]))
        second, _, _ = project_runtime_definitions(copy.deepcopy(first))
        shuffled, _, _ = project_runtime_definitions(payload([entry(list(reversed(facts)))]))
        self.assertEqual(canonical(first), canonical(second))
        self.assertEqual(canonical(first), canonical(shuffled))


    def test_numeric_checker_outcomes_preserve_fail_unknown_not_applicable(self):
        from plc.instruction_constraints import check_fact_constraints
        base = fact('range_a', dimension='constraints.numeric_ranges',
                    value={'ranges': [{'operand_index': 0, 'minimum': 1, 'maximum': 10}]},
                    conditions=[{'context': 'project_mode', 'equals': 'structured'}])
        duplicate = copy.deepcopy(base); duplicate['id'] = 'range_b'
        duplicate['sources'][0]['pdf_page'] = 2
        source_unknown = fact('source_unknown', dimension='constraints.numeric_ranges',
                             value={'ranges': [{'operand_index': 1, 'minimum': 0}]},
                             status='declared_unverified', verification='not_performed')
        execution_gap = fact('execution_gap', dimension='execution.source_qualifications',
                             value={'capability': 'not_established'}, status='declared_unverified',
                             verification='not_performed')
        before = [base, duplicate, source_unknown, execution_gap]
        after = project(before)
        for value in (-1, 0, 5, 11, None, True, float('nan')):
            for mode in (None, 'structured', 'simple'):
                for language in ('ladder', 'st', None):
                    with self.subTest(value=value, mode=mode, language=language):
                        reports = [check_fact_constraints(facts, {'$0': value, '$1': 0},
                            target='PWM', plc_model='FX5U', language=language,
                            context={} if mode is None else {'project_mode': mode})
                            for facts in (before, after)]
                        self.assertEqual(reports[0]['status'], reports[1]['status'])
                        self.assertEqual(reports[0]['execution_coverage'], reports[1]['execution_coverage'])
                        self.assertEqual({(c['status'], c.get('reason')) for c in reports[0]['checks']},
                                         {(c['status'], c.get('reason')) for c in reports[1]['checks']})

    def test_generation_selection_retains_unsafe_condition_and_dependency(self):
        from knowledge.fact_selection import project_packet
        from shared.model_context import technical_fact_packet
        deps = [fact('dep_a', dimension='execution.scan', value={'state': 'hold'},
                     conditions=[{'context': 'enabled', 'equals': True}]),
                fact('dep_b', dimension='execution.scan', value={'state': 'hold'},
                     conditions=[{'context': 'enabled', 'equals': True}])]
        roots = [fact('r_a', dimension='parameters.1.range', depends_on=['dep_a']),
                 fact('r_b', dimension='parameters.1.range', depends_on=['dep_b'])]
        before = deps + roots + [fact('noise', dimension='effects.unrelated')]
        after = project(before)
        def packet_for(facts):
            return {'kind': 'instruction', 'target': 'PWM', 'scope': {'models': ['FX5U']},
                    'content_origin': 'authored_fact', 'status': 'source_verified',
                    'value': {'opcode': 'PWM', 'target_model': 'FX5U', 'facts': facts}}
        def semantic_selected(result):
            return {canonical({k: v for k, v in f.items() if k not in ('id', 'sources', 'depends_on')})
                    for f in result['value']['facts']}
        for context in ({}, {'enabled': True}, {'enabled': False}):
            results = [project_packet(packet_for(facts), plc_model='FX5U', language='ladder',
                                      aspects=['operand_range'], instance_context=context)
                       for facts in (before, after)]
            self.assertEqual(semantic_selected(results[0]), semantic_selected(results[1]))
            self.assertEqual({g['reason'] for g in results[0]['fact_selection']['gaps']},
                             {g['reason'] for g in results[1]['fact_selection']['gaps']})
            self.assertEqual(results[0]['fact_selection']['whole_instruction_complete'],
                             results[1]['fact_selection']['whole_instruction_complete'])
            for result in results:
                self.assertNotIn('effects.unrelated', str(technical_fact_packet(result)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
