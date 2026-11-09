"""Synthetic random-access storage, not a claim of native PLC support."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from knowledge.runtime_fact_projection import intern_runtime_sources
from knowledge.runtime_fact_publication import write_runtime_sqlite, write_owner_manifest
from plc.instruction_owner_store import OwnerDefinitionStore


def fixture():
    entries=[]
    for model in ('FX5U','FX5UC'):
        facts=[]
        for identity, deps in [('base',[]),('effect',['base'])]:
            facts.append({'id':identity,'dimension':'effects.synthetic','value':{'flag':True},
                          'scope':{'models':[model],'forms':['PWM']},'depends_on':deps,
                          'status':'source_verified','verification':'source_checked',
                          'sources':[{'manual_id':'synthetic','revision':'A'}]})
        entries.append({'vendor':'mitsubishi','opcode':'PWM','target_model':model,'facts':facts})
    return {'schema_version':1,'method':'instruction-material-compiler-v1','corpus_kind':'authored_plc_facts_v1',
            'fact_bundle_version':'synthetic:owner-test','entries':entries}


def build(root):
    raw=fixture(); pooled=intern_runtime_sources(raw,intern_values=True)
    db=Path(root)/'plc_facts.sqlite';manifest=Path(root)/'instruction_definitions.manifest.json'
    write_runtime_sqlite(pooled,db);write_owner_manifest(pooled,db,manifest)
    return raw,db,manifest


def refresh_manifest(db,manifest):
    data=json.loads(manifest.read_text());data['database'].update(bytes=db.stat().st_size,sha256=hashlib.sha256(db.read_bytes()).hexdigest());manifest.write_text(json.dumps(data))


class OwnerStoreTests(unittest.TestCase):
    def test_light_header_preserves_absent_null_and_boolean_without_hydration(self):
        for marker in ('absent', None, False, True):
            raw=fixture()
            if marker != 'absent':raw['entries'][0]['call_available']=marker
            raw['entries'][0]['call_admission_details']={'declared_scope':'explicit'}
            with tempfile.TemporaryDirectory() as tmp:
                pooled=intern_runtime_sources(raw,intern_values=True)
                db=Path(tmp)/'facts.sqlite';manifest=Path(tmp)/'instruction_definitions.manifest.json'
                write_runtime_sqlite(pooled,db,field_pooling=True);write_owner_manifest(pooled,db,manifest)
                store=OwnerDefinitionStore(manifest)
                try:
                    header=store.get_owner_header(('mitsubishi','PWM','FX5U'))
                    if marker == 'absent':self.assertNotIn('call_available',header)
                    else:self.assertIs(header['call_available'],marker)
                    self.assertEqual(header['call_admission_details'],{'declared_scope':'explicit'})
                    header['call_admission_details']['declared_scope']='changed'
                    self.assertEqual(store.get_owner_header(('mitsubishi','PWM','FX5U'))['call_admission_details']['declared_scope'],'explicit')
                    self.assertEqual(store.stats()['hydrated_owner_count'],0)
                finally:store.close()

    def test_no_owner_hydration_until_requested_and_no_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw,db,manifest=build(tmp);store=OwnerDefinitionStore(manifest)
            self.assertEqual(store.stats()['hydrated_owner_count'],0)
            self.assertEqual(len(list(store.iter_owner_headers())),2)
            self.assertEqual(store.stats()['hydrated_owner_count'],0)
            row=store.get_entry('PWM',cpu='FX5U')
            self.assertEqual(row,raw['entries'][0]);self.assertEqual(store.stats()['hydrated_owner_count'],1)
            row['facts'][0]['value']['flag']=False
            self.assertTrue(store.get_entry('PWM',cpu='FX5UC')['facts'][0]['value']['flag'])
            self.assertTrue(store.get_entry('PWM',cpu='FX5U')['facts'][0]['value']['flag'])
            self.assertIsNone(store.get_entry('OTHER',cpu='FX5U'))
            with self.assertRaises(sqlite3.OperationalError):store._db.execute('DELETE FROM runtime_sources')
            store.close()

    def test_manifest_path_and_digest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,db,manifest=build(tmp);original=json.loads(manifest.read_text())
            for name in ('../plc_facts.sqlite','/tmp/plc_facts.sqlite','C:\\facts.sqlite','https://bad/facts.sqlite'):
                bad=copy.deepcopy(original);bad['database']['filename']=name;manifest.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):OwnerDefinitionStore(manifest)
            bad=copy.deepcopy(original);bad['database']['sha256']='0'*64;manifest.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):OwnerDefinitionStore(manifest)

    def test_source_withdrawal_and_changed_resource_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,db,manifest=build(tmp);store=OwnerDefinitionStore(manifest)
            with sqlite3.connect(db) as edit:edit.execute('DELETE FROM runtime_sources')
            self.assertFalse(store.current())
            with self.assertRaises(ValueError):store.get_entry('PWM',cpu='FX5U')
            store.close();refresh_manifest(db,manifest)
            store=OwnerDefinitionStore(manifest)
            with self.assertRaises(ValueError):store.get_entry('PWM',cpu='FX5U')
            store.close()

    def test_dangling_dependency_rejected_even_with_new_packet_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,db,manifest=build(tmp)
            with sqlite3.connect(db) as edit:
                owner,text=edit.execute('SELECT owner,payload_json FROM runtime_definitions LIMIT 1').fetchone()
                packet=json.loads(text);packet['value']['facts'][0]['depends_on']=['missing'];text=json.dumps(packet)
                edit.execute('UPDATE runtime_definitions SET payload_json=? WHERE owner=?',(text,owner))
                edit.execute('UPDATE runtime_owner_index SET packet_sha256=? WHERE owner=?',(hashlib.sha256(text.encode()).hexdigest(),owner))
            refresh_manifest(db,manifest);store=OwnerDefinitionStore(manifest)
            with self.assertRaises(ValueError):store.get_entry('PWM',cpu='FX5U')
            store.close()

if __name__=='__main__':unittest.main()
