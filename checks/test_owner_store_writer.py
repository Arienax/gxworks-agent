"""Incremental producer and exact shared-field regressions, synthetic only."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import pytest
from unittest.mock import patch
from checks.test_instruction_owner_store import fixture
from knowledge.runtime_fact_publication import OwnerStoreWriter,write_runtime_sqlite,write_owner_manifest
from knowledge.runtime_fact_projection import intern_runtime_sources
from plc.instruction_owner_store import OwnerDefinitionStore


class WriterTests(unittest.TestCase):
    def test_incremental_exact_roundtrip_and_technical_index(self):
        raw=fixture();raw['entries'][0]['facts'][0]['value']={'rule':'UNIQUE_TECHNICAL_SIGNAL','duration':9973,'unit':'microseconds'}
        raw['entries'][0]['facts'][0]['conditions']=[{'context':'UNIQUE_CONDITIONAL_CONTEXT','equals':True}]
        before=copy.deepcopy(raw)
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.sqlite';manifest=Path(tmp)/'instruction_definitions.manifest.json'
            writer=OwnerStoreWriter(db,{k:v for k,v in raw.items() if k!='entries'})
            for entry in raw['entries']:writer.add_entry(entry)
            receipt=writer.finalize(manifest);self.assertEqual(receipt['sqlite']['owners'],2)
            self.assertGreater(receipt['sqlite']['exact_field_objects'],0)
            store=OwnerDefinitionStore(manifest)
            for entry in raw['entries']:self.assertEqual(store.get_entry(entry['opcode'],cpu=entry['target_model']),entry)
            store.close()
            with closing(sqlite3.connect(db)) as read, read:
                for token in ('UNIQUE_TECHNICAL_SIGNAL','9973','microseconds','UNIQUE_CONDITIONAL_CONTEXT'):
                    self.assertGreater(read.execute('SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?',(token,)).fetchone()[0],0)
                self.assertNotIn('source_refs',read.execute('SELECT text FROM chunks LIMIT 1').fetchone()[0])
                self.assertNotIn('value_ref',read.execute('SELECT text FROM chunks LIMIT 1').fetchone()[0])
        self.assertEqual(raw,before)

    def test_stream_writer_rejects_unknown_provenance_and_dangling_dependency(self):
        for change in ('private','dependency'):
            raw=fixture();entry=raw['entries'][0]
            if change=='private':entry['facts'][0]['value']['manual_text']='private original'
            else:entry['facts'][0]['depends_on']=['missing']
            with tempfile.TemporaryDirectory() as tmp:
                writer=OwnerStoreWriter(Path(tmp)/'facts.sqlite',{k:v for k,v in raw.items() if k!='entries'})
                try:
                    with self.assertRaises(ValueError):writer.add_entry(entry)
                finally:writer.close()
                self.assertFalse((Path(tmp)/'instruction_definitions.manifest.json').exists())

    def test_field_hash_collision_is_rejected(self):
        class ConstantHash:
            def hexdigest(self):return 'a'*64
        raw=fixture();pooled=intern_runtime_sources(raw,intern_values=True)
        with tempfile.TemporaryDirectory() as tmp:
            with patch('knowledge.runtime_fact_publication.hashlib.sha256',return_value=ConstantHash()):
                with self.assertRaises(ValueError):write_runtime_sqlite(pooled,Path(tmp)/'facts.sqlite',field_pooling=True)

    def test_missing_shared_field_rejected(self):
        raw=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.sqlite';manifest=Path(tmp)/'instruction_definitions.manifest.json'
            writer=OwnerStoreWriter(db,{k:v for k,v in raw.items() if k!='entries'})
            writer.add_entry(raw['entries'][0]);writer.finalize(manifest)
            with closing(sqlite3.connect(db)) as edit, edit:edit.execute('DELETE FROM runtime_fact_fields')
            m=json.loads(manifest.read_text());m['database'].update(bytes=db.stat().st_size,sha256=hashlib.sha256(db.read_bytes()).hexdigest());manifest.write_text(json.dumps(m))
            store=OwnerDefinitionStore(manifest)
            try:
                with self.assertRaises(ValueError):store.get_entry('PWM',cpu='FX5U')
            finally:store.close()

if __name__=='__main__':unittest.main()


@pytest.mark.parametrize('layout', ['current', 'source-pool', 'field-pool', 'fact-rows'])
def test_new_owner_builder_keeps_semantic_revision_independent_of_storage(tmp_path, layout):
    from scripts.build_instruction_owner_store import _build_owner_store
    raw=fixture();raw['fact_bundle_version']='programming-semantics-v5'
    source=tmp_path/'reviewed.json';source.write_text(json.dumps(raw),encoding='utf-8')
    output=tmp_path/'output'
    _build_owner_store(source,output,storage_layout=layout)
    assert (output/'instruction_owners.sqlite').is_file()
    assert not (output/'plc_facts.sqlite').exists()
    store=OwnerDefinitionStore(output/'instruction_definitions.manifest.json')
    try:
        assert store.fact_bundle_version=='programming-semantics-v5'
        for entry in raw['entries']:
            assert store.get_entry(entry['opcode'],cpu=entry['target_model'])==entry
    finally:
        store.close()
