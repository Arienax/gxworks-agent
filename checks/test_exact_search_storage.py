"""Exact text sharing preserves retrieval cardinality and BM25 corpus statistics."""
import sqlite3
import unittest
from unittest.mock import patch
from knowledge.runtime_fact_publication import _SCHEMA_SQL
from knowledge.exact_search_storage import create_search_pool,insert_chunk,validate_search_schema,validate_search_rows
from knowledge.index_access import fetch_chunks,schema


class ExactSearchStorageTests(unittest.TestCase):
    def connection(self, pooled):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript(_SCHEMA_SQL)
        if pooled:create_search_pool(db)
        self.addCleanup(db.close)
        return db

    def populate(self,db):
        for n in range(1,8):
            insert_chunk(db,(str(n),'PWM 9973 milliseconds timing' if n<6 else 'MOV word transfer', '{}', '["FX5U"]' if n%2 else '["Q00"]','PWM','instruction','authored_fact','fact_representation_reviewed'),rowid=n*3)
        db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")

    def test_exact_view_rows_and_bm25_with_scoped_limit(self):
        old,new=self.connection(False),self.connection(True)
        self.populate(old);self.populate(new)
        self.assertEqual([tuple(r) for r in old.execute('select rowid,* from chunks order by rowid')], [tuple(r)[:1]+tuple(r)[2:] for r in new.execute('select rowid,* from chunks order by rowid')])
        self.assertEqual(new.execute('select count(*) from runtime_search_documents').fetchone()[0],2)
        for query in ('PWM','9973','milliseconds','MOV','timing OR word'):
            for limit in (1,2,100):
                sql="select rowid,bm25(chunks_fts) from chunks_fts where chunks_fts match ? and rowid in(select rowid from chunks where plc_models='[\"FX5U\"]') order by bm25(chunks_fts),rowid limit ?"
                self.assertEqual([tuple(r) for r in old.execute(sql,(query,limit))],[tuple(r) for r in new.execute(sql,(query,limit))])

    def test_fetch_chunks_works_by_id_and_rowid(self):
        db=self.connection(True);self.populate(db)
        result=fetch_chunks(db,schema(db),[('id','1'),('rowid',6)])
        self.assertEqual(result[('id','1')]['text'],'PWM 9973 milliseconds timing')
        self.assertEqual(result[('rowid','6')]['id'],'2')

    def test_missing_reference_unknown_version_and_hash_tamper_reject(self):
        db=self.connection(True);self.populate(db)
        db.execute("update runtime_search_documents set text='corrupt' where id=1")
        with self.assertRaises(ValueError):fetch_chunks(db,schema(db),[('id','1')])
        db.execute('delete from runtime_search_documents where id=1')
        with self.assertRaises(ValueError):validate_search_schema(db)
        db.execute("update meta set value='unknown' where key='search_serialization'")
        with self.assertRaises(ValueError):validate_search_schema(db)

    def test_collision_is_not_equality(self):
        db=self.connection(True)
        digest=type('Digest',(),{'hexdigest':lambda self:'0'*64})()
        with patch('knowledge.exact_search_storage.hashlib.sha256',return_value=digest):
            insert_chunk(db,('one','one','{}','[]','','instruction','',''))
            with self.assertRaisesRegex(ValueError,'collision'):insert_chunk(db,('two','two','{}','[]','','instruction','',''))

    def test_legacy_and_unversioned_pool(self):
        db=self.connection(False);validate_search_schema(db)
        create_search_pool(db);db.execute('delete from meta')
        with self.assertRaises(ValueError):validate_search_schema(db)

    def test_new_writer_tuple_rows_roundtrip_with_legacy_still_supported(self):
        import tempfile,json
        from pathlib import Path
        from test_instruction_owner_store import fixture
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        from plc.instruction_owner_store import OwnerDefinitionStore
        raw=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.sqlite';manifest=Path(tmp)/'instruction_definitions.manifest.json'
            writer=OwnerStoreWriter(db,{k:v for k,v in raw.items() if k!='entries'},fact_rows=True,search_pooling=True)
            for entry in raw['entries']:writer.add_entry(entry)
            writer.finalize(manifest)
            store=OwnerDefinitionStore(manifest)
            try:
                for entry in raw['entries']:self.assertEqual(store.get_entry(entry['opcode'],cpu=entry['target_model']),entry)
            finally:store.close()
            with sqlite3.connect(db) as read:
                self.assertEqual(read.execute("select value from meta where key='runtime_serialization'").fetchone()[0],'owner-field-row-v3')
                self.assertFalse(read.execute("select 1 from sqlite_master where name='seen_fact_ids'").fetchone())
                packets=[json.loads(row[0]) for row in read.execute('select payload_json from runtime_definitions')]
                self.assertTrue(all(isinstance(f,list) for p in packets for f in p['value']['facts']))

    def test_storage_audit_counts_stored_pool_not_expanded_view(self):
        import tempfile,sys
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
        from audit_runtime_sqlite_storage import audit_storage
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'facts.sqlite'
            with sqlite3.connect(path) as db:
                db.executescript(_SCHEMA_SQL);create_search_pool(db);self.populate(db)
                db.execute('INSERT INTO runtime_fact_fields VALUES (1,?,?)',('{"field":"id","value":"private-identity"}','x'))
            report=audit_storage(path)
            self.assertNotIn('chunks',report['logical_columns'])
            self.assertEqual(report['logical_columns']['runtime_search_documents']['rows'],2)
            self.assertEqual(report['logical_columns']['runtime_chunk_bindings']['rows'],7)
            self.assertEqual(report['field_pool_distribution'][0]['field'],'id')
            self.assertNotIn('private-identity',str(report))

    def test_nonempty_table_not_replaced(self):
        db=self.connection(False);self.populate(db)
        with self.assertRaises(ValueError):create_search_pool(db)
        self.assertEqual(db.execute('select count(*) from chunks').fetchone()[0],7)

if __name__=='__main__':unittest.main()
