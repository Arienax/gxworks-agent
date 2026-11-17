"""Packaging must use the authenticated owner pair, not require a giant JSON."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import os
import shutil
from unittest.mock import patch
from checks.test_instruction_owner_store import fixture
from knowledge.runtime_fact_publication import OwnerStoreWriter

spec=importlib.util.spec_from_file_location('owner_resource_collector',Path(__file__).resolve().parents[1]/'scripts/runtime_knowledge_resources.py')
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)

class ResourceTests(unittest.TestCase):
    def test_package_audit_checks_the_skill_beside_knowledge(self):
        with tempfile.TemporaryDirectory() as tmp:
            package=Path(tmp)
            for relative,destination in collector.FILES:
                target=package/destination/Path(relative).name
                target.parent.mkdir(parents=True,exist_ok=True);target.write_text('{}')
            with patch.object(collector,'assert_fact_database'):
                # The executable spec also includes the skill's referenced docs.
                (package/'skills/gxworks-mcp-router/README.md').write_text('Skill reference')
                collector.audit_packaged_knowledge(package)
                extra=package/'knowledge/unlisted.json';extra.write_text('{}')
                with self.assertRaisesRegex(ValueError,'unlisted.json'):
                    collector.audit_packaged_knowledge(package)
                extra.unlink()
                (package/'skills/gxworks-mcp-router/SKILL.md').unlink()
                with self.assertRaisesRegex(ValueError,'SKILL.md'):
                    collector.audit_packaged_knowledge(package)

    def test_catalog_local_manifest_replaces_only_definition_blob(self):
        raw=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);catalog=root/'resources/instructions/mitsubishi';catalog.mkdir(parents=True)
            for name in collector.INSTRUCTION_FILES:
                if name!='instruction_definitions.json.gz':(catalog/name).write_text('{}')
            # Frozen FX5 research inputs may exist locally but must not ship.
            for name in ('pwm_contract.generated.json', 'pwm_reviewed_facts.json'):
                (catalog/name).write_text('{}')
            writer=OwnerStoreWriter(catalog/'facts.sqlite',{k:v for k,v in raw.items() if k!='entries'})
            for entry in raw['entries']:writer.add_entry(entry)
            manifest=catalog/'instruction_definitions.manifest.json';writer.finalize(manifest)
            resources=collector.runtime_instruction_data(root)
            names={Path(path).name for path,target in resources}
            self.assertIn(manifest.name,names);self.assertIn('facts.sqlite',names)
            self.assertNotIn('instruction_definitions.json.gz',names)
            self.assertIn('common.json',names)
            self.assertNotIn('pwm_contract.generated.json',names)
            self.assertNotIn('pwm_reviewed_facts.json',names)
            self.assertNotIn('instruction_behavior_facts.json',names)
            self.assertNotIn('operand_semantic_coverage_summary.json',names)
            manifest.write_text('{}')
            with self.assertRaises(ValueError):collector.runtime_instruction_data(root)

    def test_build_cache_reuses_exact_copies_but_rejects_changed_database(self):
        raw=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';catalog=root/'resources/instructions/mitsubishi';catalog.mkdir(parents=True)
            for name in collector.INSTRUCTION_FILES:
                if name!='instruction_definitions.json.gz':(catalog/name).write_text('{}')
            database=catalog/'facts.sqlite'
            writer=OwnerStoreWriter(database,{k:v for k,v in raw.items() if k!='entries'})
            for entry in raw['entries']:writer.add_entry(entry)
            writer.finalize(catalog/'instruction_definitions.manifest.json')
            cache=Path(tmp)/'cache';cache.mkdir()
            with patch.dict(os.environ,{'GX_RELEASE_VALIDATION_CACHE':str(cache)}):
                with patch.object(collector,'assert_fact_database',side_effect=ValueError('invalid facts')):
                    with self.assertRaisesRegex(ValueError,'invalid facts'):
                        collector.runtime_instruction_data(root)
                self.assertEqual(list(cache.iterdir()),[])
                with patch.object(collector,'assert_fact_database',wraps=collector.assert_fact_database) as scan:
                    collector.runtime_instruction_data(root)
                    staged=Path(tmp)/'staged';shutil.copytree(root,staged)
                    collector.runtime_instruction_data(staged)
                    self.assertEqual(scan.call_count,1)
                    # Retain file size: a cached result must not bypass integrity.
                    with (staged/'resources/instructions/mitsubishi/facts.sqlite').open('r+b') as stream:
                        stream.seek(-1,2);last=stream.read(1);stream.seek(-1,2);stream.write(bytes([last[0]^1]))
                    with self.assertRaisesRegex(ValueError,'integrity mismatch'):
                        collector.runtime_instruction_data(staged)
            with patch.object(collector,'assert_fact_database',wraps=collector.assert_fact_database) as scan:
                collector.runtime_instruction_data(root)
                self.assertEqual(scan.call_count,1)

if __name__=='__main__':unittest.main()
