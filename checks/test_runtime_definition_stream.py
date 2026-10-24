"""Bounded transport iteration and one-command candidate migration tests."""
import copy
import gzip
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
from checks.test_instruction_owner_store import fixture
from plc.instruction_definition_storage import compact_definitions, expand_definitions
from plc.instruction_owner_store import OwnerDefinitionStore
from tools.runtime_definition_stream import inspect_definition_stream, iter_definition_owners
from scripts.build_instruction_owner_store import build_owner_store
from knowledge.fact_corpus import project_definition


class DefinitionStreamTests(unittest.TestCase):
    def put(self, root, payload):
        p = Path(root) / 'definitions.json.gz'
        p.write_bytes(gzip.compress(json.dumps(payload).encode()))
        return p

    def test_flat_and_compact_exact_owner_roundtrip(self):
        for payload in (fixture(), compact_definitions(fixture())):
            with self.subTest(schema=payload['schema_version']), tempfile.TemporaryDirectory() as d:
                p = self.put(d, payload)
                metadata = inspect_definition_stream(p)
                rows = list(iter_definition_owners(p, metadata))
                expected = expand_definitions(payload)['entries']
                key = lambda row: (row['vendor'], row['opcode'], row['target_model'])
                self.assertEqual(sorted(rows, key=key), sorted(expected, key=key))
                self.assertNotIn('entries', metadata)
                self.assertNotIn('definitions', metadata)

    def test_metadata_after_owners_and_auxiliary_arrays_preserved(self):
        payload = fixture()
        payload = {'entries': payload.pop('entries'), **payload,
                   'language_facts': [{'target': 'synthetic'}], 'documents': [{'private': 'must not disappear'}]}
        with tempfile.TemporaryDirectory() as d:
            p = self.put(d, payload)
            metadata = inspect_definition_stream(p)
            self.assertEqual(metadata['documents'], payload['documents'])
            self.assertEqual(metadata['language_facts'], payload['language_facts'])
            self.assertEqual(len(list(iter_definition_owners(p, metadata))), 2)

    def test_modified_input_or_metadata_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.put(d, fixture()); metadata = inspect_definition_stream(p)
            metadata['method'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'changed'):
                list(iter_definition_owners(p, metadata))
            metadata = inspect_definition_stream(p)
            p.write_bytes(p.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'changed'):
                list(iter_definition_owners(p, metadata))

    def test_unsupported_pooled_or_ambiguous_input_rejected(self):
        for extra in ({'source_serialization': 'fact-source-pool-v1'}, {'definitions': []}, {'schema_version': True}):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as d:
                p = self.put(d, {**fixture(), **extra})
                with self.assertRaises(ValueError):
                    inspect_definition_stream(p)

    def test_metadata_aggregate_is_bounded_not_just_each_element(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.put(d, {'schema_version': 1, 'entries': [], 'language_facts': ['x' * 20] * 20})
            with self.assertRaisesRegex(ValueError, 'Combined root metadata'):
                inspect_definition_stream(p, max_unit_bytes=128)

    def test_form_model_product_is_not_materialized(self):
        payload = {'schema_version': 2, 'serialization': 'common+form_diff+model_diff-v1',
                   'verification_inheritance': False, 'model_profiles': {'all': ['CPU'+str(i) for i in range(1000)]},
                   'definitions': [{'vendor': 'mitsubishi', 'base_opcode': 'X', 'common': {'facts': {}},
                       'owners': [{'forms': ['X'+str(i) for i in range(1000)], 'model_profile': 'all'}],
                       'form_diffs': {}, 'model_diffs': []}]}
        with tempfile.TemporaryDirectory() as d:
            p = self.put(d, payload); metadata = inspect_definition_stream(p)
            tracemalloc.start()
            try:
                rows = iter_definition_owners(p, metadata)
                self.assertEqual(next(rows)['opcode'], 'X0')
                self.assertLess(tracemalloc.get_traced_memory()[1], 8 * 1024 * 1024)
                rows.close()
            finally:
                tracemalloc.stop()

    def test_streaming_builder_runs_conversion_and_audit_without_json_load(self):
        with tempfile.TemporaryDirectory() as d:
            payload = compact_definitions(fixture()); p = self.put(d, payload)
            out = Path(d) / 'candidate'
            # Reader/writer may json.loads individual units, but never json.load the document.
            with patch('json.load', side_effect=AssertionError('full document load forbidden')):
                receipt = build_owner_store(p, out)
            self.assertEqual(receipt['owners_checked'], 2)
            self.assertFalse(receipt['semantic_projection_performed'])
            self.assertFalse(receipt['release_accepted'])
            self.assertTrue((out / 'owner-store-audit.json').is_file())
            self.assertFalse((out / 'instruction_definitions.manifest.pending.json').exists())
            store = OwnerDefinitionStore(out / 'instruction_definitions.manifest.json')
            try:
                self.assertEqual(store.get_entry('PWM', cpu='FX5U'), fixture()['entries'][0])
            finally:
                store.close()
            with self.assertRaisesRegex(ValueError, 'never overwritten'):
                build_owner_store(p, out)

    def test_builder_does_not_upgrade_unreviewed_metadata(self):
        for key in ('corpus_kind', 'method'):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as d:
                payload = fixture(); payload.pop(key)
                p = self.put(d, payload); out = Path(d) / 'candidate'
                with self.assertRaisesRegex(ValueError, 'explicit'):
                    build_owner_store(p, out)
                self.assertFalse((out / 'instruction_definitions.manifest.json').exists())

    def test_builder_preserves_false_and_missing_admission_fields(self):
        with tempfile.TemporaryDirectory() as d:
            payload = fixture(); payload['entries'][0]['call_available'] = False
            p = self.put(d, payload); out = Path(d) / 'candidate'; build_owner_store(p, out)
            report = json.loads((out / 'owner-store-audit.json').read_text())['owner_store']
            row = report['owner_provenance']['by_model']['FX5U']
            self.assertEqual(row['call_available'], {'false': 1})
            self.assertEqual(row['call_admission_reason'], {'<missing>': 1})

    def test_explicit_raw_projection_preserves_withheld_and_matches_official_projection(self):
        with tempfile.TemporaryDirectory() as d:
            payload = fixture(); payload.pop('corpus_kind')
            payload['entries'][0]['facts'][0]['value'] = {'manual_text': 'private original condition'}
            payload['entries'][1]['facts'][0]['status'] = 'unknown'
            payload['entries'][1]['facts'][1]['status'] = 'conflict'
            source = self.put(d, compact_definitions(payload)); before = source.read_bytes()
            out, private = Path(d) / 'candidate', Path(d) / 'private'
            receipt = build_owner_store(source, out, project_raw=True, private_audit_dir=private)
            self.assertTrue(receipt['strict_projection_performed'])
            self.assertFalse(receipt['fact_status_promotion_performed'])
            self.assertEqual(receipt['raw_fact_instances'], 4)
            self.assertEqual(receipt['projected_fact_instances'], 2)
            self.assertEqual(receipt['withheld_fact_instances'], 2)
            store = OwnerDefinitionStore(out / 'instruction_definitions.manifest.json')
            try:
                for owner in payload['entries']:
                    self.assertEqual(store.get_entry('PWM', cpu=owner['target_model']), project_definition(owner)[0])
            finally:
                store.close()
            with gzip.open(private / 'projection-receipts.jsonl.gz', 'rt') as stream:
                rows = [json.loads(line) for line in stream]
            withheld = [item for row in rows if row['kind'] == 'owner_projection' for item in row['withheld']]
            self.assertEqual({x['id'] for x in withheld}, {'base', 'effect'})
            self.assertIn('withheld_dependency', {x['reason'] for x in withheld})
            self.assertEqual(source.read_bytes(), before)
            self.assertNotIn('private original condition', (out / 'owner-store-build-receipt.json').read_text())

    def test_raw_projection_requires_separate_private_paths(self):
        with tempfile.TemporaryDirectory() as d:
            source = self.put(d, fixture()); out = Path(d) / 'candidate'
            with self.assertRaisesRegex(ValueError, 'requires a separate'):
                build_owner_store(source, out, project_raw=True)
            with self.assertRaisesRegex(ValueError, 'must be separate'):
                build_owner_store(source, out, project_raw=True, private_audit_dir=out / 'evidence')


if __name__ == '__main__':
    unittest.main()
