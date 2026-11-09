"""Packaging must use the authenticated owner pair, not require a giant JSON."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from test_instruction_owner_store import fixture
from knowledge.runtime_fact_publication import OwnerStoreWriter

spec=importlib.util.spec_from_file_location('owner_resource_collector',Path(__file__).resolve().parents[1]/'scripts/runtime_knowledge_resources.py')
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)

class ResourceTests(unittest.TestCase):
    def test_catalog_local_manifest_replaces_only_definition_blob(self):
        raw=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);catalog=root/'resources/instructions/mitsubishi';catalog.mkdir(parents=True)
            for name in collector.INSTRUCTION_FILES:
                if name!='instruction_definitions.json.gz':(catalog/name).write_text('{}')
            writer=OwnerStoreWriter(catalog/'facts.sqlite',{k:v for k,v in raw.items() if k!='entries'})
            for entry in raw['entries']:writer.add_entry(entry)
            manifest=catalog/'instruction_definitions.manifest.json';writer.finalize(manifest)
            resources=collector.runtime_instruction_data(root)
            names={Path(path).name for path,target in resources}
            self.assertIn(manifest.name,names);self.assertIn('facts.sqlite',names)
            self.assertNotIn('instruction_definitions.json.gz',names)
            self.assertIn('common.json',names)
            manifest.write_text('{}')
            with self.assertRaises(ValueError):collector.runtime_instruction_data(root)

if __name__=='__main__':unittest.main()
