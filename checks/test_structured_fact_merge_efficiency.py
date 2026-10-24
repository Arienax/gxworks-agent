"""Regression tests for the indexed structured-fact merge hot path.

The reference below intentionally mirrors the original algorithm. It catches
differences in variant naming, source order, conflict status, and dependency
remapping without requiring a private knowledge corpus or full index build.
"""
from __future__ import annotations

import copy
import json
import random
import unittest
from unittest.mock import patch

from knowledge.structured_facts import (
    _FactMergeIndex, _merge_fact_rows, authored_packet, merge_exact_records,
)


def fact(identity, number, status='candidate_evidence', *, dependency=(), condition=None):
    value = {
        'id': identity,
        'dimension': 'parameters.1.purpose',
        'scope': {'models': ['FX3U'], 'forms': ['MOV']},
        'value': {'operand': number, 'unit': 'word'},
        'sources': [{'id': f'{identity}:{number}', 'page': [2, 3]}],
        'status': status,
        'verification': 'source_checked' if status == 'source_verified' else 'not_performed',
    }
    if dependency:
        value['depends_on'] = list(dependency)
    if condition is not None:
        value['conditions'] = [condition]
    return value


def old_merge(left, incoming):
    """Unoptimized source behavior, retained only as a test oracle."""
    by_id = {item['id']: item for item in left if item.get('id')}
    remap, added = {}, []
    for original in incoming:
        item = copy.deepcopy(original)
        identity = item.get('id')
        existing = by_id.get(identity)
        if existing is not None:
            family = identity.split(':variant:')[0]
            siblings = [row for key, row in by_id.items()
                        if key == family or key.startswith(family + ':variant:')]
            comparable = lambda row: {k: v for k, v in row.items()
                                      if k not in {'id', 'sources', 'status', 'verification'}}
            equal = next((row for row in siblings if comparable(row) == comparable(item)), None)
            if equal is not None:
                existing = equal
                if identity != existing['id']:
                    remap[identity] = existing['id']
                sources = existing.setdefault('sources', [])
                for source in item.get('sources', ()):
                    if source not in sources:
                        sources.append(copy.deepcopy(source))
                statuses = {existing.get('status'), item.get('status')}
                if 'conflict' in statuses:
                    existing['status'] = 'conflict'
                    existing['verification'] = 'not_performed'
                elif 'not_applicable' not in statuses:
                    strength = ('source_verified', 'candidate_evidence', 'declared_unverified', 'unknown')
                    chosen = next((state for state in strength if state in statuses), existing.get('status'))
                    existing['status'] = chosen
                    existing['verification'] = 'source_checked' if chosen == 'source_verified' else 'not_performed'
                continue
            suffix = 2
            while family + ':variant:' + str(suffix) in by_id:
                suffix += 1
            item['id'] = family + ':variant:' + str(suffix)
            remap[identity] = item['id']
            from knowledge.structured_facts import _contradictory_fact_values
            for prior in siblings:
                same_condition = all(prior.get(k) == item.get(k)
                                     for k in ('dimension', 'scope', 'conditions'))
                if same_condition and _contradictory_fact_values(prior.get('value'), item.get('value')):
                    prior['status'] = item['status'] = 'conflict'
        by_id[item.get('id')] = item
        added.append(item)
    for item in added:
        if item.get('depends_on'):
            item['depends_on'] = [remap.get(key, key) for key in item['depends_on']]
    left.extend(added)


def assert_same_batches(testcase, base, batches):
    old, new = copy.deepcopy(base), copy.deepcopy(base)
    unchanged = copy.deepcopy(batches)
    index = _FactMergeIndex(new)
    for position, batch in enumerate(batches):
        old_merge(old, batch)
        _merge_fact_rows(new, batch, _index=index)
        testcase.assertEqual(new, old, f'batch {position}')
        testcase.assertEqual(batch, unchanged[position], 'incoming facts were mutated')
    testcase.assertEqual(index.by_id, {row['id']: row for row in new if row.get('id')})
    return new


def packet(facts, *, catalogue_only=False):
    return {
        'content_origin': 'authored_fact', 'kind': 'instruction', 'target': 'MOV',
        'scope': {'models': ['FX3U']},
        'value': {
            'opcode': 'MOV', 'target_model': 'FX3U',
            'facts': copy.deepcopy(facts), 'call_available': not catalogue_only,
            'catalogue_only': catalogue_only,
        },
    }


def row(source):
    return {'text': json.dumps(source), 'content_origin': 'authored_fact',
            'fact_kind': 'instruction', 'fact_target': 'MOV', 'plc_models': ['FX3U']}


class IndexedFactMergeTests(unittest.TestCase):
    def test_exact_variants_and_dependency_remapping(self):
        base = [fact('A', 0), fact('A:variant:2', 2), fact('A:variant:4', 4),
                fact('B', 0, status='source_verified')]
        batches = [
            [fact('A', 2, 'source_verified'),
             fact('A', 3, dependency=('A',)), fact('A', 0)],
            [fact('A', 5), fact('A', 4), fact('B', 1)],
            [fact('A:variant:3', 9), fact('A', 6, dependency=('B',))],
        ]
        merged = assert_same_batches(self, base, batches)
        self.assertEqual(len({item['id'] for item in merged}), len(merged))
        self.assertIn('A:variant:3', {item['id'] for item in merged})
        self.assertIn('A:variant:5', {item['id'] for item in merged})

    def test_conflict_scope_and_source_verification_stay_distinct(self):
        base = [fact('A', 1, 'source_verified')]
        batches = [
            [fact('A', 1, 'unknown')],  # must not downgrade source_checked
            [fact('A', 2)],             # same scope, contradictory value
            [fact('A', 3, condition={'context': 'enabled', 'equals': True})],
            [fact('A', 2, 'conflict')],
            [fact('A', 2, 'not_applicable')],
        ]
        merged = assert_same_batches(self, base, batches)
        self.assertTrue(any(item['status'] == 'conflict' for item in merged))
        self.assertTrue(any(item.get('conditions') for item in merged))

    def test_falsy_and_sparse_variant_ids_keep_legacy_allocation(self):
        base = [fact('', 1), fact('A:variant:2', 2), fact('A:variant:4', 4)]
        batches = [
            [fact('', 1), fact('A', 1)],
            [fact('', 2), fact('A', 3), fact('A', 5)],
            [fact('A', 6)],
        ]
        assert_same_batches(self, base, batches)

    def test_randomized_staged_merge_matches_preoptimization(self):
        rng = random.Random(20261025)
        statuses = ('source_verified', 'candidate_evidence', 'declared_unverified',
                    'unknown', 'conflict', 'not_applicable')
        for trial in range(45):
            def make():
                name = rng.choice(('A', 'B', 'C', 'D', 'A:variant:2', 'B:variant:3'))
                number = rng.randrange(5)
                dep = (rng.choice(('A', 'B', 'C')),) if rng.randrange(4) == 0 else ()
                return fact(name, number, rng.choice(statuses), dependency=dep)
            base = [make() for _ in range(rng.randrange(2, 13))]
            batches = [[make() for _ in range(rng.randrange(1, 15))] for _ in range(4)]
            with self.subTest(trial=trial):
                assert_same_batches(self, base, batches)

    def test_duplicate_fact_is_not_deepcopied(self):
        source = fact('A', 2, 'source_verified')
        original = fact('A', 2, 'candidate_evidence')
        original['sources'] = [{'id': 'new', 'nested': [1, 2]}]
        left = [source]
        index = _FactMergeIndex(left)
        actual_deepcopy = copy.deepcopy
        with patch('knowledge.structured_facts.copy.deepcopy', wraps=actual_deepcopy) as copied:
            _merge_fact_rows(left, [original], _index=index)
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]['status'], 'source_verified')
        self.assertEqual(len(left[0]['sources']), 2)
        self.assertTrue(all(call.args[0] is not original for call in copied.call_args_list))
        self.assertEqual(original['status'], 'candidate_evidence')

    def test_index_tracks_new_families_without_copying_existing_facts(self):
        base = [fact('A', 0), fact('C:variant:5', 1)]
        index = _FactMergeIndex(base)
        self.assertIs(index.by_id['A'], base[0])
        self.assertIs(index.by_family['C'][0], base[1])
        _merge_fact_rows(base, [fact('B', 1), fact('A', 2)], _index=index)
        self.assertEqual(len(index.by_family['A']), 2)
        self.assertIs(index.by_id['A:variant:2'], base[-1])

    def test_record_union_reuses_index_and_resets_after_placeholder_replacement(self):
        base = packet([fact('A', 0)], catalogue_only=True)
        first = packet([fact('A', 1, 'source_verified')])
        second = packet([fact('A', 2)])
        third = packet([fact('A', 1, 'candidate_evidence')])
        result = merge_exact_records(
            [row(base), row(first), row(second), row(third)], plc_model='FX3U')
        self.assertEqual(len(result), 1)
        final = authored_packet(result[0])
        facts = final['value']['facts']
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0]['value']['operand'], 1)
        self.assertEqual(facts[0]['status'], 'conflict')
        self.assertEqual(facts[1]['id'], 'A:variant:2')


if __name__ == '__main__':
    unittest.main()
