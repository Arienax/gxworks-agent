"""Synthetic semantic publication tests, without manual/private evidence."""
import copy
import unittest

from knowledge.runtime_fact_projection import (
    RuntimeProjectionError, VerifiedCatalog, expand_runtime_sources, intern_runtime_sources,
    project_runtime_definitions, resolve_fact_sources,
)


def fact(identity, **kw):
    return dict(id=identity, dimension='constraints.numeric_ranges',
                value={'ranges': [{'operand': 's1', 'minimum': 1}]},
                scope={'models': ['FX5U'], 'forms': ['PWM']},
                status='source_verified', verification='source_checked',
                sources=[{'manual_id': 'synthetic', 'revision': 'A', 'pdf_page': 1}], **kw)


def payload(facts):
    return {'schema_version': 1, 'entries': [dict(vendor='mitsubishi',
            target_model='FX5U', opcode='PWM', facts=facts)]}


class RuntimeFactProjectionTests(unittest.TestCase):
    def test_duplicate_semantics_union_sources_keep_private_locations(self):
        left, right = fact('a'), fact('b')
        left['value']['source_page'] = 1
        right['value']['source_page'] = 2
        right['sources'][0]['pdf_page'] = 2
        original = payload([right, left]); before = copy.deepcopy(original)
        result, evidence, receipt = project_runtime_definitions(original)
        self.assertEqual(original, before)
        self.assertEqual(receipt['merged_facts'], 1)
        merged = result['entries'][0]['facts'][0]
        self.assertEqual(merged['id'], 'a')
        self.assertEqual(len(merged['sources']), 2)
        self.assertNotIn('source_page', merged['value'])
        self.assertEqual([x['removed_value_fields']['source_page'] for x in evidence['entries'][0]['facts']], [1, 2])
        self.assertEqual(project_runtime_definitions(result)[0], result)

    def test_nested_and_unknown_metadata_are_not_stripped(self):
        a, b = fact('a'), fact('b')
        a['value']['rules'] = [{'source_page': 1, 'source_representation': 'binary'}]
        b['value']['rules'] = [{'source_page': 2, 'source_representation': 'binary'}]
        out, _, receipt = project_runtime_definitions(payload([a, b]))
        self.assertEqual(receipt['merged_facts'], 0)
        self.assertEqual(out['entries'][0]['facts'][0]['value']['rules'], a['value']['rules'])

    def test_dependency_equivalence_and_authority_remapping(self):
        a, b = fact('a'), fact('b')
        c, d = fact('c', depends_on=['a']), fact('d', depends_on=['b'])
        c['value'] = d['value'] = {'effect': 'synthetic'}
        for f in [c, d]:
            f['authority_decision'] = {'fact_id': f['id'], 'policy': 'synthetic'}
        out, _, receipt = project_runtime_definitions(payload([d, b, c, a]))
        self.assertEqual(receipt['output_facts'], 2)
        self.assertEqual(out['entries'][0]['facts'][1]['depends_on'], ['a'])
        self.assertEqual(out['entries'][0]['facts'][1]['authority_decision']['fact_id'], 'c')

    def test_unknown_status_never_upgraded(self):
        a, b = fact('a'), fact('b')
        b.update(status='declared_unverified', verification='not_performed')
        out, _, receipt = project_runtime_definitions(payload([a, b]))
        self.assertEqual(receipt['merged_facts'], 0)
        self.assertEqual(out['entries'][0]['facts'][1]['status'], 'declared_unverified')

    def test_source_applicability_is_not_an_unconditional_union(self):
        a, b = fact('a'), fact('b')
        b['sources'][0]['conditions'] = [{'context': 'enabled', 'equals': True}]
        self.assertEqual(project_runtime_definitions(payload([a, b]))[2]['merged_facts'], 0)

    def test_invalid_graph_and_json_rejected(self):
        variants = [payload([fact('a', depends_on=['missing'])]),
                    payload([fact('a', depends_on=['a'])]),
                    payload([fact('a', depends_on=['b']), fact('b', depends_on=['a'])]),
                    payload([fact('a'), fact('a')])]
        invalid = fact('invalid'); invalid['value'] = {1: 'bad'}
        variants.append(payload([invalid]))
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(RuntimeProjectionError):
                project_runtime_definitions(raw)

    def test_embedded_witness_id_and_runtime_prefix_preserved(self):
        a, b = fact('a'), fact('b')
        formal = fact('pwm-formal:synthetic')
        formal['value']['live_source_witnesses'] = ['b']
        out, _, _ = project_runtime_definitions(payload([a, b, formal]))
        self.assertEqual({x['id'] for x in out['entries'][0]['facts']}, {'a', 'b', 'pwm-formal:synthetic'})

    def test_unknown_source_and_payload_references_pin_identity(self):
        for location in ('source', 'entry', 'payload', 'mapping_key', 'other_owner'):
            a, b, c = fact('a'), fact('b'), fact('c')
            c['value'] = {'different': True}
            raw = payload([a, b, c])
            if location == 'source':
                c['sources'][0]['witness_id'] = 'b'
            elif location == 'entry':
                raw['entries'][0]['gap_witness'] = 'b'
            elif location == 'mapping_key':
                c['sources'][0]['fact_digests'] = {'b': 'synthetic_hash'}
            elif location == 'other_owner':
                other = dict(raw['entries'][0], opcode='DPWM', facts=[fact('other')])
                other['facts'][0]['value']['external_witness'] = 'b'
                raw['entries'].append(other)
            else:
                raw['gap_witness'] = 'b'
            out, _, _ = project_runtime_definitions(raw)
            self.assertIn('b', {f['id'] for entry in out['entries'] for f in entry['facts']})

    def test_source_coverage_difference_prevents_merge(self):
        a, b = fact('a'), fact('b')
        a['sources'][0]['coverage'] = {'complete_manual': True}
        b['sources'][0]['coverage'] = {'complete_manual': False}
        self.assertEqual(project_runtime_definitions(payload([a, b]))[2]['merged_facts'], 0)

    def test_optional_value_pool_is_exact_and_tamper_checked(self):
        raw = payload([fact('a'), fact('b')]); before = copy.deepcopy(raw)
        pooled = intern_runtime_sources(raw, intern_values=True)
        self.assertEqual(raw, before)
        self.assertEqual(len(pooled['value_catalog']), 1)
        self.assertEqual(expand_runtime_sources(pooled), raw)
        item = pooled['entries'][0]['facts'][0]
        with self.assertRaises(RuntimeProjectionError):
            resolve_fact_sources(item, pooled['source_catalog'])
        tampered = copy.deepcopy(pooled)
        next(iter(tampered['value_catalog'].values()))['new_claim'] = True
        with self.assertRaises(RuntimeProjectionError):
            expand_runtime_sources(tampered)
        ambiguous = copy.deepcopy(pooled)
        ambiguous['entries'][0]['facts'][0]['value'] = {}
        with self.assertRaises(RuntimeProjectionError):
            expand_runtime_sources(ambiguous)

    def test_verified_catalog_owns_immutable_detached_snapshots(self):
        pooled = intern_runtime_sources(payload([fact('a')]), intern_values=True)
        sources = VerifiedCatalog(pooled['source_catalog'])
        values = VerifiedCatalog(pooled['value_catalog'])
        row = pooled['entries'][0]['facts'][0]
        expected = resolve_fact_sources(row, sources, values)
        next(iter(pooled['source_catalog'].values()))['revision'] = 'changed'
        next(iter(pooled['value_catalog'].values()))['new'] = True
        self.assertEqual(resolve_fact_sources(row, sources, values), expected)
        hydrated = resolve_fact_sources(row, sources, values)
        hydrated['sources'][0]['revision'] = 'changed'
        hydrated['value']['new'] = True
        self.assertEqual(resolve_fact_sources(row, sources, values), expected)
        with self.assertRaises(AttributeError):
            sources._VerifiedCatalog__snapshots = {}
        with self.assertRaises(RuntimeProjectionError):
            VerifiedCatalog(pooled['source_catalog'])
        with self.assertRaises(RuntimeProjectionError):
            VerifiedCatalog(pooled['value_catalog'])
        with self.assertRaises(RuntimeProjectionError):
            resolve_fact_sources(row, VerifiedCatalog({}), values)
        with self.assertRaises(RuntimeProjectionError):
            resolve_fact_sources(row, sources, VerifiedCatalog({}))

    def test_verified_frozen_types_order_and_immutable_leaf_sharing(self):
        original = fact('a')
        original['value'] = {'long_key_not_interned_by_python': [True, 1, 1.0, None,
                              'long technical token requiring shared immutable storage',
                              {'nested': ['second', 'first']} ]}
        pooled = intern_runtime_sources(payload([original]), intern_values=True)
        catalog = VerifiedCatalog(pooled['value_catalog'])
        identity = next(iter(catalog))
        first, second = catalog[identity], catalog[identity]
        self.assertEqual(first, original['value'])
        key = next(iter(first))
        self.assertIs(key, next(iter(second)))
        self.assertEqual([type(x) for x in first[key][:4]], [bool, int, float, type(None)])
        self.assertIs(first[key][4], second[key][4])
        first[key][-1]['nested'].append('changed')
        self.assertEqual(second[key][-1]['nested'], ['second', 'first'])
        self.assertEqual(catalog[identity], original['value'])

    def test_source_pool_exact_round_trip_and_tamper_fail_closed(self):
        raw = payload([fact('a'), fact('b')]); before = copy.deepcopy(raw)
        pooled = intern_runtime_sources(raw)
        self.assertEqual(raw, before)
        self.assertEqual(len(pooled['source_catalog']), 1)
        self.assertEqual(expand_runtime_sources(pooled), raw)
        item = pooled['entries'][0]['facts'][0]
        with self.assertRaises(RuntimeProjectionError):
            resolve_fact_sources(item, {})
        changed = copy.deepcopy(pooled['source_catalog'])
        next(iter(changed.values()))['revision'] = 'different'
        with self.assertRaises(RuntimeProjectionError):
            resolve_fact_sources(item, changed)
        with self.assertRaises(RuntimeProjectionError):
            resolve_fact_sources(dict(item, sources=[]), pooled['source_catalog'])


if __name__ == '__main__':
    unittest.main()
