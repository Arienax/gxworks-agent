# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Equivalence and cost regressions for the indexed fact-row merge.

The reference implementation below is the previous whole-list scan, kept verbatim
so the indexed merge is compared against an independent expectation rather than
against itself. Source variants, verification strength, conflicts and dependency
remapping must stay identical.
"""
import copy
import json
import random
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]

from knowledge.structured_facts import (  # noqa: E402
    _contradictory_fact_values,
    _merge_fact_rows,
    _FactMergeIndex,
)


def _reference_merge(left, incoming):
    """Previous implementation: rebuild the id map and rescan it per collision."""
    by_id = {fact['id']: fact for fact in left if fact.get('id')}
    remap, added = {}, []
    for original in incoming:
        fact = copy.deepcopy(original)
        identity = fact.get('id')
        existing = by_id.get(identity)
        if existing is not None:
            family = identity.split(':variant:')[0]
            siblings = [row for key, row in by_id.items()
                        if key == family or key.startswith(family + ':variant:')]
            comparison = lambda row: {k: v for k, v in row.items()
                                      if k not in {'id', 'sources', 'status', 'verification'}}
            equal = next((row for row in siblings if comparison(row) == comparison(fact)), None)
            if equal is not None:
                existing = equal
                if identity != existing['id']:
                    remap[identity] = existing['id']
                sources = existing.setdefault('sources', [])
                for source in fact.get('sources', ()):
                    if source not in sources:
                        sources.append(copy.deepcopy(source))
                statuses = {existing.get('status'), fact.get('status')}
                if 'conflict' in statuses:
                    existing['status'] = 'conflict'
                    existing['verification'] = 'not_performed'
                elif 'not_applicable' not in statuses:
                    strength = ('source_verified', 'candidate_evidence', 'declared_unverified', 'unknown')
                    chosen = next((status for status in strength if status in statuses), existing.get('status'))
                    existing['status'] = chosen
                    existing['verification'] = 'source_checked' if chosen == 'source_verified' else 'not_performed'
                continue
            suffix = 2
            while family + ':variant:' + str(suffix) in by_id:
                suffix += 1
            fact['id'] = family + ':variant:' + str(suffix)
            remap[identity] = fact['id']
            for prior in siblings:
                same_condition = all(prior.get(k) == fact.get(k) for k in ('dimension', 'scope', 'conditions'))
                if same_condition and _contradictory_fact_values(prior.get('value'), fact.get('value')):
                    prior['status'] = fact['status'] = 'conflict'
        by_id[fact.get('id')] = fact
        added.append(fact)
    for fact in added:
        if fact.get('depends_on'):
            fact['depends_on'] = [remap.get(key, key) for key in fact['depends_on']]
    left.extend(added)


def canonical(left):
    return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str)


def sample_fact(rng, family, suffix=None, *, payload=None, status=None, sources=None,
                depends_on=None, dimension=None):
    identity = family if suffix is None else '%s:variant:%d' % (family, suffix)
    return {
        'id': identity,
        'dimension': dimension or rng.choice(['constraints.range', 'parameters.types', 'identity.form']),
        'scope': {'models': rng.sample(['FX5U', 'FX5S', 'FX3U'], rng.randint(1, 3))},
        'conditions': rng.choice([[], [{'executions_per_scan': 2}]]),
        'value': payload if payload is not None else {'rule': rng.choice(['a', 'b']), 'code': 'PLC_RULE'},
        'status': status or rng.choice(['source_verified', 'candidate_evidence', 'unknown']),
        'verification': rng.choice(['source_checked', 'not_performed']),
        'sources': sources if sources is not None else [
            {'manual_id': 'm%d' % rng.randint(1, 3), 'pdf_pages': [rng.randint(1, 40)]}],
        'depends_on': depends_on if depends_on is not None else [],
    }


def random_scenarios(seed, count):
    """Duplicate-heavy batches that collide, agree, diverge and remap."""
    rng = random.Random(seed)
    scenarios = []
    families = ['f%d' % index for index in range(6)]
    for _ in range(count):
        left = []
        for family in rng.sample(families, rng.randint(1, len(families))):
            left.append(sample_fact(rng, family))
            for suffix in rng.sample(range(2, 7), rng.randint(0, 2)):
                left.append(sample_fact(rng, family, suffix))
        batches = []
        for _ in range(rng.randint(1, 3)):
            batch = []
            for _ in range(rng.randint(0, 5)):
                family = rng.choice(families)
                payload = {'rule': rng.choice(['a', 'b']), 'code': 'PLC_RULE'} if rng.random() < 0.7 else None
                batch.append(sample_fact(
                    rng, family, rng.choice([None, None, 2, 3]), payload=payload,
                    depends_on=rng.choice([[], ['f0'], ['f1:variant:2', 'f2']])))
            batches.append(batch)
        scenarios.append((left, batches))
    return scenarios


class IndexedMergeTest(unittest.TestCase):
    def test_random_batches_match_the_reference_algorithm(self):
        scenarios = random_scenarios(20261025, 120)
        for index, (left, batches) in enumerate(scenarios):
            expected, actual = copy.deepcopy(left), copy.deepcopy(left)
            for batch in batches:
                _reference_merge(expected, copy.deepcopy(batch))
                _merge_fact_rows(actual, copy.deepcopy(batch))
            self.assertEqual(canonical(expected), canonical(actual), 'scenario %d diverged' % index)

    def test_reused_index_matches_a_per_batch_rebuild(self):
        rng = random.Random(7)
        shared, rebuilt = [], []
        index = _FactMergeIndex(shared)
        for _ in range(6):
            batch = [sample_fact(rng, rng.choice(['f0', 'f1']), rng.choice([None, 2, 3])) for _ in range(4)]
            _merge_fact_rows(shared, copy.deepcopy(batch), _index=index)
            _merge_fact_rows(rebuilt, copy.deepcopy(batch))
            self.assertEqual(canonical(rebuilt), canonical(shared))

    def test_external_extension_invalidates_the_cached_index(self):
        first = sample_fact(random.Random(1), 'f0', payload={'rule': 'a'})
        first_list = []
        _merge_fact_rows(first_list, [copy.deepcopy(first)])
        outside = sample_fact(random.Random(2), 'f1', payload={'rule': 'b'})
        first_list.append(outside)
        incoming = dict(copy.deepcopy(outside), status='source_verified')
        _merge_fact_rows(first_list, [incoming])
        ids = [row['id'] for row in first_list]
        self.assertEqual(['f0', 'f1'], ids)
        self.assertEqual('source_verified', first_list[1]['status'])

    def test_conflicting_values_keep_both_variants_and_mark_the_conflict(self):
        base = sample_fact(random.Random(4), 'f2', payload={'rule': 'a'}, status='source_verified')
        left = [copy.deepcopy(base)]
        _merge_fact_rows(left, [dict(copy.deepcopy(base), value={'rule': 'b'})])
        self.assertEqual(['f2', 'f2:variant:2'], [row['id'] for row in left])
        self.assertEqual({'conflict'}, {row['status'] for row in left})

    def test_equivalent_witness_keeps_sources_and_strongest_state(self):
        rng = random.Random(5)
        base = sample_fact(rng, 'f3', payload={'rule': 'a'}, status='unknown', sources=[{'manual_id': 'm1'}])
        left = [copy.deepcopy(base)]
        weaker = dict(copy.deepcopy(base), status='candidate_evidence',
                      verification='not_performed', sources=[{'manual_id': 'm2'}])
        stronger = dict(copy.deepcopy(base), status='source_verified', sources=[{'manual_id': 'm2'}])
        _merge_fact_rows(left, [weaker, stronger])
        self.assertEqual(['f3'], [row['id'] for row in left])
        self.assertEqual('source_verified', left[0]['status'])
        self.assertEqual('source_checked', left[0]['verification'])
        self.assertEqual([{'manual_id': 'm1'}, {'manual_id': 'm2'}], left[0]['sources'])

    def test_variant_numbering_is_sparse_and_dependencies_remap(self):
        rng = random.Random(6)
        left = [sample_fact(rng, 'f4', payload={'rule': 'a'}),
                sample_fact(rng, 'f4', 7, payload={'rule': 'b'})]
        incoming = sample_fact(rng, 'f4', payload={'rule': 'c'},
                               depends_on=['f4', 'f4:variant:7', 'unknown'])
        _merge_fact_rows(left, [incoming])
        added = left[-1]
        self.assertEqual('f4:variant:2', added['id'])
        # A dependency on a row that had to become a variant follows the remap.
        self.assertEqual(['f4:variant:2', 'f4:variant:7', 'unknown'], added['depends_on'])

    def test_empty_identifiers_do_not_break_the_index(self):
        rng = random.Random(8)
        left = [{'id': None, 'value': {'rule': 'a'}, 'dimension': 'identity.form'}]
        incoming = sample_fact(rng, 'f5')
        _merge_fact_rows(left, [incoming])
        self.assertEqual([None, 'f5'], [row.get('id') for row in left])
        numeric = [{'id': 41, 'value': {'rule': 'a'}, 'dimension': 'identity.form'}]
        _merge_fact_rows(numeric, [{'id': 42, 'value': {'rule': 'b'}, 'dimension': 'identity.form'}])
        self.assertEqual([41, 42], [row['id'] for row in numeric])

    def test_multiple_missing_and_none_ids_retain_each_unidentified_fact(self):
        rows = [{'id': None, 'value': {'code': 1}}, {'value': {'code': 2}},
                {'id': None, 'value': {'code': 3}}]
        actual = []
        index = _FactMergeIndex(actual)
        for batch in (rows, rows):
            _merge_fact_rows(actual, batch, _index=index)
        self.assertEqual(actual, rows + rows)
        self.assertEqual(index.by_id, {})
        self.assertEqual(rows[0]['value'], {'code': 1})

    def test_replaced_middle_row_invalidates_the_cached_index(self):
        rng = random.Random(10)
        left = [sample_fact(rng, 'f%d' % index) for index in range(80)]
        _merge_fact_rows(left, [sample_fact(rng, 'f0', payload={'rule': 'z'})])
        # Same length and same end rows, but a middle row now holds a new ID.
        replacement = sample_fact(random.Random(11), 'swapped', payload={'rule': 'replaced'})
        left[40] = replacement
        incoming = dict(copy.deepcopy(replacement), status='source_verified')
        expected = copy.deepcopy(left)
        _reference_merge(expected, [copy.deepcopy(incoming)])
        _merge_fact_rows(left, [copy.deepcopy(incoming)])
        self.assertEqual(len(left), 81)
        self.assertEqual('source_verified', left[40]['status'])
        self.assertEqual(canonical(expected), canonical(left))

    def test_rows_without_ids_stay_visible_only_inside_their_batch(self):
        rng = random.Random(12)
        left = [sample_fact(rng, 'f%d' % index) for index in range(70)]
        expected = copy.deepcopy(left)
        for payload in ({'rule': 'a'}, {'rule': 'b'}):
            row = {'value': payload, 'dimension': 'identity.form', 'status': 'unknown'}
            _reference_merge(expected, [copy.deepcopy(row)])
            _merge_fact_rows(left, [copy.deepcopy(row)])
        self.assertEqual(len(left), 72)
        self.assertEqual(canonical(expected), canonical(left))

    def test_duplicate_heavy_merge_outpaces_the_reference_scan(self):
        rng = random.Random(9)
        families = ['f%d' % index for index in range(1000)]
        left = [sample_fact(rng, family) for family in families for _ in range(4)]
        incoming = [sample_fact(rng, rng.choice(families)) for _ in range(4000)]
        slow_list, fast_list = copy.deepcopy(left), copy.deepcopy(left)

        start = time.perf_counter()
        _reference_merge(slow_list, copy.deepcopy(incoming))
        reference_seconds = time.perf_counter() - start

        start = time.perf_counter()
        _merge_fact_rows(fast_list, copy.deepcopy(incoming))
        indexed_seconds = time.perf_counter() - start

        self.assertEqual(canonical(slow_list), canonical(fast_list))
        if reference_seconds >= 0.2:
            self.assertLess(indexed_seconds, reference_seconds / 4,
                            'indexed %.3fs vs reference %.3fs' % (indexed_seconds, reference_seconds))


if __name__ == '__main__':
    unittest.main()
