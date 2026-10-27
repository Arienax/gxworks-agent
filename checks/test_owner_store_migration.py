"""Synthetic sealed-store migration boundary and exactness checks."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from migrate_instruction_owner_store import migrate
from checks.test_instruction_owner_store import fixture
from knowledge.runtime_fact_publication import OwnerStoreWriter
from plc.instruction_owner_store import OwnerDefinitionStore,MANIFEST_NAME

class MigrationTests(unittest.TestCase):
    def source(self,root):
        directory=root/'source';directory.mkdir();raw=fixture()
        w=OwnerStoreWriter(directory/'plc_facts.sqlite',{k:v for k,v in raw.items() if k!='entries'})
        for entry in raw['entries']:w.add_entry(entry)
        w.finalize(directory/MANIFEST_NAME);w.close();return directory/MANIFEST_NAME
    def test_exact_migration_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);m=self.source(root);old=(m.parent/'plc_facts.sqlite').read_bytes();r=migrate(m,root/'new')
            self.assertFalse(r['release_accepted']);self.assertTrue(r['fact_transport_roundtrip_verified']);self.assertEqual(old,(m.parent/'plc_facts.sqlite').read_bytes())
            a=OwnerDefinitionStore(m);b=OwnerDefinitionStore(root/'new'/MANIFEST_NAME)
            try:
                for h in a.iter_owner_headers():self.assertEqual(a.get_entry(h['opcode'],cpu=h['target_model'],vendor=h['vendor']),b.get_entry(h['opcode'],cpu=h['target_model'],vendor=h['vendor']))
            finally:a.close();b.close()
            self.assertTrue((root/'new'/'instruction_owners.sqlite').is_file())
            self.assertFalse((root/'new'/'plc_facts.sqlite').exists())
            self.assertTrue((root/'new'/'owner-store-storage-audit.json').is_file())
    def test_existing_nested_and_symlink_output_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);m=self.source(root)
            for out in (m.parent,m.parent/'nested',root):
                with self.assertRaises(ValueError):migrate(m,out)
            link=root/'link'
            try:
                link.symlink_to(root/'source',target_is_directory=True)
            except OSError as error:
                if getattr(error, 'winerror', None) != 1314:
                    raise
                with patch.object(Path, 'is_symlink', lambda value: value == link):
                    with self.assertRaisesRegex(ValueError, 'ymlink'):migrate(m,link/'new')
            else:
                with self.assertRaisesRegex(ValueError, 'ymlink'):migrate(m,link/'new')
    def test_wal_and_low_space_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);m=self.source(root);wal=Path(str(m.parent/'plc_facts.sqlite')+'-wal');wal.write_bytes(b'')
            with self.assertRaises(ValueError):migrate(m,root/'new')
            wal.unlink()
            with patch('migrate_instruction_owner_store.shutil.disk_usage',return_value=type('Usage',(),{'free':0})()):
                with self.assertRaises(ValueError):migrate(m,root/'new')
            self.assertFalse((root/'new').exists())
    def test_mid_failure_retains_partial_without_final_manifest(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);m=self.source(root)
            with patch('migrate_instruction_owner_store.pack_fact_row',side_effect=ValueError('injected')):
                with self.assertRaises(ValueError):migrate(m,root/'new')
            self.assertFalse((root/'new'/MANIFEST_NAME).exists());self.assertTrue((root/'new'/'migration-failure.json').exists());self.assertTrue((root/'new'/'instruction_owners.sqlite').exists())
    def test_missing_or_tampered_pool_rows_fail_without_manifest(self):
        for table in ('runtime_values','runtime_sources'):
            for action in ('delete','tamper'):
                with self.subTest(table=table,action=action),tempfile.TemporaryDirectory() as t:
                    root=Path(t);m=self.source(root);db=m.parent/'plc_facts.sqlite'
                    with closing(sqlite3.connect(db)) as c, c:
                        if action=='delete':c.execute('DELETE FROM '+table)
                        else:c.execute('UPDATE '+table+" SET payload_json='{}'")
                    v=json.loads(m.read_text());v['database']['bytes']=db.stat().st_size;v['database']['sha256']=hashlib.sha256(db.read_bytes()).hexdigest();m.write_text(json.dumps(v))
                    with self.assertRaises(ValueError):migrate(m,root/'new')
                    self.assertFalse((root/'new'/MANIFEST_NAME).exists())
    def test_invalid_dependencies_or_scope_rejected_after_resigning(self):
        for field,value in (('depends_on',['missing']),('scope',{'forms':['OTHER'],'models':['FX3U']})):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as t:
                root=Path(t);m=self.source(root);db=m.parent/'plc_facts.sqlite'
                with closing(sqlite3.connect(db)) as c, c:
                    for identity,text in c.execute('SELECT id,payload_json FROM runtime_fact_fields').fetchall():
                        data=json.loads(text)
                        if data['field']==field:
                            data['value']=value;text=json.dumps(data,sort_keys=True,separators=(',',':'))
                            c.execute('UPDATE runtime_fact_fields SET payload_json=?,sha256=? WHERE id=?',(text,hashlib.sha256(text.encode()).hexdigest(),identity));break
                v=json.loads(m.read_text());v['database']['bytes']=db.stat().st_size;v['database']['sha256']=hashlib.sha256(db.read_bytes()).hexdigest();m.write_text(json.dumps(v))
                with self.assertRaises(ValueError):migrate(m,root/'new')
                self.assertFalse((root/'new'/MANIFEST_NAME).exists())
    def test_invalid_fact_container_is_not_normalized(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);m=self.source(root);db=m.parent/'plc_facts.sqlite'
            with closing(sqlite3.connect(db)) as c, c:
                owner,text=c.execute('SELECT owner,payload_json FROM runtime_definitions LIMIT 1').fetchone()
                packet=json.loads(text);packet['value']['facts']={};text=json.dumps(packet)
                c.execute('UPDATE runtime_definitions SET payload_json=? WHERE owner=?',(text,owner))
                c.execute('UPDATE runtime_owner_index SET packet_sha256=? WHERE owner=?',(hashlib.sha256(text.encode()).hexdigest(),owner))
            v=json.loads(m.read_text());v['database']['bytes']=db.stat().st_size;v['database']['sha256']=hashlib.sha256(db.read_bytes()).hexdigest();m.write_text(json.dumps(v))
            with self.assertRaises(ValueError):migrate(m,root/'new')
            self.assertFalse((root/'new'/MANIFEST_NAME).exists())
    def test_source_changed_during_migration_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);m=self.source(root)
            from migrate_instruction_owner_store import audit_storage
            def alter(path):
                x=audit_storage(path);m.write_text(m.read_text()+'\n');return x
            with patch('migrate_instruction_owner_store.audit_storage',side_effect=alter):
                with self.assertRaises(ValueError):migrate(m,root/'new')
            self.assertFalse((root/'new'/MANIFEST_NAME).exists())
if __name__=='__main__':unittest.main()
