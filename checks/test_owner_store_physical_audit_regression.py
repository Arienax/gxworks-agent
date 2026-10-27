"""Independent physical/logical accounting checks; no user/private fixtures."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from checks.test_instruction_owner_store import fixture
from knowledge.runtime_fact_publication import OwnerStoreWriter


def make_store(root):
    raw=fixture()
    for entry in raw['entries']:
        for fact in entry['facts']:
            fact['value']={'signal':'PRIVATE_TECHNICAL_VALUE_DO_NOT_REPORT','samples':list(range(200))}
    db=root/'facts.sqlite';manifest=root/'instruction_definitions.manifest.json'
    writer=OwnerStoreWriter(db,{k:v for k,v in raw.items()if k!='entries'})
    for entry in raw['entries']:writer.add_entry(entry)
    writer.finalize(manifest)
    return db,manifest


def raw_accounting(db):
    """Separate plain SQLite oracle; no production diagnostic helpers."""
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as conn, conn:
        page_size=conn.execute('PRAGMA page_size').fetchone()[0]
        page_count=conn.execute('PRAGMA page_count').fetchone()[0]
        free=conn.execute('PRAGMA freelist_count').fetchone()[0]
        objects={name:(pages,total) for name,pages,total in conn.execute('SELECT name,count(*),sum(pgsize) FROM dbstat GROUP BY name')}
    return {'page_size':page_size,'page_count':page_count,'free_pages':free,'objects':objects,'bytes':page_size*page_count}

from tools import audit_runtime_sqlite_storage as subject
import subprocess
import sys

class PhysicalAuditIndependentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db,self.manifest=make_store(self.root)
    def test_pages_match_independent_sql_and_file_header(self):
        oracle=raw_accounting(self.db);report=subject.audit_storage(self.db)
        header=self.db.read_bytes()[:100]
        self.assertEqual(header[:16],b'SQLite format 3\x00')
        header_page=int.from_bytes(header[16:18],'big');header_page=65536 if header_page==1 else header_page
        self.assertEqual(header_page,oracle['page_size'])
        self.assertEqual(int.from_bytes(header[28:32],'big'),oracle['page_count'])
        self.assertEqual(report['database_file_bytes'],self.db.stat().st_size)
        self.assertEqual(report['sqlite_page_bytes'],oracle['bytes'])
        self.assertEqual(report['dbstat_attributed_bytes'],sum(v[1] for v in oracle['objects'].values()))
        self.assertEqual(sum(v['allocated_bytes'] for v in report['physical_objects'].values())+report['freelist_bytes'],oracle['bytes'])
        self.assertEqual(report['unattributed_nonfreelist_page_bytes'],0)
        self.assertEqual(report['file_bytes_minus_sqlite_page_bytes'],0)
    def test_fts_shadow_pages_count_once_and_all_indexes_are_owned(self):
        oracle=raw_accounting(self.db);report=subject.audit_storage(self.db)
        fts_expected=sum(size for name,(_,size) in oracle['objects'].items() if name.startswith('chunks_fts_'))
        fts_actual=sum(v['allocated_bytes']for k,v in report['physical_objects'].items()if k.startswith('fts:'))
        self.assertEqual(fts_actual,fts_expected)
        self.assertNotIn('table:chunks_fts',report['physical_objects'])
        with closing(sqlite3.connect(self.db)) as c, c:
            indexes=[name for name, in c.execute("SELECT name FROM sqlite_schema WHERE type='index' AND tbl_name='runtime_owner_index'")]
        self.assertEqual(report['physical_objects']['index:runtime_owner_index']['allocated_bytes'],sum(oracle['objects'][name][1]for name in indexes))
    def test_free_pages_are_not_counted_twice(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE transient_data(x BLOB)');c.executemany('INSERT INTO transient_data VALUES (?)',[(b'x'*10000,)]*40);c.execute('DROP TABLE transient_data')
        oracle=raw_accounting(self.db);self.assertGreater(oracle['free_pages'],0)
        r=subject.audit_storage(self.db)
        self.assertEqual(r['freelist_pages'],oracle['free_pages'])
        self.assertEqual(r['dbstat_attributed_bytes']+r['freelist_bytes'],r['sqlite_page_bytes'])
    def test_logical_field_pool_is_exact_payload_count_not_physical_estimate(self):
        r=subject.audit_storage(self.db)
        with closing(sqlite3.connect(self.db)) as c, c:
            fields={}
            for text, in c.execute('SELECT payload_json FROM runtime_fact_fields'):
                name=json.loads(text)['field'];fields.setdefault(name,[0,0]);fields[name][0]+=1;fields[name][1]+=len(text.encode())
        for row in r['field_pool_distribution']:
            self.assertEqual((row['rows'],row['payload_json_utf8_bytes']),tuple(fields[row['field']]))
            self.assertNotIn('allocated_bytes',row)
        self.assertAlmostEqual(sum(x['share_of_field_pool_json_bytes']for x in r['field_pool_distribution']),1)
        self.assertEqual(sum(x['payload_json_utf8_bytes']for x in r['field_pool_distribution']),r['logical_columns']['runtime_fact_fields']['selected_column_utf8_bytes']['payload_json'])
    def test_report_never_contains_technical_values_or_unknown_names(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE SECRET_PRIVATE_OBJECT(x TEXT)')
            c.execute('INSERT INTO SECRET_PRIVATE_OBJECT VALUES (?)',('SECRET_PRIVATE_BODY',))
            text=json.dumps({'field':'SECRET_PRIVATE_FIELD','value':'SECRET_PRIVATE_BODY'})
            c.execute('INSERT INTO runtime_fact_fields(payload_json,sha256) VALUES (?,?)',(text,hashlib.sha256(text.encode()).hexdigest()))
        r=subject.audit_storage(self.db);text=json.dumps(r)
        for marker in ('PRIVATE_TECHNICAL_VALUE_DO_NOT_REPORT','SECRET_PRIVATE_OBJECT','SECRET_PRIVATE_FIELD','SECRET_PRIVATE_BODY'):
            self.assertNotIn(marker,text)
        self.assertIn('other_table',r['physical_objects'])
        self.assertIn('unknown_field',[x['field']for x in r['field_pool_distribution']])
    def test_no_owner_hydration_and_input_bytes_unchanged(self):
        before=self.db.read_bytes()
        with patch('plc.instruction_owner_store.OwnerDefinitionStore.get_entry',side_effect=AssertionError('owner hydration forbidden')),patch('knowledge.runtime_fact_publication.hydrate_owner_packet',side_effect=AssertionError('owner hydration forbidden')):
            r=subject.audit_storage(self.db)
        self.assertEqual(self.db.read_bytes(),before);self.assertTrue(r['read_only']);self.assertTrue(r['input_unchanged'])
    def test_dbstat_unavailable_keeps_physical_unknown_not_logical_substitution(self):
        with patch.object(subject,'_physical_stats',side_effect=sqlite3.OperationalError('no such table: dbstat')):
            r=subject.audit_storage(self.db)
        self.assertFalse(r['dbstat_available']);self.assertEqual(r['physical_objects'],{})
        self.assertIsNone(r['dbstat_attributed_bytes']);self.assertIsNone(r['unattributed_nonfreelist_page_bytes'])
        self.assertTrue(r['logical_columns']);self.assertGreater(r['sqlite_page_bytes'],0)
    def test_real_dbstat_error_not_suppressed_as_unavailable(self):
        with patch.object(subject,'_physical_stats',side_effect=sqlite3.OperationalError('database disk image is malformed')):
            with self.assertRaises(sqlite3.OperationalError):subject.audit_storage(self.db)
    def test_json_functions_unavailable_keeps_field_breakdown_unknown(self):
        with patch.object(subject,'_field_stats',side_effect=sqlite3.OperationalError('no such function: json_valid')):
            r=subject.audit_storage(self.db)
        self.assertFalse(r['field_pool_json_functions_available']);self.assertEqual(r['field_pool_distribution'],[])
        self.assertTrue(r['dbstat_available'])
    def test_sidecars_and_symlinks_rejected(self):
        for suffix in ('-wal','-journal','-shm'):
            side=Path(str(self.db)+suffix);side.write_bytes(b'')
            try:
                with self.assertRaises(ValueError):subject.audit_storage(self.db)
            finally:side.unlink()
        link=self.root/'alias.sqlite';link.symlink_to(self.db)
        with self.assertRaises(ValueError):subject.audit_storage(link)
    def test_source_mutation_during_audit_invalidates_result(self):
        physical=subject._physical_stats
        def mutate(connection):
            rows=physical(connection)
            with self.db.open('ab') as stream:stream.write(b'changed after page read')
            return rows
        with patch.object(subject,'_physical_stats',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'changed during'):subject.audit_storage(self.db)

    def test_cli_exclusive_output_never_overwrites_input_or_report(self):
        script=Path(subject.__file__);before=self.db.read_bytes();output=self.root/'report.json'
        def run(path):return subprocess.run([sys.executable,str(script),'--sqlite',str(self.db),'--output',str(path)],capture_output=True,text=True)
        self.assertEqual(run(output).returncode,0);saved=output.read_bytes()
        self.assertNotEqual(run(output).returncode,0);self.assertEqual(output.read_bytes(),saved)
        self.assertNotEqual(run(self.db).returncode,0);self.assertEqual(self.db.read_bytes(),before)
    def test_connection_is_readonly_query_only_and_never_loads_extensions(self):
        real_connect=sqlite3.connect;seen=[]
        class Guard:
            def __init__(self,conn):self.conn=conn
            def execute(self,sql,*args):
                seen.append(sql)
                if sql=='BEGIN':
                    self.assert_query_only()
                    try:self.conn.execute('CREATE TABLE SHOULD_NOT_EXIST(x)')
                    except sqlite3.OperationalError:pass
                    else:raise AssertionError('write succeeded')
                return self.conn.execute(sql,*args)
            def assert_query_only(self):
                assert self.conn.execute('PRAGMA query_only').fetchone()[0]==1
            def enable_load_extension(self,*args):raise AssertionError('extension load enabled')
            def load_extension(self,*args):raise AssertionError('extension loaded')
            def rollback(self):return self.conn.rollback()
            def close(self):return self.conn.close()
        def connect(database,*args,**kwargs):
            self.assertIn('?mode=ro',database);self.assertTrue(kwargs['uri']);return Guard(real_connect(database,*args,**kwargs))
        with patch.object(subject.sqlite3,'connect',side_effect=connect):subject.audit_storage(self.db)
        self.assertFalse(any('load_extension' in q.lower()for q in seen))

if __name__=='__main__':unittest.main()
