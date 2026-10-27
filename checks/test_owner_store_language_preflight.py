"""Bounded metadata failures occur before any expensive owner publication."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from checks.test_instruction_owner_store import fixture
import scripts.build_instruction_owner_store as build


class OwnerStoreLanguagePreflightTests(unittest.TestCase):
    def input_file(self, root, features):
        payload=fixture();payload['language_facts']=features
        path=Path(root)/'source.json.gz'
        path.write_bytes(gzip.compress(json.dumps(payload).encode()))
        return path

    def test_unreviewed_language_metadata_fails_before_database_or_ledger(self):
        # The extra metadata is intentionally still unsupported until reviewed.
        # This is a safety regression, not permission to accept real model rules.
        feature={'target':'ST.CALL','language':'st','unreviewed_extra':['FX5U'],'facts':[]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=self.input_file(root,[feature]);before=source.read_bytes()
            out=root/'candidate';private=root/'private'
            with patch.object(build,'OwnerStoreWriter') as writer, \
                 patch.object(build,'iter_definition_owners') as owners:
                with self.assertRaisesRegex(ValueError,'metadata'):
                    build.build_owner_store(source,out,project_raw=True,private_audit_dir=private)
                writer.assert_not_called();owners.assert_not_called()
            self.assertFalse((out/'plc_facts.sqlite').exists())
            self.assertFalse((out/'instruction_definitions.manifest.json').exists())
            self.assertFalse((private/'projection-receipts.jsonl.gz').exists())
            self.assertFalse((private/'projection-summary.json').exists())
            failure=json.loads((private/'projection-failure.json').read_text())
            self.assertEqual(failure['phase'],'language_preflight')
            self.assertEqual(failure['owners_processed'],0)
            self.assertFalse(failure['release_accepted'])
            self.assertEqual(source.read_bytes(),before)

    def test_duplicate_feature_targets_fail_before_owner_iteration(self):
        feature={'target':'ST.CALL','language':'st','facts':[]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=self.input_file(root,[feature,feature])
            with patch.object(build,'OwnerStoreWriter') as writer:
                with self.assertRaises(ValueError):build.build_owner_store(source,root/'candidate')
                writer.assert_not_called()

    def test_cli_projects_bounded_metadata_once_before_owner_iteration(self):
        feature={'target':'ST.CALL','language':'st','facts':[]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=self.input_file(root,[feature]);events=[]
            original_project=build.project_language_features
            original_iter=build.iter_definition_owners
            def project(features):events.append('language');return original_project(features)
            def owners(*args,**kwargs):events.append('owners');return original_iter(*args,**kwargs)
            with patch.object(build,'project_language_features',side_effect=project) as projected, \
                 patch.object(build,'iter_definition_owners',side_effect=owners), \
                 patch('knowledge.runtime_fact_publication.project_language_features',side_effect=AssertionError('Unexpected second language projection')):
                receipt=build.build_owner_store(source,root/'candidate',project_raw=True,private_audit_dir=root/'private')
            projected.assert_called_once_with([feature])
            self.assertEqual(events,['language','owners'])
            self.assertEqual(receipt['published_language_features'],1)

    def test_late_failure_retains_partial_artifacts_but_no_active_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=self.input_file(root,[]);out=root/'candidate';private=root/'private'
            with patch.object(build.OwnerStoreWriter,'add_entry',side_effect=ValueError('synthetic owner failure')):
                with self.assertRaisesRegex(ValueError,'synthetic owner failure'):
                    build.build_owner_store(source,out,project_raw=True,private_audit_dir=private)
            self.assertTrue((out/'plc_facts.sqlite').exists())
            self.assertTrue((private/'projection-receipts.jsonl.gz').exists())
            self.assertFalse((out/'instruction_definitions.manifest.json').exists())
            self.assertFalse((out/'owner-store-build-receipt.json').exists())
            failure=json.loads((private/'projection-failure.json').read_text())
            self.assertEqual(failure['phase'],'owner_projection')
            self.assertFalse(failure['conventional_manifest_present'])
            original=(private/'projection-failure.json').read_bytes()
            with self.assertRaises(ValueError):
                build.build_owner_store(source,out,project_raw=True,private_audit_dir=private)
            self.assertEqual((private/'projection-failure.json').read_bytes(),original)


if __name__=='__main__':unittest.main()
