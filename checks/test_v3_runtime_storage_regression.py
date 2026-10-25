"""Independent exact-storage regressions, synthetic public-safe fixtures only."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from knowledge.exact_search_storage import create_search_pool,insert_chunk,validate_search_schema,validate_search_rows,COLUMNS
from knowledge import index_access


def search_database(pooled):
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE chunks(id TEXT PRIMARY KEY,text TEXT,metadata_json TEXT,plc_models TEXT,instruction_opcode TEXT,chunk_type TEXT,content_origin TEXT,distribution_status TEXT); CREATE VIRTUAL TABLE chunks_fts USING fts5(text,content="chunks",content_rowid="rowid");')
    if pooled:create_search_pool(c)
    for rowid,model,text in [(7,'FX3U','PWM output timer 中文'),(19,'Q00','PWM output timer 中文'),(41,'FX3U','MOV unique_value')]:
        insert_chunk(c,(str(rowid),text,'{}',json.dumps([model]),'PWM','instruction','authored_fact','fact_representation_reviewed'),rowid=rowid)
    c.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    return c


class ExactSearchIndependentTests(unittest.TestCase):
    def test_old_v2_and_pool_expose_identical_nine_column_rows_and_bm25(self):
        old=search_database(False);new=search_database(True)
        self.addCleanup(old.close);self.addCleanup(new.close)
        sql='SELECT rowid,'+','.join(COLUMNS)+' FROM chunks ORDER BY rowid'
        self.assertEqual([tuple(r)for r in old.execute(sql)],[tuple(r)for r in new.execute(sql)])
        self.assertEqual(new.execute('SELECT count(*) FROM runtime_search_documents').fetchone()[0],2)
        self.assertEqual(new.execute('SELECT count(*) FROM chunks_fts').fetchone()[0],3)
        for term in ('PWM','output','timer','MOV','中文'):
            for k in (1,2,10):
                sql='SELECT rowid,bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?'
                self.assertEqual([tuple(r)for r in old.execute(sql,(term,k))],[tuple(r)for r in new.execute(sql,(term,k))])
    def test_missing_reference_fails_schema_not_inner_join_silent_loss(self):
        c=search_database(True);self.addCleanup(c.close)
        c.execute('DELETE FROM runtime_search_documents WHERE id=(SELECT document_id FROM runtime_chunk_bindings WHERE rowid=7)')
        self.assertEqual(c.execute('SELECT count(*) FROM chunks').fetchone()[0],3)
        with self.assertRaises(ValueError):validate_search_schema(c)
        rows=c.execute('SELECT rowid AS _chunk_rowid,* FROM chunks WHERE rowid=7').fetchall()
        with self.assertRaises(ValueError):validate_search_rows(c,rows)
    def test_selected_bad_content_hash_fails_actual_fetch_consumer(self):
        c=search_database(True);self.addCleanup(c.close);schema=index_access.schema(c)
        c.execute("UPDATE runtime_search_documents SET text='changed secret source' WHERE id=(SELECT document_id FROM runtime_chunk_bindings WHERE rowid=7)")
        with self.assertRaises(ValueError):index_access.fetch_chunks(c,schema,[('rowid',7)])
    def test_unknown_or_missing_version_rejected(self):
        for marker in ('unknown',None):
            c=search_database(True);self.addCleanup(c.close)
            if marker is None:c.execute("DELETE FROM meta WHERE key='search_serialization'")
            else:c.execute("UPDATE meta SET value=? WHERE key='search_serialization'",(marker,))
            with self.assertRaises(ValueError):validate_search_schema(c)
    def test_document_hash_collision_not_assumed_equal(self):
        c=search_database(True);self.addCleanup(c.close)
        class Hash:
            def hexdigest(self):return c.execute('SELECT sha256 FROM runtime_search_documents LIMIT 1').fetchone()[0]
        with patch('knowledge.exact_search_storage.hashlib.sha256',return_value=Hash()):
            with self.assertRaises(ValueError):insert_chunk(c,('new','different collision','{}','["FX3U"]','PWM','instruction','authored_fact','fact_representation_reviewed'))
    def test_pool_conversion_cannot_destroy_nonempty_legacy_chunks(self):
        c=search_database(False);self.addCleanup(c.close)
        with self.assertRaises(ValueError):create_search_pool(c)
        self.assertEqual(c.execute('SELECT count(*) FROM chunks').fetchone()[0],3)

class FactRowIndependentTests(unittest.TestCase):
    def setUp(self):
        from knowledge.fact_field_codec import pack_fact_row,unpack_fact_row,POOLED_FIELDS
        self.pack=pack_fact_row;self.unpack=unpack_fact_row;self.fields=POOLED_FIELDS
        self.pool={};self.identities={}
    def intern(self,field,value):
        text=json.dumps([field,value],sort_keys=True,separators=(',',':'),ensure_ascii=False)
        if text not in self.identities:
            identity=len(self.pool)+1;self.identities[text]=identity;self.pool[identity]={'field':field,'value':copy.deepcopy(value)}
        return self.identities[text]
    def test_every_axis_unknown_fields_types_order_and_identity_roundtrip(self):
        variants=[None,False,True,0,1,1.0,[],{},['FX3U','Q00'],['Q00','FX3U'],{'literal':'PRIVATE_EXACT_VALUE','nested':[None,False,1,1.0]}]
        for i,value in enumerate(variants):
            fact={'id':'same-owner-local-id','dimension':'constraints.synthetic','status':'unknown','scope':{'models':['FX3U'],'languages':['st'],'forms':['PWM']},'conditions':[{'branch':i}],'depends_on':['later','first'],'source_refs':['source-b','source-a'],'value_ref':'exact-value','verification':value,'unknown_future_field':{'payload':value}}
            for fields in (self.fields,self.fields|{'id'}):
                with self.subTest(value=value,id_pooled='id'in fields):
                    row=self.pack(fact,self.intern,fields=fields);decoded=self.unpack(row,self.pool.get)
                    self.assertEqual(json.dumps(decoded,sort_keys=True),json.dumps(fact,sort_keys=True))
                    decoded['unknown_future_field']['mutated']=True
                    self.assertNotIn('mutated',self.unpack(row,self.pool.get)['unknown_future_field'])
    def test_absent_and_explicit_null_remain_distinct(self):
        absent={'id':'x'};null={'id':'x','conditions':None}
        a=self.pack(absent,self.intern);b=self.pack(null,self.intern)
        self.assertNotEqual(a,b);self.assertEqual(self.unpack(a,self.pool.get),absent);self.assertEqual(self.unpack(b,self.pool.get),null)
    def test_json_boolean_integer_and_float_pool_values_do_not_alias(self):
        rows=[self.pack({'status':value},self.intern)for value in (True,1,1.0)]
        self.assertEqual(len(self.pool),3)
        self.assertEqual([type(self.unpack(row,self.pool.get)['status'])for row in rows],[bool,int,float])
    def test_bad_shape_refs_unknown_catalog_field_and_inline_collision_fail(self):
        valid=self.pack({'id':'x','status':'unknown'},self.intern);ref=valid[0][0]
        bad=[{},[],[[]],[[],{},'extra'],[{},{}],[[],None],[[True],{}],[[0],{}],[[-1],{}],[[1.0],{}],[[999],{}],[[ref,ref],{}],[[],{'field_refs':[]}],[[ref],{'status':'source_verified'}]]
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):self.unpack(value,self.pool.get)
        with self.assertRaises(ValueError):self.unpack([[ref],{}],lambda _: {'field':'unapproved','value':1})
    def test_nonfinite_and_nonjson_rejected_without_partial_output(self):
        for value in (float('nan'),float('inf'),(1,2),{1:'nonstring-key'}):
            with self.subTest(value=repr(value)),self.assertRaises(ValueError):self.pack({'future_field':value},self.intern)

class V3OwnerStoreIndependentTests(unittest.TestCase):
    def setUp(self):
        from checks.test_instruction_owner_store import fixture
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.db=self.root/'facts.sqlite';self.manifest=self.root/'instruction_definitions.manifest.json';self.raw=fixture()
        for entry in self.raw['entries']:
            for fact in entry['facts']:fact['future_extra']={'typed_values':[None,False,1,1.0],'ordered':['later','first']}
        writer=OwnerStoreWriter(self.db,{k:v for k,v in self.raw.items()if k!='entries'},fact_rows=True,search_pooling=True)
        for entry in self.raw['entries']:writer.add_entry(entry)
        writer.finalize(self.manifest)
    def refresh(self):
        v=json.loads(self.manifest.read_text());v['database'].update(bytes=self.db.stat().st_size,sha256=hashlib.sha256(self.db.read_bytes()).hexdigest());self.manifest.write_text(json.dumps(v))
    def open(self):
        from plc.instruction_owner_store import OwnerDefinitionStore
        s=OwnerDefinitionStore(self.manifest);self.addCleanup(s.close);return s
    def rewrite_first_packet(self,change):
        with sqlite3.connect(self.db)as c:
            owner,text=c.execute('SELECT owner,payload_json FROM runtime_definitions ORDER BY owner LIMIT 1').fetchone();v=json.loads(text);change(v['value']['facts']);text=json.dumps(v,sort_keys=True,separators=(',',':'))
            c.execute('UPDATE runtime_definitions SET payload_json=? WHERE owner=?',(text,owner));c.execute('UPDATE runtime_owner_index SET packet_sha256=? WHERE owner=?',(hashlib.sha256(text.encode()).hexdigest(),owner))
        self.refresh()
    def test_v3_all_fields_roundtrip_unknown_fields_and_repeated_id_pool(self):
        s=self.open()
        for entry in self.raw['entries']:
            value=s.get_entry(entry['opcode'],cpu=entry['target_model'])
            self.assertEqual(json.dumps(value,sort_keys=True),json.dumps(entry,sort_keys=True))
        with sqlite3.connect(self.db)as c:
            self.assertEqual(c.execute("SELECT count(*) FROM runtime_fact_fields WHERE json_extract(payload_json,'$.field')='id'").fetchone()[0],2)
            self.assertEqual(c.execute("SELECT value FROM meta WHERE key='runtime_serialization'").fetchone()[0],'owner-field-row-v3')
    def test_unique_fact_ids_stay_inline_and_do_not_make_id_pool(self):
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        raw=copy.deepcopy(self.raw)
        for entry in raw['entries']:
            for fact in entry['facts']:
                fact['id']+='_'+entry['target_model'];fact['depends_on']=[x+'_'+entry['target_model']for x in fact.get('depends_on',[])]
        db=self.root/'unique.sqlite';writer=OwnerStoreWriter(db,{k:v for k,v in raw.items()if k!='entries'},fact_rows=True)
        for e in raw['entries']:writer.add_entry(e)
        writer.finalize(self.root/'unique.manifest.json')
        with sqlite3.connect(db)as c:self.assertEqual(c.execute("SELECT count(*) FROM runtime_fact_fields WHERE json_extract(payload_json,'$.field')='id'").fetchone()[0],0)
    def test_wrong_row_shape_rejected_after_packet_and_manifest_rehash(self):
        self.rewrite_first_packet(lambda facts:facts.__setitem__(0,{'id':'not-a-v3-row'}))
        s=self.open()
        with self.assertRaises(ValueError):s.get_entry('PWM',cpu='FX5U')
    def test_missing_fact_field_ref_rejected_after_rehash(self):
        self.rewrite_first_packet(lambda facts:facts[0][0].append(99999999));s=self.open()
        with self.assertRaises(ValueError):s.get_entry('PWM',cpu='FX5U')
    def test_wrong_model_scope_rejected_even_after_catalog_and_manifest_rehash(self):
        with sqlite3.connect(self.db)as c:
            identity,text=c.execute("SELECT id,payload_json FROM runtime_fact_fields WHERE json_extract(payload_json,'$.field')='scope' LIMIT 1").fetchone()
            value=json.loads(text);value['value']['models']=['OTHER_MODEL'];text=json.dumps(value,sort_keys=True,separators=(',',':'))
            c.execute('UPDATE runtime_fact_fields SET payload_json=?,sha256=? WHERE id=?',(text,hashlib.sha256(text.encode()).hexdigest(),identity))
        self.refresh();s=self.open()
        with self.assertRaises(ValueError):s.get_entry('PWM',cpu='FX5U')

    def test_wrong_fact_row_version_is_not_decoded_as_legacy(self):
        with sqlite3.connect(self.db)as c:c.execute("UPDATE meta SET value='future-unreviewed' WHERE key='fact_row_serialization'")
        self.refresh()
        with self.assertRaises(ValueError):self.open()
    def test_missing_search_reference_fails_real_store_schema(self):
        with sqlite3.connect(self.db)as c:c.execute('DELETE FROM runtime_search_documents')
        self.refresh()
        with self.assertRaises(ValueError):self.open()
    def test_corrupt_search_marker_schema_rejects_and_broad_returns_no_evidence(self):
        from knowledge import core
        from plc.instructions import InstructionRegistry
        registry=InstructionRegistry([]);registry.load_instruction_owner_store(self.manifest)
        self.addCleanup(registry._definition_owner_store.close)
        with patch.object(index_access,'index_path',return_value=self.db),patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY',registry):
            index_access.close_thread_connection();core._retrieve_cached.cache_clear()
            # Low-level schema is the explicit diagnostic boundary. The legacy
            # public broad facade intentionally maps retrieval errors to [].
            with sqlite3.connect(self.db)as c:c.execute("UPDATE meta SET value='unknown-marker' WHERE key='search_serialization'")
            with sqlite3.connect(self.db)as c:
                c.row_factory=sqlite3.Row
                with self.assertRaises(ValueError):index_access.schema(c)
            self.assertEqual(core.retrieve_broad_knowledge('PWM',plc_model='FX5U',task_type='analysis'),[])
            index_access.close_thread_connection()

    def test_valid_search_binding_substitution_rejected_by_original_manifest(self):
        with sqlite3.connect(self.db)as c:
            ids=[r[0]for r in c.execute('SELECT id FROM runtime_search_documents')]
            self.assertGreaterEqual(len(ids),2)
            c.execute('UPDATE runtime_chunk_bindings SET document_id=?',(ids[-1],))
        with self.assertRaises(ValueError):self.open()

if __name__=='__main__':unittest.main()
